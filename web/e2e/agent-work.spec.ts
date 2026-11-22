import { expect, test, type Page } from "@playwright/test";
import type { AgentWorkRequest, AgentWorkTask } from "../src/api/agentWork";
import type { TeamAgent } from "../src/api/teamAgents";

const WORKSPACE = "11000000-0000-4000-8000-000000000011";
const CONNECTION = "11000000-0000-4000-8000-000000000012";
const BASE = "a".repeat(40);
const PATCH = "b".repeat(64);
const REVIEW = "c".repeat(64);
const DATE = "2026-08-04T12:00:00Z";
const savedAgents: TeamAgent[] = ["Implementer", "Reviewer"].map((name, index) => ({
  id: `11000000-0000-4000-8000-00000000002${index}`,
  revision: 1,
  connection_id: CONNECTION,
  name,
  description: "",
  avatar: "initials",
  instructions: "",
  starters: [],
}));

async function fixture(page: Page, initial: AgentWorkTask[] = []) {
  const tasks = new Map(initial.map((task) => [task.id, task]));
  const previews: AgentWorkRequest[] = [];
  const starts: (AgentWorkRequest & { token: string; confirm_external: boolean })[] = [];
  const decisions: object[] = [];
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: savedAgents } }));
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({
      json: {
        connections: [
          {
            id: CONNECTION,
            provider: "codex-cli",
            label: "Code model",
            model: "gpt-5-codex",
            status: "connected",
            fingerprint: "a".repeat(64),
          },
        ],
      },
    }),
  );
  await page.route(/\/api\/agent-work(?:\?task=[^/]+)?$/, (route) => {
    const id = new URL(route.request().url()).searchParams.get("task");
    return route.fulfill({ json: id ? { task: tasks.get(id) } : { tasks: [...tasks.values()], available: true } });
  });
  await page.route("**/api/agent-work/preview", (route) => {
    const draft = route.request().postDataJSON() as AgentWorkRequest;
    previews.push(draft);
    return route.fulfill({
      json: {
        token: "preview-" + draft.task_id,
        workspace_id: WORKSPACE,
        agent_name: savedAgents.find((agent) => agent.id === draft.agent_id)?.name,
        provider: "codex-cli",
        model: "gpt-5-codex",
        external: true,
        outgoing: draft.instructions,
        tools: draft.predecessor_id ? ["read_delivery"] : ["read_pinned_files", "validate_patch"],
        base_revision: BASE,
        predecessor_sha256: draft.predecessor_id ? PATCH : null,
      },
    });
  });
  await page.route("**/api/agent-work/start", (route) => {
    const request = route.request().postDataJSON() as (typeof starts)[number];
    starts.push(request);
    const previous = tasks.get(request.task_id);
    if (previous) return route.fulfill({ json: { task: previous } });
    const review = request.mode === "review";
    const task: AgentWorkTask = {
      id: request.task_id,
      title: request.title,
      mode: request.mode,
      agent_name: savedAgents.find((agent) => agent.id === request.agent_id)!.name,
      status: "delivered",
      created_at: DATE,
      updated_at: DATE,
      error: null,
      checkout: "inputs/example",
      base_revision: BASE,
      criteria: request.criteria,
      predecessor_id: request.predecessor_id ?? null,
      predecessor_sha256: review ? PATCH : null,
      delivery_sha256: review ? REVIEW : PATCH,
      delivery: {
        kind: review ? "review" : "patch",
        summary: review ? "Missing coverage for empty input." : "The total now includes the last item.",
        body: review ? "Add a check for an empty list." : "+ return sum(items)",
        checks: review ? [] : ["patch_applies"],
      },
    };
    tasks.set(task.id, task);
    // The task is recorded even when its first start response is lost.
    if (starts.length === 1) return route.fulfill({ status: 503, json: { error: "unreadable_response" } });
    return route.fulfill({ json: { task } });
  });
  await page.route("**/api/agent-work/review", (route) => {
    const request = route.request().postDataJSON();
    decisions.push(request);
    const previous = tasks.get(request.task_id)!;
    const task: AgentWorkTask = {
      ...previous,
      status: request.decision === "accept" ? "accepted" : "returned",
      review: { decision: request.decision, reason: request.reason, reviewed_at: DATE },
    };
    tasks.set(task.id, task);
    return route.fulfill({ json: { task } });
  });
  return { tasks, previews, starts, decisions };
}

