import { describe, expect, it, vi } from "vitest";
import { readWorkspaceEvents, workspaceEventCursor } from "./workspaceEvents";

const WORKSPACE = "11111111-1111-4111-8111-111111111111";
const CLAIM = "22222222-2222-4222-8222-222222222222";
const REVIEW = {
  id: "2",
  type: "evidence.reviewed",
  schema_version: 1,
  workspace_id: WORKSPACE,
  occurred_at: "2026-11-19T12:30:24.123456Z",
  actor: { kind: "user", id: "33333333-3333-4333-8333-333333333333" },
  subject: { kind: "evidence", id: "44444444-4444-4444-8444-444444444444" },
  data: { claim_id: CLAIM, decision_id: "55555555-5555-4555-8555-555555555555", action: "approve" },
};

function frame<T extends { id: string; type: string }>(event: T, id = event.id) {
  return `id: ${id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

function source(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

describe("Workspace event frames", () => {
  it("parses split UTF-8, CRLF and multiline data without treating comments as events", async () => {
    const event = JSON.stringify({ ...REVIEW, note: "审阅" }, null, 2);
    const text = [
      ": connected",
      "",
      "id: 2",
      "event: evidence.reviewed",
      ...event.split("\n").map((line) => "data: " + line),
      "",
      "",
    ].join("\r\n");
    const bytes = new TextEncoder().encode(text);
    const receive = vi.fn();
    await readWorkspaceEvents(
      source(Array.from(bytes, (byte) => Uint8Array.of(byte))),
      WORKSPACE,
      new AbortController().signal,
      receive,
    );
    expect(receive.mock.calls).toEqual([[[{ id: "2", refresh: { kind: "evidence", claimId: CLAIM } }]]]);
  });

  it("ignores foreign, malformed, mismatched and unterminated frames", async () => {
    const receive = vi.fn();
    const bytes = new TextEncoder().encode(
      frame({ ...REVIEW, workspace_id: CLAIM }) +
        frame(REVIEW, "3") +
        frame({ ...REVIEW, data: { ...REVIEW.data, claim_id: "bad" } }) +
        "id: 4\nevent: evidence.reviewed\ndata: { invalid }\n\n" +
        frame({ ...REVIEW, id: "9007199254740993" }) +
        frame({ ...REVIEW, id: "9007199254740994" }).slice(0, -1),
    );
    await readWorkspaceEvents(source([bytes]), WORKSPACE, new AbortController().signal, receive);
    expect(receive.mock.calls).toEqual([[[{ id: "9007199254740993", refresh: { kind: "evidence", claimId: CLAIM } }]]]);
  });

  it("projects valid scope changes and system activity without borrowing any status from the event", async () => {
    const scope = { claim_id: CLAIM };
    const system = { kind: "system", id: null };
    const event = (id: string, type: string, subject: { kind: string; id: string }, data: Record<string, unknown>) => ({
      ...REVIEW,
      id,
      type,
      subject,
      data,
      actor: system,
    });
    const actual = [
      event("3", "claim.frozen", { kind: "claim", id: CLAIM }, scope),
      event("4", "basis.confirmed", { kind: "formalization", id: WORKSPACE }, scope),
      event("5", "basis_item.retired", { kind: "basis_item", id: CLAIM }, {}),
      event("6", "verification_input.retired", { kind: "workspace", id: WORKSPACE }, { input_kind: "source" }),
      event("7", "condition.decided", { kind: "condition", id: CLAIM }, { claim_id: null, action: "reject" }),
      event("8", "proof_task.updated", { kind: "proof_task", id: WORKSPACE }, scope),
      event("9", "job.status_changed", { kind: "job", id: WORKSPACE }, { ...scope, status: "succeeded" }),
      event("10", "evidence.recorded", { kind: "evidence", id: WORKSPACE }, scope),
      event("11", "claim.concluded", { kind: "claim", id: WORKSPACE }, scope), // inconsistent claim
      event("12", "verification_input.retired", { kind: "workspace", id: CLAIM }, { input_kind: "source" }),
      event("13", "new_type.recorded", { kind: "other", id: CLAIM }, {}),
      event("14", "basis_item.confirmed", { kind: "basis_item", id: CLAIM }, {}),
    ];
    const receive = vi.fn();
    const bytes = new TextEncoder().encode(actual.map((item) => frame(item)).join(""));
    await readWorkspaceEvents(source([bytes]), WORKSPACE, new AbortController().signal, receive);
    expect(receive.mock.calls).toEqual([
      [
        [
          { id: "3", refresh: { kind: "claim", claimId: CLAIM } },
          { id: "4", refresh: { kind: "basis", claimId: CLAIM } },
          { id: "5", refresh: { kind: "recheck" } },
          { id: "6", refresh: { kind: "recheck" } },
          { id: "7", refresh: { kind: "library" } },
          { id: "8", refresh: { kind: "proof-result", claimId: CLAIM } },
          { id: "9", refresh: { kind: "job", claimId: CLAIM } },
          { id: "10", refresh: { kind: "evidence", claimId: CLAIM } },
          { id: "13" },
          { id: "14", refresh: { kind: "basis-item" } },
        ],
      ],
    ]);
  });

  it("accepts only canonical decimal cursors in the server's bigint range", () => {
    for (const value of [0, "", "-1", "01", "1.2", "9223372036854775808"])
      expect(workspaceEventCursor(value)).toBe(false);
    expect(workspaceEventCursor("0")).toBe(true);
    expect(workspaceEventCursor("9223372036854775807")).toBe(true);
  });

  it("cancels and releases an idle body when its workspace closes", async () => {
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({ cancel });
    const abort = new AbortController();
    const receive = vi.fn();
    const pending = readWorkspaceEvents(stream, WORKSPACE, abort.signal, receive);
    abort.abort();
    await pending;
    expect(cancel).toHaveBeenCalledOnce();
    expect(stream.locked).toBe(false);
    expect(receive).not.toHaveBeenCalled();
  });
});
