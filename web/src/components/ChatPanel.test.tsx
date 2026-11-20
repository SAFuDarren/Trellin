import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "../i18n";
import en from "../i18n/en.json";
import { buildGraph } from "../workbench/graph";
import { ChatPanel } from "./ChatPanel";

afterEach(() => vi.unstubAllGlobals());

it("keeps a blocked proof opinion on its exact node until a later explicit release", async () => {
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
  const fetch = vi.fn<typeof window.fetch>(async () => Response.json({}));
  vi.stubGlobal("fetch", fetch);
  const panel = (selected: string, available = false, blocker = "public_basis") => (
    <ChatPanel
      session="test"
      graph={graph}
      claimId={claim}
      selected={selected}
      activity={{
        claim_id: claim,
        activity: [],
        guidance_channels: available ? ["codex"] : [],
        guidance_blocker: available ? null : blocker,
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
  const { rerender } = render(panel(node));
  fireEvent.click(screen.getByRole("button", { name: en.conversations.proofActivity }));
  const opinion = () => screen.getByRole<HTMLTextAreaElement>("textbox", { name: en.composer.label });
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
  fireEvent.click(screen.getByRole("button", { name: en.composer.confirmLabel }));
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
});