test("a named implementation goes through scope review, exact-result handoff, and a recoverable human decision", async ({
  page,
}) => {
  const { tasks, previews, starts, decisions } = await fixture(page);
  await page.goto("/#session=fixture");
  await page.locator("nav.rail").getByRole("link", { name: /^Team/ }).click();
  await page.locator(".section-nav").getByRole("link", { name: "Agents", exact: true }).click();
  await page.getByRole("link", { name: "Assign task to Implementer" }).click();
  const agent = page.getByRole("button", { name: "Agent", exact: true });
  await expect(agent).toHaveText(savedAgents[0]!.name);
  expect(starts).toHaveLength(0);
  await page.getByLabel("Task title").fill("Fix the total");
  await page.getByLabel("Instructions").fill("Include the last item in the total.");
  await page.getByLabel("Acceptance criteria").fill("The total includes every item.");
  await page.getByLabel("Project checkout").fill("inputs/example");
  await page.getByLabel("Base commit").fill(BASE);
  await page.getByLabel("Allowed files").fill("total.py\n\n test_total.py ");
  await page.getByRole("button", { name: "Preview task" }).click();

  const start = page.getByRole("button", { name: "Start task" });
  await expect(start).toBeDisabled();
  await expect(page.getByText(WORKSPACE, { exact: true })).toBeVisible();
  await expect(page.getByText(BASE, { exact: true })).toBeVisible();
  await expect(page.getByText("Read approved file versions, Validate a proposed patch", { exact: true })).toBeVisible();
  await expect(page.getByText("Include the last item in the total.", { exact: true })).toBeVisible();
  expect(previews[0]).toMatchObject({
    agent_id: savedAgents[0]!.id,
    checkout: "inputs/example",
    revision: BASE,
    files: ["total.py", "test_total.py"],
  });
  expect(starts).toHaveLength(0);
  await page.getByRole("checkbox", { name: /^Authorize sending/ }).check();
  await start.click();
  await expect(page.getByRole("alert")).toContainText("Could not confirm that the task started.");
  await start.click();
  await expect(page.getByRole("heading", { name: "Fix the total" })).toBeVisible();
  expect(starts).toHaveLength(2);
  expect(starts[1]).toEqual(starts[0]);
  expect(starts[0]?.confirm_external).toBe(true);
  expect(tasks.size).toBe(1);
  await expect(page.getByText(PATCH, { exact: true })).toBeVisible();
  await expect(page.getByText("The patch applies to the approved file versions.")).toBeVisible();
  await page.reload();
  await expect(page.getByText("The total now includes the last item.")).toBeVisible();

  await page.getByRole("link", { name: "Assign review" }).click();
  await agent.click();
  await page.getByRole("option", { name: savedAgents[1]!.name, exact: true }).click();
  expect(previews).toHaveLength(1);
  expect(starts).toHaveLength(2);
  await page.getByLabel("Instructions").fill("Review the recorded patch.");
  await page.getByLabel("Acceptance criteria").fill("Check both normal and empty input.");
  await page.getByRole("button", { name: "Preview task" }).click();
  await expect(page.getByText(PATCH, { exact: true })).toBeVisible();
  expect(previews[1]).toMatchObject({
    agent_id: savedAgents[1]!.id,
    mode: "review",
    predecessor_id: starts[0]?.task_id,
  });
  await page.getByRole("checkbox", { name: /^Authorize sending/ }).check();
  await start.click();
  await expect(page.getByText("Missing coverage for empty input.")).toBeVisible();
  await page.getByLabel("Review reason").fill("Needs an executable regression test.");
  await page.getByRole("button", { name: "Return delivery" }).click();
  await expect(page.getByRole("status")).toHaveText("Returned");
  expect(decisions[0]).toMatchObject({ delivery_sha256: REVIEW, decision: "return", task_id: starts[2]?.task_id });

  await page.getByRole("link", { name: "Open the original delivery" }).click();
  await page.getByLabel("Review reason").fill("Code change confirmed.");
  await page.getByRole("button", { name: "Accept delivery" }).click();
  await expect(page.getByRole("status")).toHaveText("Accepted");
  expect(decisions[1]).toMatchObject({ delivery_sha256: PATCH, decision: "accept", task_id: starts[0]?.task_id });
  await page.getByRole("link", { name: /Awaiting review/ }).click();
  await expect(page.getByText("No deliveries awaiting your decision.")).toBeVisible();
});

