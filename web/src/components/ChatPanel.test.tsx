import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "../i18n";
import en from "../i18n/en.json";
import { buildGraph } from "../workbench/graph";
import type { GuidanceNote } from "../workbench/types";
import { ChatPanel } from "./ChatPanel";

afterEach(() => vi.unstubAllGlobals());

const claim = "11111111-1111-4111-8111-111111111111";
const statement = "22222222-2222-4222-8222-222222222222";
const node = "goal:" + claim;
const other = "lemma:33333333-3333-4333-8333-333333333333";
const graph = buildGraph(i18n.t, {
  claim: { id: claim, title: "Synthetic claim", status: "open", domain: "lean-core", statement_id: statement },
  nodes: [
    { id: node, kind: "goal" },
    { id: other, kind: "lemma" },
  ],
  edges: [],
});
const draftPath = "/api/proof-drafts";
const oldRevision = "44444444-4444-4444-8444-444444444444";
const newRevision = "55555555-5555-4555-8555-555555555555";
const updatedAt = "2026-11-20T09:00:00Z";

const panel = (
  selected: string,
  available = false,
  blocker = "public_basis",
  guidance: GuidanceNote[] = [],
  draftAvailable?: boolean,
  proofSelected: string | null = selected,
) => (
  <ChatPanel
    session="test"
    graph={graph}
    claimId={claim}
    selected={selected}
    proofSelected={proofSelected}
    activity={{
      claim_id: claim,
      activity: [],
      guidance,
      guidance_channels: available ? ["codex"] : [],
      guidance_blocker: available ? null : blocker,
      proof_draft_available: draftAvailable,
    }}
    activityError=""
    scopeReady={false}
    running={0}
    queued={0}
    open
    allNodes={false}
    onToggle={vi.fn()}
    onAllNodes={vi.fn()}
    onSelect={vi.fn()}
    onSent={vi.fn()}
  />
);

const opinion = () => screen.getByRole<HTMLTextAreaElement>("textbox", { name: en.composer.label });
const openProof = () => fireEvent.click(screen.getByRole("button", { name: en.conversations.proofActivity }));
const button = (name: string) => screen.getByRole<HTMLButtonElement>("button", { name });

it("keeps a blocked proof opinion on its exact node until a later explicit release", async () => {
  const fetch = vi.fn<typeof window.fetch>(async () => Response.json({}));
  vi.stubGlobal("fetch", fetch);
  const { rerender } = render(panel(node));
  openProof();
  const note = "First check the original premise.";
  expect(screen.getByText(en.composer.blockers.public_basis)).toBeTruthy();
  expect(screen.getByText(en.composer.draftOnly)).toBeTruthy();
  fireEvent.change(opinion(), { target: { value: note } });
  expect(opinion().value).toBe(note);
  expect(screen.queryByRole("button", { name: en.composer.send })).toBeNull();
  expect(screen.queryByRole("combobox", { name: en.composer.audience })).toBeNull();
  expect(screen.queryByRole("button", { name: en.composer.upgrade })).toBeNull();
  const postMessage = vi.fn();
  vi.stubGlobal("webkit", { messageHandlers: { trellinUpgradeProject: { postMessage } } });
  rerender(panel(node, false, "connected_schema"));
  expect(screen.getByText(en.composer.blockers.connected_schema)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: en.composer.upgrade }));
  expect(postMessage).toHaveBeenCalledExactlyOnceWith("confirm");
  expect(fetch).not.toHaveBeenCalled();
  expect(opinion().value).toBe(note);
  rerender(panel(other));
  expect(opinion().value).toBe("");
  rerender(panel(node, true));
  expect(opinion().value).toBe(note);
  fireEvent.click(screen.getByRole("button", { name: en.composer.send }));
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: en.composer.confirm }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  const [url, options] = fetch.mock.calls[0]!;
  expect(url).toBe("/api/guidance");
  expect(JSON.parse(String(options?.body))).toMatchObject({
    claim_id: claim,
    statement_id: statement,
    node_id: node,
    text: note,
    audience: "codex",
    confirm_release: true,
  });

  const receipt = {
    id: "saved-note",
    node_id: node,
    text: note,
    created_at: "2026-11-20T09:00:00Z",
    audience: "codex",
    current_scope: true,
  };
  const noteStatus = () => {
    const history = within(screen.getByRole("list", { name: en.chat.feed }));
    return within(history.getByText(note).closest("li")!).getByRole("status").textContent;
  };
  rerender(panel(node, true, "public_basis", [receipt]));
  expect(noteStatus()).toContain(en.chat.waitingCodex);
  expect(noteStatus()).toContain(en.chat.guidanceNextCodex);
  expect(noteStatus()).not.toContain(en.chat.submittedCodex);

  rerender(panel(node, false, "public_basis", [receipt]));
  expect(noteStatus()).toContain(en.chat.blockedGuidance);
  expect(noteStatus()).toContain(en.composer.blockers.public_basis);
  expect(noteStatus()).not.toContain(en.chat.guidanceNextCodex);

  const oldScope = { ...receipt, current_scope: false };
  rerender(panel(node, true, "public_basis", [oldScope]));
  expect(noteStatus()).toContain(en.chat.staleGuidance);
  expect(noteStatus()).toContain(en.chat.staleGuidanceNext);

  rerender(panel(node, false, "public_basis", [{ ...oldScope, submitted_at: "2026-11-20T09:05:00Z" }]));
  expect(noteStatus()).toContain(en.chat.submittedCodex);
  expect(noteStatus()).not.toContain(en.chat.staleGuidance);
  expect(noteStatus()).not.toContain(en.chat.blockedGuidance);
});

