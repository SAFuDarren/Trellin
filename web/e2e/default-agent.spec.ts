import { expect, test, type Page } from "@playwright/test";
import type { ConversationSummary } from "../src/api/conversations";
import type { TeamAgent } from "../src/api/teamAgents";

// #328: Chats and the floating Agent chat start with an Agent whenever one is saved.
const connection = {
  id: "9a33cb02-fa5c-40f4-bc2e-591c368e754e",
  provider: "codex-cli",
  model: "gpt-5-codex",
  label: "Research Codex",
  status: "connected",
  fingerprint: "a".repeat(64),
};

function agent(id: string, name: string): TeamAgent {
  return {
    id,
    revision: 1,
    connection_id: connection.id,
    name,
    description: "",
    avatar: "book",
    instructions: "",
    starters: [],
  };
}

const ada = agent("11111111-aaaa-4aaa-8aaa-111111111111", "Ada");
const basil = agent("22222222-bbbb-4bbb-8bbb-222222222222", "Basil");

function thread(id: string, title: string, agentId: string, updated: string): ConversationSummary {
  return {
    id,
    title,
    agent_id: agentId,
    claim_id: null,
    statement_id: null,
    node_id: null,
    created_at: updated,
    updated_at: updated,
    running: false,
    revision: 1,
    latest_turn: null,
  };
}

async function library(page: Page, agents: TeamAgent[], threads: ConversationSummary[] = []) {
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [connection] } }));
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents } }));
  await page.route(/\/api\/conversations(?:[/?]|$)/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/thread")) {
      const found = threads.find(({ id }) => id === url.searchParams.get("thread_id"));
      return route.fulfill({ json: { thread: found && { ...found, messages: [] } } });
    }
    return route.fulfill({ json: { threads } });
  });
}

async function openChats(page: Page) {
  await page.goto("/#session=fixture");
  await page
    .locator("nav.rail")
    .getByRole("link", { name: /^Chats/ })
    .click();
}

const dock = (page: Page) => page.getByRole("region", { name: "Agent chat", exact: true });

async function openDock(page: Page) {
  await page.goto("/#session=fixture");
  await page.getByRole("button", { name: "Agent chat", exact: true }).click();
  await expect(dock(page)).toBeVisible();
}

test("with no saved Agent, Chats stays on its templates and the panel asks for an Agent", async ({ page }) => {
  await library(page, []);
  await openChats(page);
  await expect(page.locator(".section-nav").getByRole("region", { name: "Add your first teammate" })).toBeVisible();
  await expect(page).toHaveURL(/#chat$/);
  await openDock(page);
  await expect(dock(page).getByRole("textbox", { name: "Write a message" })).toBeVisible();
});

test("with one saved Agent, Chats and the panel open on it", async ({ page }) => {
  await library(page, [ada]);
  await openChats(page);
  await expect(page).toHaveURL(new RegExp(`#chat/employee%2F${ada.id}$`));
  await expect(page.getByRole("textbox", { name: "Message Ada" })).toBeVisible();
  await openDock(page);
  await expect(dock(page).getByRole("textbox", { name: "Message Ada" })).toBeVisible();
});

test("with two saved Agents and no conversation, Chats and the panel start with the first Agent", async ({ page }) => {
  await library(page, [basil, ada]);
  await openChats(page);
  await expect(page).toHaveURL(new RegExp(`#chat/employee%2F${ada.id}$`));
  await expect(page.getByRole("textbox", { name: "Message Ada" })).toBeVisible();
  await openDock(page);
  await expect(dock(page).getByRole("textbox", { name: "Message Ada" })).toBeVisible();
});

test("with two saved Agents, Chats reopens the latest Agent conversation and the panel preselects its Agent", async ({
  page,
}) => {
  const older = thread("aa000000-0000-4000-8000-000000000001", "Older question", ada.id, "2026-11-20T08:00:00Z");
  const latest = thread("aa000000-0000-4000-8000-000000000002", "Latest question", basil.id, "2026-11-21T08:00:00Z");
  await library(page, [ada, basil], [older, latest]);
  await openChats(page);
  await expect(page).toHaveURL(new RegExp(`#chat/${latest.id}$`));
  await openDock(page);
  await dock(page).getByRole("button", { name: "New conversation", exact: true }).click();
  await expect(dock(page).getByRole("textbox", { name: "Message Basil" })).toBeVisible();
});
