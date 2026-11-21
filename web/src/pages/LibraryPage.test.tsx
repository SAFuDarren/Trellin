import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "../i18n";
import en from "../i18n/en.json";
import { useRoute } from "../shell/route";
import { LibraryPage } from "./LibraryPage";

const OLD_ID = "f966ca30-261d-4acf-93b6-3566b8a67610";
const NEW_ID = "d037c5d4-37ee-489b-a0c2-1cb29330bd69";
const FULL = "An exact statement, with its complete wording.";
const OLD = {
  id: OLD_ID,
  logical_key: "some-key",
  version: 1,
  origin: "packet",
  admitted: true,
  retired: false,
  needs_recheck: false,
  kind: "goal",
  natural: "An exact statement, with its complete …",
  lean: "True",
  formula: null,
  declaration: null,
  used_by: [],
  created_at: "2026-11-20T01:00:00Z",
};
const BASE = {
  ...OLD,
  natural: FULL,
  available: true,
  mode: "goal",
  sha256: "current",
};

function Page() {
  const [route] = useRoute();
  return <LibraryPage session="session" sub={route.sub} onOpen={() => {}} />;
}

function openLibrary() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "#library");
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("loads the exact version at either entry point, discards unsaved text, and synchronizes the confirmed revision", async () => {
  const changes = { id: OLD_ID, sha256: "current", natural: "Updated statement.", lean: "True ∧ True" };
  const calls = vi.fn();
  let saved = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      switch (url) {
        case "/api/library":
          return Response.json({
            items: saved ? [{ ...OLD, ...changes, id: NEW_ID, statement_revision: 2 }, OLD] : [OLD],
          });
        case `/api/library/item?version=${OLD_ID}`:
          return Response.json(BASE);
        case "/api/library/preview":
          calls("preview", JSON.parse(String(init.body)));
          return Response.json({
            base: BASE,
            after: { natural: changes.natural, lean: changes.lean },
            affected: { claims: 1, checks: 1, receipts: 1 },
            token: "reviewed",
          });
        case "/api/library/revise":
          calls("revise", JSON.parse(String(init.body)));
          saved = true;
          return Response.json({ id: NEW_ID, version: 2 });
      }
      return Response.json({}, { status: 404 });
    }),
  );
  openLibrary();

  const editInRow = await screen.findByRole("button", { name: /^Edit statement for / });
  fireEvent.click(editInRow);
  const dialog = await screen.findByRole("dialog", { name: en.libraryEdit.title });
  const wording = await within(dialog).findByLabelText(en.detail.statement);
  expect((wording as HTMLTextAreaElement).value).toBe(FULL);
  fireEvent.change(wording, { target: { value: "Discarded." } });
  fireEvent.click(within(dialog).getByRole("button", { name: en.libraryEdit.discard }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(calls).not.toHaveBeenCalled();

  const inspector = screen.getByRole("complementary");
  fireEvent.click(within(inspector).getByRole("button", { name: en.libraryEdit.edit }));
  const editor = await screen.findByRole("dialog");
  const statement = await within(editor).findByLabelText(en.detail.statement);
  expect((statement as HTMLTextAreaElement).value).toBe(FULL);
  fireEvent.change(statement, { target: { value: changes.natural } });
  fireEvent.change(within(editor).getByLabelText(en.detail.lean), { target: { value: changes.lean } });
  fireEvent.click(within(editor).getByRole("button", { name: en.libraryEdit.review }));
  expect(await within(editor).findByText(en.libraryEdit.before)).toBeTruthy();
  expect(within(editor).getByText(en.libraryEdit.after)).toBeTruthy();
  expect(within(editor).getByText(FULL)).toBeTruthy();
  expect(calls.mock.calls).toEqual([["preview", changes]]);
  fireEvent.change(within(editor).getByLabelText(en.libraryEdit.reason), { target: { value: "Correct the scope." } });
  fireEvent.click(within(editor).getByRole("button", { name: en.libraryEdit.confirm }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(calls.mock.calls[1]).toEqual(["revise", { ...changes, token: "reviewed", reason: "Correct the scope." }]);
  await waitFor(() => {
    const newInspector = screen.getByRole("complementary", { name: changes.natural });
    expect(within(newInspector).getByText(/^v2/)).toBeTruthy();
    const row = within(screen.getByRole("table")).getByRole("row", { name: /Updated statement/ });
    expect(row.getAttribute("aria-selected")).toBe("true");
    expect(within(row).getByText("v2")).toBeTruthy();
  });
});
