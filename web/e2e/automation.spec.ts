import { expect, test, type Page } from "@playwright/test";

// #94: Automation leads with New automation and templates; a rule runs only after its exact proposal is approved.
const READY = "11111111-1111-4111-8111-111111111111";
const BLOCKED = "33333333-3333-4333-8333-333333333333";
const POLICY = "44444444-4444-4444-8444-444444444444";
const FINGERPRINT = "c".repeat(64);

const templates = {
  templates: [
    {
      id: "lean-reuse",
      claims: [
        { claim_id: READY, title: "Ready claim", missing: null },
        { claim_id: BLOCKED, title: "Unconfirmed claim", missing: "lean_statement" },
      ],
    },
    { id: "observe", claims: [{ claim_id: READY, title: "Ready claim", missing: null }] },
  ],
};
const review = {
  policy_id: POLICY,
  claim_id: READY,
  claim_title: "Ready claim",
  fingerprint: FINGERPRINT,
  state: "awaiting_approval",
  approved: false,
  stopped: false,
  template: "lean-reuse",
  metric: "exact-closure",
  actions: ["lean"],
  model_rounds: 0,
  budget_seconds: null,
  max_rounds: null,
};

async function openAutomation(page: Page) {
  await page.goto("/#session=fixture");
  await page
    .locator("nav.rail")
    .getByRole("link", { name: /^Automation/ })
    .click();
}

for (const colorScheme of ["light", "dark"] as const) {
  test(`an empty project starts from a template and approves the exact proposal in ${colorScheme}`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme });
    const writes: { path: string; body: unknown }[] = [];
    let approvalFails = true;
    await page.route("**/api/progress", (route) => route.fulfill({ json: { policies: [] } }));
    await page.route("**/api/automation", (route) => route.fulfill({ json: templates }));
    await page.route("**/api/automation/*", (route) => {
      const path = new URL(route.request().url()).pathname;
      writes.push({ path, body: route.request().postDataJSON() });
      if (path.endsWith("/propose")) return route.fulfill({ json: review });
      if (approvalFails) {
        approvalFails = false;
        return route.fulfill({ status: 409, json: { error: "automation.worker_unavailable" } });
      }
      return route.fulfill({ json: { ...review, approved: true, run: "started" } });
    });
    await openAutomation(page);

    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "No automations yet", exact: true })).toBeVisible();
    await expect(page.locator(".section-nav").getByRole("link", { name: /^All rules/ })).toHaveCount(0);
    await expect(main.getByText("research-multiplier", { exact: false })).toHaveCount(0);
    await expect(main.getByRole("button", { name: "New automation", exact: true })).toBeVisible();

    await main.getByRole("button", { name: /^Reuse and reduce Lean proofs/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "Choose a claim", exact: true })).toBeVisible();
    const blocked = dialog.getByRole("button", { name: /^Unconfirmed claim/ });
    await expect(blocked).toBeDisabled();
    await expect(blocked).toContainText("Confirm its exact Lean statement first");
    await dialog.getByRole("button", { name: /^Ready claim/ }).click();

    // Choosing a claim records a proposal for review; it does not approve or run anything.
    await expect(dialog.getByRole("heading", { name: "Review and approve", exact: true })).toBeVisible();
    expect(writes.map(({ path }) => path)).toEqual(["/api/automation/propose"]);
    expect(writes[0]!.body).toEqual({ claim_id: READY, template: "lean-reuse" });
    await expect(dialog.getByText("Nothing runs until you approve this exact rule.")).toBeVisible();
    await expect(dialog.getByText("Ready claim", { exact: true })).toBeVisible();
    await expect(dialog.getByText("None", { exact: true })).toBeVisible();

    const approve = dialog.getByRole("button", { name: "Approve and run", exact: true });
    await approve.click();
    await expect(dialog.getByRole("alert")).toContainText("worker logins are missing");
    await approve.click();
    await expect(dialog).toBeHidden();
    await expect(main.getByRole("status")).toHaveText("Approved. The first run has started.");
    expect(writes.at(-1)).toEqual({
      path: "/api/automation/approve",
      body: { policy_id: POLICY, fingerprint: FINGERPRINT, reason: "Run this rule on the selected claim." },
    });
  });
}

test("rules waiting for approval are reviewed in place, and a running rule is stopped with a reason", async ({
  page,
}) => {
  const writes: { path: string; body: unknown }[] = [];
  let stopFails = true;
  const base = {
    claim_id: READY,
    claim_title: "Ready claim",
    proposed_at: "2026-11-20T02:00:00Z",
    approved_by: null,
    approved_at: null,
    stopped_at: null,
    stop_reason: null,
    last_run_at: null,
    last_status: null,
    last_reason: null,
  };
  const policies = [
    {
      ...base,
      id: POLICY,
      state: "awaiting_approval",
      policy: { metric: "exact-closure", actions: [] },
      policy_sha256: FINGERPRINT,
    },
    {
      ...base,
      id: "55555555-5555-4555-8555-555555555555",
      state: "running",
      approved_by: "Trellin local researcher",
      approved_at: "2026-11-20T02:05:00Z",
      policy: { metric: "exact-closure", actions: ["lean"] },
      policy_sha256: "d".repeat(64),
    },
  ];
  await page.route("**/api/progress", (route) => route.fulfill({ json: { policies } }));
  await page.route("**/api/automation/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    writes.push({ path, body: route.request().postDataJSON() });
    if (path.endsWith("/stop") && stopFails) {
      stopFails = false;
      return route.fulfill({ status: 409, json: { error: "automation.stale" } });
    }
    return route.fulfill({ json: { ...review, approved: true, stopped: path.endsWith("/stop"), run: "waiting" } });
  });
  await openAutomation(page);

  const main = page.getByRole("main");
  await expect(page.locator(".section-nav").getByRole("link", { name: /^Waiting for approval/ })).toBeVisible();
  await expect(main.getByRole("button", { name: "New automation", exact: true })).toBeVisible();
  const cards = main.locator(".policy-card");
  await expect(cards).toHaveCount(2);

  await cards.nth(0).getByRole("button", { name: "Review and approve", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Review and approve", exact: true })).toBeVisible();
  await expect(dialog.getByText("Observe only", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Approve and run", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(main.getByRole("status")).toHaveText("Approved. It runs when the current proof work finishes.");
  expect(writes[0]).toMatchObject({
    path: "/api/automation/approve",
    body: { policy_id: POLICY, fingerprint: FINGERPRINT },
  });

  const running = cards.nth(1);
  await running.getByRole("button", { name: "Stop", exact: true }).click();
  const stop = running.getByRole("group", { name: "Stop" });
  await expect(stop.getByText("Stopping keeps its results and run history.")).toBeVisible();
  await stop.getByRole("textbox", { name: "Why stop this rule" }).fill("Enough for now.");
  await stop.getByRole("button", { name: "Stop rule", exact: true }).click();
  await expect(stop.getByRole("alert")).toHaveText("This rule changed. Review it again.");
  await stop.getByRole("button", { name: "Stop rule", exact: true }).click();
  await expect(stop).toBeHidden();
  expect(writes.at(-1)).toEqual({
    path: "/api/automation/stop",
    body: { policy_id: "55555555-5555-4555-8555-555555555555", reason: "Enough for now." },
  });
});
