import { expect, test } from "@playwright/test";
import type { Team } from "../src/api/sections";
import { mockWorkspaceEvents } from "./workspace-events";
import { openDestination } from "./navigation";

const WORKSPACE = "11111111-2222-4333-8444-555555555555";
const CLAIM_ID = "11111111-1111-4111-8111-111111111111";
const CLAIM_TITLE = "自然数加零的对称性";
const EVIDENCE_ID = "77777777-7777-4777-8777-777777777777";

test("running proof jobs agree across Agent activity, Claims, Chats and the Team badge", async ({ page, request }) => {
  await request.post("/__reset", { data: {} });
  const emitEvent = await mockWorkspaceEvents(page, WORKSPACE);
  let finished = false;
  await page.route("**/api/inbox", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: {
        ...(await response.json()),
        workspace_events: { workspace_id: WORKSPACE, cursor: finished ? "2" : "1", coverage: "workspace-writes-v1" },
      },
    });
  });
  await page.route("**/api/team", async (route) => {
    const response = await route.fetch();
    const team = (await response.json()) as Team;
    if (finished) team.jobs = team.jobs.map((job) => (job.id === "j2" ? { ...job, state: "succeeded" } : job));
    await route.fulfill({ response, json: team });
  });

  // The fixture has no Agent work, two jobs for the same proof node, and a proof
  // thread that reports both jobs. The thread keeps its old count after one job finishes.
  await page.goto("/#session=fixture");
  const rail = page.locator("nav.rail");
  const launcher = page.getByRole("button", { name: /^Agent activity/ });
  const team = rail.getByRole("link", { name: /^Team/ });
  await expect(launcher.getByText("2 running", { exact: true })).toBeVisible();
  await expect(team).toHaveAccessibleName(/2 running/);
  await expect(team.locator(".rail-badge")).toHaveText("2");

  await openDestination(page, "Claims");
  const card = page.locator(".board-card").filter({ hasText: CLAIM_TITLE });
  await expect(card.getByText("2 running", { exact: true })).toBeVisible();
  await expect(card.getByRole("link", { name: "1 result to review", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "1 to review", exact: true })).toBeVisible();

  await rail.getByRole("link", { name: "Chats", exact: true }).click();
  const proof = page.getByRole("region", { name: "Proof activity", exact: true });
  await expect(proof.getByText("2 working", { exact: true })).toBeVisible();
  await launcher.click();
  const activity = page.getByRole("region", { name: "Agent activity", exact: true });
  const running = activity.locator('[data-activity-source="job"][data-activity-state="running"]');
  await expect(running).toHaveCount(2);
  await expect(running.first()).toContainText(CLAIM_TITLE);
  await expect(running.first()).toContainText("GPT-5 Codex");
  await expect(running.first().locator("time")).toContainText(/ago/);
  const runningFilter = activity.getByRole("button", { name: "Running", exact: true });
  await runningFilter.click();
  await expect(runningFilter).toHaveAttribute("aria-pressed", "true");
  await expect(activity.locator("[data-activity-state]")).toHaveCount(2);

  let navigations = 0;
  const countNavigations = () => navigations++;
  page.on("framenavigated", countNavigations);
  finished = true;
  await emitEvent({
    id: "2",
    type: "workspace.changed",
    workspace_id: WORKSPACE,
    schema_version: 1,
    occurred_at: new Date().toISOString(),
    actor: { kind: "system", id: null },
    subject: { kind: "workspace", id: WORKSPACE },
    data: { area: "proof", claim_id: CLAIM_ID },
  });
  await expect(running).toHaveCount(1);
  await expect(launcher.getByText("1 running", { exact: true })).toBeVisible();
  await expect(team).toHaveAccessibleName(/1 running/);
  await expect(team.locator(".rail-badge")).toHaveText("1");
  await expect(proof.getByText("1 working", { exact: true })).toBeVisible();
  expect(navigations).toBe(0);
  page.off("framenavigated", countNavigations);

  await activity.getByRole("button", { name: "Completed", exact: true }).click();
  await expect(activity.locator('[data-activity-source="job"][data-activity-state="completed"]')).toHaveCount(2);

  await openDestination(page, "Claims");
  await expect(card.getByText("1 running", { exact: true })).toBeVisible();
  const reviewFilter = activity.getByRole("button", { name: "Needs review", exact: true });
  await reviewFilter.click();
  await expect(reviewFilter).toHaveAttribute("aria-pressed", "true");
  await expect(activity.locator("[data-activity-state]")).toHaveCount(1);
  const review = activity.getByRole("link", { name: new RegExp(CLAIM_TITLE) });
  await expect(review).toHaveAttribute("href", `#review/${EVIDENCE_ID}`);
  await review.click();
  await expect(page).toHaveURL(new RegExp(`#review/${EVIDENCE_ID}$`));
  await expect(page.getByRole("button", { name: "Open in workbench", exact: true })).toBeVisible();

  await launcher.click();
  await activity.getByRole("button", { name: "Running", exact: true }).click();
  await running.getByRole("button").click();
  const origin = page.locator(".main-proof-graph").getByRole("button", { name: /^Proof obligation 2/ });
  await expect(origin).toHaveAttribute("aria-pressed", "true");
});

test("an empty Agent activity panel offers a path to Agent tasks", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.route("**/api/inbox", (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/team", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), jobs: [] } });
  });
  await page.route("**/api/threads", (route) => route.fulfill({ json: { threads: [] } }));
  await page.goto("/#session=fixture");

  await page.getByRole("button", { name: "Agent activity", exact: true }).press("Enter");
  const activity = page.getByRole("region", { name: "Agent activity", exact: true });
  await expect(activity.getByText("No active work or results to review.", { exact: true })).toBeVisible();
  await expect(activity.getByRole("group", { name: "Filter activity", exact: true })).toHaveCount(0);
  expect((await activity.boundingBox())!.height).toBeLessThan(100);
  const assignment = activity.getByRole("link", { name: "Open Agent tasks", exact: true });
  await expect(assignment).toBeInViewport({ ratio: 1 });
  await assignment.press("Enter");
  await expect(page).toHaveURL(/#agent-work$/);
  await expect(page.getByRole("main").getByRole("heading", { name: "Agent tasks", exact: true })).toBeVisible();
});
