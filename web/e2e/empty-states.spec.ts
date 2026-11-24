import { expect, test, type Page } from "@playwright/test";

// #328: Inbox, Agent tasks and Automation say why a page is empty or unavailable and offer one next step.
const WORKSPACE = "11111111-2222-4333-8444-555555555555";

async function go(page: Page, hash: string, language?: "zh-CN") {
  if (language)
    await page.addInitScript((value) => localStorage.setItem("research-multiplier-language", value), language);
  await page.goto("/#session=fixture");
  await page.evaluate((target) => {
    location.hash = target;
  }, hash);
}

function emptyState(page: Page) {
  return page.locator(".empty-state");
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("an Inbox without a workspace identity explains why and opens Review", async ({ page }) => {
  await go(page, "#agent-work/inbox");
  const state = emptyState(page);
  await expect(state.getByRole("status")).toHaveText("Inbox is unavailable for this project.");
  await expect(state).toContainText("Items waiting for you are still in Review.");
  await state.getByRole("link", { name: "Open Review", exact: true }).click();
  await expect(page).toHaveURL(/#review/);
});

test("an empty Inbox view says what will appear and offers a way on", async ({ page }) => {
  await page.route("**/api/inbox", async (route) => {
    const response = await route.fetch();
    const inbox = await response.json();
    await route.fulfill({ response, json: { ...inbox, items: [], review_identity: { workspace_id: WORKSPACE } } });
  });
  await page.route(/\/api\/agent-work(?:\?|$)/, (route) => route.fulfill({ json: { tasks: [], available: true } }));
  await page.route("**/api/jobs", (route) => route.fulfill({ json: { jobs: [] } }));
  await go(page, "#agent-work/inbox");
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await inbox.getByRole("button", { name: "Archived", exact: true }).click();
  const state = emptyState(page);
  await expect(state.getByRole("status")).toHaveText("There are no archived items.");
  await expect(state).toContainText("Items you archive move here.");
  await state.getByRole("button", { name: "Show all items", exact: true }).click();
  await expect(inbox.getByRole("button", { name: "All", exact: true })).toHaveAttribute("aria-pressed", "true");
});

test("Agent tasks without tasks offers New task, and an empty waiting view returns to all tasks", async ({ page }) => {
  await page.route(/\/api\/agent-work(?:\?|$)/, (route) => route.fulfill({ json: { tasks: [], available: true } }));
  await go(page, "#agent-work");
  let state = emptyState(page);
  await expect(state.getByRole("status")).toHaveText("No agent tasks in this project yet.");
  await expect(state).toContainText("Its progress and delivery appear here.");
  await expect(state.getByRole("link", { name: "New task", exact: true })).toHaveAttribute("href", "#agent-work/new");

  await page.evaluate(() => {
    location.hash = "#agent-work/waiting";
  });
  state = emptyState(page);
  await expect(state.getByRole("status")).toHaveText("No deliveries awaiting your decision.");
  await state.getByRole("link", { name: "Show all tasks", exact: true }).click();
  await expect(page).toHaveURL(/#agent-work$/);
});

test("unavailable Agent tasks offer the desktop upgrade, or a retry in a browser", async ({ page }) => {
  let available = false;
  await page.route(/\/api\/agent-work(?:\?|$)/, (route) => route.fulfill({ json: { tasks: [], available } }));
  await go(page, "#agent-work");
  const state = emptyState(page);
  await expect(state.getByRole("status")).toHaveText("Agent tasks are not available in this project yet.");
  await expect(state).toContainText("Open the project in the Trellin app");
  available = true;
  await state.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(emptyState(page).getByRole("status")).toHaveText("No agent tasks in this project yet.");
});

test("in the app, unavailable Agent tasks start the backup and upgrade", async ({ page }) => {
  await page.addInitScript(() => {
    const sent: string[] = [];
    Object.assign(window, {
      upgradeRequests: sent,
      webkit: { messageHandlers: { trellinUpgradeProject: { postMessage: (request: string) => sent.push(request) } } },
    });
  });
  await page.route(/\/api\/agent-work(?:\?|$)/, (route) => route.fulfill({ json: { tasks: [], available: false } }));
  await go(page, "#agent-work");
  const state = emptyState(page);
  await expect(state).toContainText("Back up and upgrade it to assign work to Agents.");
  await state.getByRole("button", { name: "Back up and upgrade", exact: true }).click();
  expect(await page.evaluate(() => (window as unknown as { upgradeRequests: string[] }).upgradeRequests)).toEqual([
    "confirm",
  ]);
});

test("Automation explains an unavailable list and an empty view, in Chinese too", async ({ page }) => {
  let fail = true;
  await page.route("**/api/progress", (route) =>
    fail
      ? route.fulfill({ status: 503, json: { error: { code: "database.unavailable", message: "unavailable" } } })
      : route.fulfill({ json: { policies: [] } }),
  );
  await go(page, "#progress/stopped", "zh-CN");
  const state = emptyState(page);
  await expect(state.getByRole("status")).toHaveText("无法加载推进规则。");
  await expect(state).toContainText("请确认项目已打开");
  fail = false;
  await state.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByRole("heading", { name: "还没有自动化" })).toBeVisible();
});

test("an empty Automation view names what is missing and shows all rules", async ({ page }) => {
  const running = {
    id: "55555555-5555-4555-8555-555555555555",
    claim_id: "11111111-1111-4111-8111-111111111111",
    claim_title: "Ready claim",
    proposed_at: "2026-11-20T02:00:00Z",
    approved_by: "Trellin local researcher",
    approved_at: "2026-11-20T02:05:00Z",
    stopped_at: null,
    stop_reason: null,
    last_run_at: null,
    last_status: null,
    last_reason: null,
    state: "running",
    policy: { metric: "exact-closure", actions: ["lean"] },
    policy_sha256: "d".repeat(64),
  };
  await page.route("**/api/progress", (route) => route.fulfill({ json: { policies: [running] } }));
  await go(page, "#progress/stopped");
  const state = emptyState(page);
  await expect(state.getByRole("status")).toHaveText("No rules have been stopped.");
  await state.getByRole("link", { name: "Show all rules", exact: true }).click();
  await expect(page).toHaveURL(/#progress$/);
  await expect(page.locator(".policy-cards")).toBeVisible();
});