test("retrying an interrupted run requires fresh scope approval and resends the same attempt after a lost receipt", async ({
  page,
}) => {
  const original: AgentWorkTask = {
    id: "11000000-0000-4000-8000-000000000030",
    title: "Draft the release summary",
    mode: "draft",
    agent_name: "Implementer",
    status: "interrupted",
    created_at: DATE,
    updated_at: DATE,
    error: "agentWork.interrupted",
    criteria: "Summarize the changes.",
    predecessor_id: null,
    delivery_sha256: null,
    delivery: null,
  };
  const { tasks } = await fixture(page, [original]);
  const previews: { task_id: string; action_id: string }[] = [];
  const starts: object[] = [];
  await page.route("**/api/agent-work/retry-preview", (route) => {
    previews.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        token: "retry-receipt",
        generation: 2,
        workspace_id: WORKSPACE,
        agent_name: original.agent_name,
        provider: "codex-cli",
        model: "gpt-5-codex",
        external: true,
        outgoing: "Original approved task context",
        tools: [],
      },
    });
  });
  await page.route("**/api/agent-work/retry", (route) => {
    starts.push(route.request().postDataJSON());
    const updated = { ...original, status: "queued" as const, error: null };
    tasks.set(original.id, updated);
    if (starts.length === 1) return route.fulfill({ status: 503, json: { error: "unreadable_response" } });
    return route.fulfill({ json: { task: updated } });
  });

  await page.goto("/#session=fixture");
  await page.locator("nav.rail").getByRole("link", { name: "Agent work", exact: true }).click();
  await page.getByRole("link", { name: /Draft the release summary/ }).click();
  const pageHeader = page.locator(".agent-work-view > .page-head");
  const scrollPanel = page.locator(".agent-work-scroll");
  const agentRow = scrollPanel.locator("dt").filter({ hasText: /^Agent$/ });
  await expect(agentRow).toBeVisible();
  const headerBounds = (await pageHeader.boundingBox())!;
  expect((await scrollPanel.boundingBox())!.y).toBeGreaterThanOrEqual(headerBounds.y + headerBounds.height);
  expect((await agentRow.boundingBox())!.y).toBeGreaterThan(headerBounds.y + headerBounds.height);
  await page.getByRole("button", { name: "Retry task" }).click();
  await expect(page.getByText("Original approved task context")).toBeVisible();
  const confirm = page.getByRole("button", { name: "Start retry" });
  await expect(confirm).toBeDisabled();
  expect(starts).toHaveLength(0);
  await page.getByRole("checkbox", { name: /^Authorize sending/ }).check();
  await confirm.click();
  await expect(page.getByText(/^Could not confirm that the task started/)).toBeVisible();
  await confirm.click();
  await expect(page.getByRole("status")).toHaveText("Queued");
  expect(starts).toEqual([
    { ...previews[0], token: "retry-receipt", generation: 2, confirm_external: true },
    { ...previews[0], token: "retry-receipt", generation: 2, confirm_external: true },
  ]);
  expect(previews[0]?.task_id).toBe(original.id);
  expect(previews[0]?.action_id).toMatch(/^[0-9a-f-]{36}$/);
});

test("Claims, Agent work and Review each have one active rail link, including legacy Agent work links", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/#session=fixture");
  const rail = page.locator("nav.rail");
  const sidebar = page.locator(".section-nav");
  const activeRail = rail.locator('[aria-current="page"]');

  await rail.getByRole("link", { name: "Claims", exact: true }).click();
  await expect(activeRail).toHaveCount(1);
  await expect(activeRail).toHaveAttribute("href", "#claims");
  await expect(sidebar.getByRole("link", { name: "Agent work", exact: true })).toHaveCount(0);

  for (const sub of ["", "/waiting", "%2Fnew%2Fagent%2F" + savedAgents[0]!.id]) {
    await page.evaluate((legacy) => {
      window.location.hash = legacy;
    }, "#claims/agent-work" + sub);
    const canonical = "#agent-work" + (sub.startsWith("%2F") ? "/" + sub.slice(3) : sub);
    await expect(page).toHaveURL(new RegExp(canonical + "$"));
    await expect(activeRail).toHaveCount(1);
    await expect(activeRail).toHaveAttribute("href", "#agent-work");
    await expect(sidebar.getByRole("link", { name: "Claims", exact: true })).toHaveCount(0);
  }

  await sidebar.getByRole("link", { name: "Agent activity", exact: true }).click();
  await expect(page).toHaveURL(/#agent-work\/activity$/);
  await expect(activeRail).toHaveAttribute("href", "#agent-work");
  const activity = page.locator("main").getByRole("group", { name: "Filter activity" });
  await expect(activity).toBeVisible();
  await activity.getByRole("button", { name: "Running", exact: true }).click();
  await expect(page.locator('main [data-activity-state="running"]').first()).toBeVisible();
  await expect(page.locator('main [data-activity-state="completed"]')).toHaveCount(0);

  const review = rail.getByRole("link", { name: /^Review/ });
  await review.click();
  await expect(activeRail).toHaveCount(1);
  await expect(activeRail).toHaveAttribute("href", "#review");
  await page.evaluate(() => {
    window.location.hash = "#review/77777777-7777-4777-8777-777777777777";
  });
  await expect(page).toHaveURL(/#review\/77777777-7777-4777-8777-777777777777$/);
  await expect(activeRail).toHaveCount(1);
  await expect(activeRail).toHaveAttribute("href", "#review");
});
