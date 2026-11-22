import { expect, test, type Page } from "@playwright/test";
import type { AgentWorkSummary } from "../src/api/agentWork";
import type { InboxItem, Job } from "../src/api/sections";

const WORKSPACE = "11111111-2222-4333-8444-555555555555";
const TASK_ID = "00112233-1122-4122-8122-334455667788";
const TIME = "2026-11-22T00:00:00Z";
const task: AgentWorkSummary = {
  id: TASK_ID,
  title: "Delivered proof-writing task",
  agent_name: "Fixture writer",
  mode: "implement",
  status: "delivered",
  created_at: TIME,
  updated_at: TIME,
  delivery_sha256: "f".repeat(64),
  predecessor_id: null,
  error: null,
};

async function openInbox(page: Page) {
  const rail = page.locator("nav.rail");
  await rail.getByRole("link", { name: /^Agent work/ }).click();
  await page.locator('.section-nav a[href="#agent-work/inbox"]').click();
  await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Display", exact: true })).toHaveCount(0);
  return page.getByRole("region", { name: "Inbox", exact: true });
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 640, height: 460 },
]) {
  test(`the Agent work inbox routes and triages records at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/api/inbox", async (route) => {
      const response = await route.fetch();
      const inbox = await response.json();
      const items: InboxItem[] = inbox.items.filter(
        (
          item: InboxItem, // Preserve the fixture's real evidence and question IDs.
        ) => item.kind === "review_evidence" || item.kind === "resolve_clarification",
      );
      await route.fulfill({
        response,
        json: { items, review_identity: { workspace_id: WORKSPACE } },
      });
    });
    await page.route("**/api/team", async (route) => {
      const response = await route.fetch();
      const team = await response.json();
      const failed: Job = { ...team.jobs[0], id: "fixture-failed", claim_title: "Failed proof run", state: "failed" };
      await route.fulfill({ response, json: { ...team, jobs: [...team.jobs, failed] } });
    });
    await page.route("**/api/agent-work", (route) =>
      route.fulfill({
        json: {
          available: true,
          tasks: [task, { ...task, id: "queued-fixture", title: "Queued task stays in Activity", status: "queued" }],
        },
      }),
    );
    await page.goto("/#session=fixture");
    const rail = page.locator("nav.rail");
    const inboxBadge = rail.getByRole("link", { name: /^Agent work/ });
    await expect(inboxBadge).toHaveAccessibleName(/4 unread inbox items/);
    let inbox = await openInbox(page);
    await expect(inbox.locator("[data-inbox-row]")).toHaveCount(4);
    await expect(inbox.getByText("Queued task stays in Activity")).toHaveCount(0);
    await inbox.getByRole("button", { name: /Delivered proof-writing task/ }).click();
    const taskDetail = inbox.getByRole("complementary", { name: task.title });
    await expect(taskDetail).toBeVisible();
    expect(await inbox.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expect(taskDetail.getByRole("link", { name: "Open record" })).toHaveAttribute(
      "href",
      `#agent-work/${TASK_ID}`,
    );
    await inbox.getByRole("button", { name: /Failed proof run/ }).click();
    await expect(
      inbox.getByRole("complementary", { name: "Failed proof run" }).getByRole("button", { name: "Open proof" }),
    ).toBeVisible();
    await inbox.getByRole("button", { name: /单射映射的复合/ }).click();
    await expect(inbox.getByRole("complementary").getByText("Which composition order is intended?")).toBeVisible();

    const evidence = inbox.getByRole("button", { name: /reverseZeroAdd/ });
    await evidence.click();
    const evidenceDetail = inbox.getByRole("complementary", { name: "reverseZeroAdd" });
    await expect(evidenceDetail.getByRole("link", { name: "Open record" })).toHaveAttribute("href", /#review\//);
    await evidence.press("r");
    await expect(inboxBadge).toHaveAccessibleName(/3 unread inbox items/);
    await evidence.press("e");
    await expect(evidence).toHaveCount(0);
    await inbox.getByRole("button", { name: "Undo", exact: true }).press("z");
    await expect(evidence).toBeVisible();
    await expect(inboxBadge).toHaveAccessibleName(/3 unread inbox items/);
    await evidence.press("e");

    await rail.getByRole("link", { name: "Home", exact: true }).click();
    inbox = await openInbox(page);
    await expect(inbox.getByRole("button", { name: /reverseZeroAdd/ })).toHaveCount(0);
    await inbox.getByRole("button", { name: "Archived", exact: true }).click();
    await inbox.getByRole("button", { name: /reverseZeroAdd/ }).click();
    await inbox.getByRole("button", { name: "Restore", exact: true }).click();
    await expect(inbox.getByRole("status")).toContainText("There are no archived items.");
    await expect(rail.getByRole("link", { name: /^Review/ })).toHaveAccessibleName(/waiting for review/);
  });
}

test("the inbox handles a workbench without a verified workspace identity", async ({ page }) => {
  await page.goto("/#session=fixture");
  await openInbox(page);
  await expect(page.getByText("Inbox is unavailable for this project.", { exact: true })).toBeVisible();
  await expect(page.locator("nav.rail").getByRole("link", { name: "Agent work", exact: true })).toBeVisible();
});