it("restores a saved opinion for the exact proof node and consumes it only with a confirmed queue receipt", async () => {
  const note = "  First check the original premise.  ";
  let saved: { revision: string; text: string; updated_at: string } | null = null;
  let finishSave: (() => void) | undefined;
  const fetch = vi.fn<typeof window.fetch>(async (url, init) => {
    if (String(url).startsWith(draftPath + "?")) {
      const params = new URL(String(url), "http://localhost").searchParams;
      expect(params.get("claim_id")).toBe(claim);
      return Response.json({ draft: params.get("node_id") === node ? saved : null });
    }
    const body = JSON.parse(String(init?.body));
    if (url === draftPath) {
      expect(body).toEqual({ claim_id: claim, node_id: node, expected_revision: null, text: note });
      await new Promise<void>((resolve) => {
        finishSave = resolve;
      });
      saved = { revision: oldRevision, text: note, updated_at: updatedAt };
      return Response.json({ draft: saved });
    }
    expect(url).toBe("/api/guidance");
    expect(body).toMatchObject({
      claim_id: claim,
      statement_id: statement,
      node_id: node,
      audience: "codex",
      confirm_release: true,
      draft_revision: oldRevision,
      text: note,
    });
    saved = null;
    return Response.json({ draft_consumed: true });
  });
  vi.stubGlobal("fetch", fetch);

  const first = render(panel(node, false, "scope", [], true));
  openProof();
  await waitFor(() => expect(opinion().disabled).toBe(false));
  fireEvent.change(opinion(), { target: { value: note } });
  fireEvent.click(button(en.composer.draft.save));
  expect(screen.getByText(en.composer.draft.saving)).toBeTruthy();
  expect(screen.queryByText(en.composer.draft.saved)).toBeNull();
  expect(screen.getByText(en.composer.blockers.scope)).toBeTruthy();

  first.rerender(panel(other, false, "scope", [], true));
  await waitFor(() => expect(opinion().disabled).toBe(false));
  expect(opinion().value).toBe("");
  finishSave!();
  await waitFor(() => expect(saved?.text).toBe(note));
  first.rerender(panel(node, false, "scope", [], true));
  await screen.findByText(en.composer.draft.saved);
  expect(opinion().value).toBe(note);
  expect(within(screen.getByRole("list", { name: en.chat.feed })).queryByText(note.trim())).toBeNull();
  expect(fetch.mock.calls.some(([url]) => url === "/api/guidance")).toBe(false);
  first.unmount();

  render(panel(node, true, "scope", [], true));
  openProof();
  await screen.findByText(en.composer.draft.saved);
  expect(opinion().value).toBe(note);
  fireEvent.click(button(en.composer.send));
  expect(fetch.mock.calls.some(([url]) => url === "/api/guidance")).toBe(false);
  fireEvent.click(button(en.composer.confirm));
  await screen.findByText(en.composer.sentCodex);
  expect(opinion().value).toBe("");
  expect(screen.queryByText(en.composer.draft.saved)).toBeNull();
  expect(fetch.mock.calls.filter(([url]) => url === "/api/guidance")).toHaveLength(1);
});

