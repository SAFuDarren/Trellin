import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import "../../i18n";
import en from "../../i18n/en.json";
import { ProofTaskPane } from "./ProofTaskPane";

const CLAIM = "11111111-1111-4111-8111-111111111111";
const MODEL = {
  id: "22222222-2222-4222-8222-222222222222",
  provider: "claude-code",
  model: "claude-test",
  label: "My connection",
  fingerprint: "a".repeat(64),
  status: "connected",
};

function openTask(external: boolean) {
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === "/api/model-connections") return Response.json({ connections: [MODEL] });
    if (path === "/api/proof-task/start" && options?.method === "POST") return Response.json({ policy_id: "signed" });
    if (path === "/api/proof-task?claim=" + CLAIM) {
      return Response.json({
        claim_id: CLAIM,
        title: "Exact public proposition",
        state: "ready",
        blocker: null,
        can_start: true,
        can_stop: false,
        local_model_allowed: false,
        external_model_allowed: external,
        scope: { token: "scope-token", proposition: "True", basis: [], omitted_basis: 0 },
        decisions: [],
        policy: null,
        receipt: null,
        adopted: false,
      });
    }
    return Response.json({}, { status: 404 });
  });
  vi.stubGlobal("fetch", fetch);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ProofTaskPane session="local-session" claimId={CLAIM} />
    </QueryClientProvider>,
  );
  return fetch;
}

afterEach(() => vi.unstubAllGlobals());

it("passes only the selected public connection after explicit scope authorization", async () => {
  const fetch = openTask(true);
  await screen.findByRole("option", { name: MODEL.label });
  const modelPicker = screen.getByRole<HTMLSelectElement>("combobox", { name: en.proofTask.modelConnection });
  expect(modelPicker.value).toBe("");
  fireEvent.change(modelPicker, { target: { value: MODEL.id } });
  fireEvent.change(screen.getByRole("combobox", { name: en.proofTask.externalRounds }), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText(en.review.reason), { target: { value: "Approve the shown public scope." } });
  const start = screen.getByRole<HTMLButtonElement>("button", { name: en.proofTask.start });
  expect(start.disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: /Claude Code · claude-test/ }));
  fireEvent.click(start);
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/proof-task/start", expect.anything()));
  const post = fetch.mock.calls.find(([path]) => path === "/api/proof-task/start");
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({
    claim_id: CLAIM,
    token: "scope-token",
    model_rounds: 2,
    reason: "Approve the shown public scope.",
    public_model: { id: MODEL.id, fingerprint: MODEL.fingerprint },
    confirm_external: true,
  });
});

it("does not load or display external models for a private or legacy proof scope", async () => {
  const fetch = openTask(false);
  expect(await screen.findByText(en.proofTask.programOnly)).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: en.proofTask.modelConnection })).toBeNull();
  expect(fetch).not.toHaveBeenCalledWith("/api/model-connections", expect.anything());
});
