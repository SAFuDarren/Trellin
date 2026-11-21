import { expect, test } from "@playwright/test";
import type { Conversation, ConversationAnchor } from "../src/api/conversations";
import type { ModelConnection } from "../src/api/modelConnections";
import { openProof } from "./project-map";

for (const surface of ["Workbench", "Chats"] as const) {
  test(`${surface} adds a specific model without losing the current conversation draft`, async ({ page, request }) => {
    await request.post("/__reset", { data: {} });
    const defaultConnection: ModelConnection = {
      id: "default-connection",
      provider: "codex-cli",
      model: "native-default",
      label: "Codex",
      fingerprint: "a".repeat(64),
      status: "connected",
    };
    const specificConnection: ModelConnection = {
      ...defaultConnection,
      id: "specific-connection",
      model: "gpt-fixture",
      fingerprint: "b".repeat(64),
      status: "untested",
    };
    const date = "2026-11-20T02:00:00Z";
    const connections = [defaultConnection];
    const adds: unknown[] = [];
    const sends: unknown[] = [];
    let thread: Conversation | undefined;

    await page.route("**/api/project-intake/models", (route) => route.fulfill({ json: { models: [] } }));
    await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections } }));
    await page.route("**/api/model-connections/native", (route) =>
      route.fulfill({
        json: {
          providers: [
            { provider: "codex-cli", status: "signed_in", models: [{ id: "gpt-fixture", label: "Fixture GPT" }] },
          ],
        },
      }),
    );
    await page.route("**/api/model-connections/add-native", (route) => {
      adds.push(route.request().postDataJSON());
      connections.push(specificConnection);
      return route.fulfill({ json: { connection: specificConnection } });
    });
    await page.route(/\/api\/conversations(?:[/?]|$)/, (route) => {
      switch (new URL(route.request().url()).pathname) {
        case "/api/conversations/create": {
          const body = route.request().postDataJSON() as { title: string } & ConversationAnchor;
          thread = {
            id: "my-thread",
            title: body.title,
            claim_id: body.claim_id ?? null,
            statement_id: body.statement_id ?? null,
            node_id: body.node_id ?? null,
            created_at: date,
            updated_at: date,
            running: false,
            revision: 0,
            messages: [],
          };
          return route.fulfill({ json: { thread } });
        }
        case "/api/conversations/thread":
          return route.fulfill({ json: { thread } });
        case "/api/conversations/send":
          sends.push(route.request().postDataJSON());
          return route.fulfill({ json: { thread } });
        default:
          return route.fulfill({ json: { threads: thread ? [thread] : [] } });
      }
    });

    await page.setViewportSize({ width: 820, height: 720 });
    await page.goto("/#session=fixture");
    if (surface === "Workbench") {
      await openProof(page);
      await page.getByRole("button", { name: /^Proof obligation 2/ }).click();
    } else {
      await page
        .locator("nav.rail")
        .getByRole("link", { name: /^Chats/ })
        .click();
    }
    await page.getByRole("button", { name: "New conversation" }).click();
    const panel = page.locator(".conversation-panel");
    const draft = panel.getByRole("textbox", { name: "Write a message" });
    const picker = panel.getByRole("button", { name: "Conversation model" });
    const menu = panel.getByRole("menu", { name: "Conversation model" });
    await picker.press("ArrowDown");
    await menu.getByRole("menuitemradio", { name: /Codex · Default model/ }).click();
    const text = "Keep this draft while I choose another model.";
    await draft.fill(text);
    const anchor = thread?.node_id;
    expect(anchor).toBe(surface === "Workbench" ? "obligation:55555555-5555-4555-8555-555555555555:2" : null);
    const url = page.url();

    const manage = menu.getByRole("menuitem", { name: "Manage model connections" });
    const dialog = page.getByRole("dialog", { name: "Model connections" });
    await picker.press("ArrowDown");
    await page.keyboard.press("End");
    await expect(manage).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(picker).toBeFocused();
    await expect(draft).toHaveValue(text);

    await picker.press("ArrowDown");
    await manage.click();
    await dialog.getByLabel("Codex model").selectOption("gpt-fixture");
    await dialog.getByRole("button", { name: "Connect Codex", exact: true }).click();
    await expect(dialog.getByRole("cell", { name: "gpt-fixture", exact: true })).toBeVisible();
    expect(adds).toEqual([{ provider: "codex-cli", model: "gpt-fixture" }]);
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(picker).toBeFocused();
    await expect(page).toHaveURL(url);
    await expect(draft).toHaveValue(text);

    await picker.press("ArrowDown");
    await expect(menu.getByRole("menuitemradio", { name: /Codex · Default model/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await menu.getByRole("menuitemradio", { name: /Codex · gpt-fixture/ }).click();
    await expect(picker).toHaveText(/Codex · gpt-fixture/);
    await expect(draft).toHaveValue(text);
    expect(sends).toHaveLength(0);
  });
}
