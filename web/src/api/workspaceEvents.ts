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
type WorkspaceEvent = { id: string; reviewedClaim?: string };

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
  if (schema_version !== 1 || event.type !== "evidence.reviewed") return { id: frame.id };
  if (
    actor.kind !== "user" ||
    !workspaceId(actor.id) ||
    subject.kind !== "evidence" ||
    !workspaceId(subject.id) ||
    !workspaceId(data.claim_id) ||
    !workspaceId(data.decision_id) ||
    (data.action !== "approve" && data.action !== "return")
  ) {
    return null;
  }
  return { id: frame.id, reviewedClaim: data.claim_id };
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
