import { expect, test, type Page } from "@playwright/test";
import type { AgentWorkTask } from "../src/api/agentWork";
import { openDestination } from "./navigation";
import { startOnStatusBoard } from "./claims-display";

const CLAIM = "自然数加零的对称性";
const COUNT = "1 of 2 obligations verified";
const REVIEW = "1 result to review";
const DATE = "2026-08-04T12:00:00Z";
const delivered: AgentWorkTask = {
  id: "11000000-0000-4000-8000-000000000040",
  title: "Check the recorded proof with an independently written lemma",
  agent_name: "Proof reviewer",
  mode: "review",
  status: "delivered",
  created_at: DATE,
  updated_at: DATE,
  delivery_sha256: "a".repeat(64),
  predecessor_id: null,
  error: null,
  delivery: { kind: "review", summary: "The lemma was checked.", body: "Verified independent result.", checks: [] },
};
const queued: AgentWorkTask = {
  ...delivered,
  id: "11000000-0000-4000-8000-000000000041",
  title: "Draft the next research step",
  status: "queued",
  mode: "draft",
  delivery_sha256: null,
  delivery: null,
  agent_name: "Proof writer",
};

const display = (page: Page) => page.getByRole("button", { name: "Display", exact: true });

async function layout(page: Page, name: string) {
  await display(page).click();
  await page.getByRole("menuitemradio", { name, exact: true }).click();
  await expect(display(page)).toBeFocused();
}

async function section(page: Page, name: "Claims" | "Agent work") {
  await openDestination(page, name === "Agent work" ? "Agent tasks" : name);
}

test("Claims preserves progress, review links and previews in Board, List and Table", async ({ page }) => {
  await startOnStatusBoard(page);
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  await section(page, "Claims");

  const card = page.locator(".board article").filter({ hasText: CLAIM });
  await expect(card.getByText(COUNT, { exact: true })).toBeVisible();
  await expect(card.getByRole("link", { name: REVIEW })).toBeVisible();
  await card.getByRole("button", { name: new RegExp(CLAIM) }).click();
  await expect(page.getByRole("button", { name: "Open in Workbench" })).toBeVisible();

  await display(page).press("ArrowDown");
  const menu = page.getByRole("menu", { name: "Display" });
  await expect(menu.getByRole("menuitemradio", { name: "Status board" })).toHaveAttribute("aria-checked", "true");
  // The menu opens on its first layout, By domain; Status board and then List follow.
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitemradio", { name: "Status board" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitemradio", { name: "List" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(display(page)).toBeFocused();
  const list = page.getByRole("list", { name: "All claims" });
  await expect(list.getByText(COUNT, { exact: true })).toBeVisible();
  await expect(list.getByRole("link", { name: REVIEW })).toBeVisible();
  await expect(list.getByRole("button", { name: new RegExp(CLAIM) })).toHaveAttribute("aria-expanded", "true");

  await layout(page, "Table");
  const table = page.getByRole("table", { name: "All claims" });
  await expect(table.getByRole("columnheader", { name: "Proof progress" })).toBeVisible();
  const row = table.getByRole("row", { name: new RegExp(CLAIM) });
  await expect(row.getByText(COUNT, { exact: true })).toBeInViewport({ ratio: 1 });
  await expect(row.getByRole("link", { name: REVIEW })).toBeVisible();
  await expect(row.getByRole("button", { name: CLAIM, exact: true })).toHaveAttribute("aria-expanded", "true");
  const preview = table.getByRole("button", { name: "Open in Workbench" });
  await expect(preview).toBeVisible();
  await preview.click();
  await expect(page.locator("#node-detail").getByRole("heading", { name: CLAIM })).toBeVisible();

  await section(page, "Claims");
  await expect(table).toBeVisible();
  await page.locator(".section-nav").getByRole("link", { name: /^flow/ }).click();
  const filtered = page.getByRole("table", { name: "flow" });
  await expect(filtered.getByRole("rowheader")).toHaveCount(1);
  await expect(filtered.getByRole("rowheader")).toHaveText("固定网络最大流");
  await page.reload();
  await expect(filtered).toBeVisible();
  await layout(page, "Status board");
  await expect(page.locator(".board article")).toHaveCount(1);
});

test("Agent work remembers its layout separately and opens the same handoff record from every layout", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.route(/\/api\/agent-work(?:\?task=[^/]+)?$/, (route) => {
    const task = new URL(route.request().url()).searchParams.get("task");
    return route.fulfill({ json: task ? { task: delivered } : { tasks: [queued, delivered], available: true } });
  });
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && new URL(request.url()).pathname.startsWith("/api/agent-work/")) {
      writes.push(request.method());
    }
  });
  await page.goto("/#session=fixture");
  await section(page, "Agent work");
  const tasks = page.getByRole("list", { name: "All tasks" });
  await expect(tasks.getByRole("link", { name: new RegExp(delivered.title) })).toBeVisible();
  await expect(tasks.getByText(/Proof writer/)).toBeVisible();

  await layout(page, "Board");
  const board = page.locator(".board");
  await expect(board.getByRole("region", { name: "Queued" }).getByRole("link")).toContainText(queued.title);
  await expect(board.getByRole("region", { name: "Awaiting review" }).getByRole("link")).toContainText(delivered.title);
  await board.getByRole("link", { name: new RegExp(delivered.title) }).click();
  await expect(page.getByText("The lemma was checked.")).toBeVisible();
  await expect(display(page)).toHaveCount(0);
  // #289: the space entry "Agent tasks" is the full list; the page no longer repeats it as "All tasks".
  const all = page.locator(".section-nav").getByRole("link", { name: /^Agent tasks/ });
  await all.click();

  await layout(page, "Table");
  const table = page.getByRole("table", { name: "All tasks" });
  await expect(table.getByRole("columnheader", { name: "Last updated" })).toBeVisible();
  const delivery = table.getByRole("row", { name: new RegExp(delivered.title) });
  await expect(delivery.getByRole("cell", { name: delivered.agent_name })).toBeVisible();
  await expect(delivery.getByRole("cell", { name: "Awaiting review" })).toBeVisible();
  await page
    .locator(".section-nav")
    .getByRole("link", { name: /^Awaiting review/ })
    .click();
  const waiting = page.getByRole("table", { name: "Awaiting review" });
  await expect(waiting.getByRole("rowheader")).toHaveCount(1);
  await waiting.getByRole("link", { name: delivered.title }).press("Enter");
  await expect(page.getByRole("heading", { name: delivered.title })).toBeVisible();
  await expect(page.getByRole("button", { name: "Accept delivery" })).toBeDisabled();

  // #326: Claims keeps its own layout, which starts by domain.
  await section(page, "Claims");
  await expect(page.locator(".research-lanes").getByRole("button", { name: new RegExp(CLAIM) })).toBeVisible();
  await section(page, "Agent work");
  await expect(table).toBeVisible();
  await page.reload();
  await expect(table).toBeVisible();
  await layout(page, "List");
  await tasks.getByRole("link", { name: new RegExp(delivered.title) }).click();
  await expect(page.getByText("The lemma was checked.")).toBeVisible();
  expect(writes).toEqual([]);
});