it("preserves edits after a draft save error and requires an explicit reload after a revision conflict", async () => {
  const first = { revision: oldRevision, text: "Original draft", updated_at: updatedAt };
  const newer = { revision: newRevision, text: "Draft from another window", updated_at: updatedAt };
  let version = first;
  let failed = false;
  const fetch = vi.fn<typeof window.fetch>(async (url, init) => {
    if (String(url).startsWith(draftPath + "?")) return Response.json({ draft: version });
    const body = JSON.parse(String(init?.body));
    if (url === draftPath + "/discard") {
      expect(body).toEqual({ claim_id: claim, node_id: node, expected_revision: newRevision });
      return Response.json({ draft: null });
    }
    expect(url).toBe(draftPath);
    expect(body).toEqual({
      claim_id: claim,
      node_id: node,
      expected_revision: oldRevision,
      text: "Local unsaved edits",
    });
    if (!failed) {
      failed = true;
      return Response.json({ error: "test failure" }, { status: 500 });
    }
    version = newer;
    return Response.json({ error: "proof_draft_conflict" }, { status: 409 });
  });
  vi.stubGlobal("fetch", fetch);
  render(panel(node, false, "public_basis", [], true));
  openProof();
  await screen.findByText(en.composer.draft.saved);
  fireEvent.change(opinion(), { target: { value: "Local unsaved edits" } });
  fireEvent.click(button(en.composer.draft.save));
  await screen.findByText(en.composer.draft.saveFailed);
  expect(opinion().value).toBe("Local unsaved edits");
  fireEvent.click(button(en.composer.draft.save));
  await screen.findByText(en.composer.draft.conflict);
  expect(opinion().value).toBe("Local unsaved edits");
  expect(button(en.composer.draft.save).disabled).toBe(true);

  fireEvent.click(button(en.composer.draft.reload));
  await screen.findByText(en.composer.draft.saved);
  expect(opinion().value).toBe(newer.text);
  fireEvent.change(opinion(), { target: { value: "Keep this local edit" } });
  fireEvent.click(button(en.composer.draft.discard));
  await screen.findByText(en.composer.draft.discardedEdits);
  expect(opinion().value).toBe("Keep this local edit");
  expect(fetch.mock.calls.some(([url]) => url === "/api/guidance")).toBe(false);
});

it("does not expose the conversation's fallback scope as an editable proof node", () => {
  const fetch = vi.fn<typeof window.fetch>();
  vi.stubGlobal("fetch", fetch);
  render(panel(node, true, "scope", [], true, null));
  openProof();
  expect(screen.getByText(en.composer.chooseNode)).toBeTruthy();
  expect(opinion().disabled).toBe(true);
  expect(screen.queryByRole("button", { name: en.composer.draft.save })).toBeNull();
  expect(screen.queryByRole("button", { name: en.composer.send })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("shows the newly saved version when an explicit queue leaves a concurrent draft untouched", async () => {
  const first = { revision: oldRevision, text: "Draft selected to queue", updated_at: updatedAt };
  const newer = { revision: newRevision, text: "Concurrent edit kept on this node", updated_at: updatedAt };
  let version = first;
  const fetch = vi.fn<typeof window.fetch>(async (url, init) => {
    if (String(url).startsWith(draftPath + "?")) return Response.json({ draft: version });
    expect(url).toBe("/api/guidance");
    expect(JSON.parse(String(init?.body))).toMatchObject({ draft_revision: oldRevision, text: first.text });
    version = newer;
    return Response.json({ draft_consumed: false });
  });
  vi.stubGlobal("fetch", fetch);
  render(panel(node, true, "scope", [], true));
  openProof();
  await screen.findByText(en.composer.draft.saved);
  fireEvent.click(button(en.composer.send));
  fireEvent.click(button(en.composer.confirm));
  await screen.findByText(en.composer.sentCodex);
  await screen.findByText(en.composer.draft.saved);
  expect(opinion().value).toBe(newer.text);
});
