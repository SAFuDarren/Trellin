const MAX_CURSOR = 9223372036854775807n;
const MAX_FRAME_LENGTH = 64 * 1024;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const EVENT_TYPE = /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/;

export function workspaceEventCursor(value: unknown): value is string {
  return typeof value === "string" && /^(?:0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= MAX_CURSOR;
}

export function workspaceId(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}

type Frame = { id: string; type: string; data: string };
export type WorkspaceRefresh =
  | { kind: "claim" | "basis" | "proof" | "proof-result" | "job" | "evidence"; claimId: string }
  | { kind: "library" | "basis-item" | "recheck" };
type WorkspaceEvent = { id: string; refresh?: WorkspaceRefresh };

const SUBJECT: Readonly<Record<string, string | undefined>> = {
  "claim.frozen": "claim",
  "claim.concluded": "claim",
  "basis.confirmed": "formalization",
  "basis_item.confirmed": "basis_item",
  "basis_item.retired": "basis_item",
  "lean_statement.confirmed": "lean_statement",
  "condition.decided": "condition",
  "verification_input.retired": "workspace",
  "proof_task.authorized": "proof_task",
  "proof_task.stopped": "proof_task",
  "proof_task.started": "proof_task",
  "proof_task.updated": "proof_task",
  "job.status_changed": "job",
  "evidence.recorded": "evidence",
  "evidence.reviewed": "evidence",
};

/** Parse a flow of text chunks, including the CRLF split across two chunks. */
function assembleFrames() {
  let tail = "";
  let skipLf = false;
  let id = "";
  let type = "";
  let lines: string[] = [];
  let length = 0;
  return (chunk: string): Frame[] => {
    if (!chunk) return [];
    const text = tail + (skipLf && chunk.startsWith("\n") ? chunk.slice(1) : chunk);
    skipLf = text.endsWith("\r");
    const frames: Frame[] = [];
    let offset = 0;
    for (const separator of text.matchAll(/\r\n|[\r\n]/g)) {
      const end = separator.index!;
      const line = text.slice(offset, end);
      offset = end + separator[0].length;
      if (!line) {
        if (lines.length) frames.push({ id, type, data: lines.join("\n") });
        id = "";
        type = "";
        lines = [];
        length = 0;
        continue;
      }
      length += line.length + 1;
      if (length > MAX_FRAME_LENGTH) throw new Error("Event frame too large.");
      const colon = line.indexOf(":");
      if (colon <= 0) continue;
      const field = line.slice(0, colon);
      const raw = line.slice(colon + 1);
      const value = raw.startsWith(" ") ? raw.slice(1) : raw;
      if (field === "id") id = value;
      if (field === "event") type = value;
      if (field === "data") lines.push(value);
    }
    tail = text.slice(offset);
    if (tail.length + length > MAX_FRAME_LENGTH) throw new Error("Event frame too large.");
    return frames;
  };
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function refreshHint(
  type: string,
  workspace: string,
  actor: Record<string, unknown>,
  subject: Record<string, unknown>,
  data: Record<string, unknown>,
): WorkspaceRefresh | null | undefined {
  const subjectKind = SUBJECT[type];
  if (!subjectKind) return undefined;
  if (
    subject.kind !== subjectKind ||
    !workspaceId(subject.id) ||
    (actor.kind === "user" && !workspaceId(actor.id)) ||
    (actor.kind === "system" && actor.id !== null)
  ) {
    return null;
  }
  if (type === "basis_item.confirmed") return { kind: "basis-item" };
  if (type === "basis_item.retired") return { kind: "recheck" };
  if (type === "verification_input.retired") {
    return subject.id === workspace && ["verifier", "environment", "source"].some((kind) => kind === data.input_kind)
      ? { kind: "recheck" }
      : null;
  }
  if (type === "condition.decided") {
    if (!["reaffirm", "reject", "revise", "derived"].some((action) => action === data.action)) return null;
    if (data.claim_id === null) return { kind: "library" };
  }
  const claimId = data.claim_id;
  if (!workspaceId(claimId)) return null;
  switch (type) {
    case "claim.frozen":
    case "claim.concluded":
      return subject.id === claimId ? { kind: "claim", claimId } : null;
    case "lean_statement.confirmed":
      return { kind: "claim", claimId };
    case "basis.confirmed":
    case "condition.decided":
      return { kind: "basis", claimId };
    case "proof_task.authorized":
    case "proof_task.stopped":
    case "proof_task.started":
      return { kind: "proof", claimId };
    case "proof_task.updated":
      return { kind: "proof-result", claimId };
    case "job.status_changed":
      return ["queued", "running", "succeeded", "failed", "cancelled"].some((state) => state === data.status)
        ? { kind: "job", claimId }
        : null;
    case "evidence.recorded":
      return { kind: "evidence", claimId };
    case "evidence.reviewed":
      if (actor.kind !== "user" || !workspaceId(data.decision_id)) return null;
      if (data.action === "return") return { kind: "recheck" };
      if (data.action === "approve") return { kind: "evidence", claimId };
      return null;
    default:
      return undefined;
  }
}

function ownedEvent(frame: Frame, workspace: string): WorkspaceEvent | null {
  if (!workspaceEventCursor(frame.id) || frame.id === "0" || !EVENT_TYPE.test(frame.type)) return null;
  let event: Record<string, unknown> | null;
  try {
    event = record(JSON.parse(frame.data));
  } catch {
    return null;
  }
  if (!event || event.workspace_id !== workspace || event.id !== frame.id || event.type !== frame.type) return null;
  const { schema_version, occurred_at } = event;
  const actor = record(event.actor);
  const subject = record(event.subject);
  const data = record(event.data);
  if (
    typeof schema_version !== "number" ||
    !Number.isSafeInteger(schema_version) ||
    schema_version < 1 ||
    typeof occurred_at !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(occurred_at) ||
    !actor ||
    !["user", "agent", "worker", "system"].some((kind) => kind === actor.kind) ||
    (actor.id != null && typeof actor.id !== "string") ||
    !subject ||
    typeof subject.kind !== "string" ||
    typeof subject.id !== "string" ||
    !data
  ) {
    return null;
  }
  if (schema_version !== 1) return { id: frame.id };
  const refresh = refreshHint(frame.type, workspace, actor, subject, data);
  if (refresh === null) return null;
  return refresh ? { id: frame.id, refresh } : { id: frame.id };
}

/** Deliver only complete, owned events. Each read gives the subscriber one refresh batch. */
export async function readWorkspaceEvents(
  body: ReadableStream<Uint8Array>,
  workspace: string,
  signal: AbortSignal,
  receive: (events: WorkspaceEvent[]) => void,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const parse = assembleFrames();
  const cancel = () => {
    void reader.cancel().catch(() => {});
  };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    if (signal.aborted) cancel();
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done || signal.aborted) return;
      const events = parse(decoder.decode(value, { stream: true }))
        .map((frame) => ownedEvent(frame, workspace))
        .filter((event): event is WorkspaceEvent => event !== null);
      if (events.length) receive(events);
    }
  } finally {
    signal.removeEventListener("abort", cancel);
    cancel();
    reader.releaseLock();
  }
}
