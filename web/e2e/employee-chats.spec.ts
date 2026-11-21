import { expect, test, type Page } from "@playwright/test";
import type { Conversation, ConversationRequest } from "../src/api/conversations";
import type { ModelConnection } from "../src/api/modelConnections";
import type { TeamAgent } from "../src/api/teamAgents";

const connection: ModelConnection = {
  id: "9a33cb02-fa5c-40f4-bc2e-591c368e754e",
  provider: "codex-cli",
  model: "native-default",
  label: "Research Codex",
  status: "connected",
  fingerprint: "a".repeat(64),
};
const analyst: TeamAgent = {
  id: "a9a3cb02-fa5c-40f4-bc2e-591c368e754e",
  revision: 1,
  connection_id: connection.id,
  name: "Research Analyst",
  description: "Checks the assumptions",
  avatar: "compass",
  instructions: "Identify missing assumptions.",
  starters: [],
};
const reviewer: TeamAgent = {
  ...analyst,
  id: "b9a3cb02-fa5c-40f4-bc2e-591c368e754e",
  name: "Proof Reviewer",
  description: "Reviews the argument",
  avatar: "book",
  instructions: "Review the argument.",
};
const acceptedAt = "2026-11-20T02:00:00Z";

function task(
  id: string,
  title: string,
  agentId: string | null,
  status?: "running" | "answered" | "failed",
): Conversation {
  return {
    id,
    title,
    agent_id: agentId,
    claim_id: null,
    statement_id: null,
    node_id: null,
    created_at: acceptedAt,
    updated_at: acceptedAt,
    revision: status ? 1 : 0,
    running: status === "running",
    messages: [
      ...(status ? [{ id: id + "-turn", role: "user" as const, text: "Please take a look.", at: acceptedAt }] : []),
      ...(status === "answered"
        ? [{ id: id + "-reply", role: "assistant" as const, text: "The argument checks out.", at: acceptedAt }]
        : []),
    ],
    latest_error: status === "failed" ? "conversation.interrupted" : null,
    latest_turn: status
      ? {
          provider: connection.provider,
          requested_model: connection.model,
          model: null,
          preview: "Please take a look.",
          at: acceptedAt,
          status,
        }
      : null,
  };
}

async function openChats(page: Page, threads: Conversation[], loseFirstCreateResponse = false) {
  const creates: { agent_id: string; title: string; request_id: string }[] = [];
  const createdThreads = new Map<string, Conversation>();
  const previews: ConversationRequest[] = [];
  const sends: (ConversationRequest & { token: string; confirm_external: boolean })[] = [];
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [analyst, reviewer] } }));
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [connection] } }));
  await page.route("**/api/project-intake/models", (route) => route.fulfill({ json: { models: [] } }));
  await page.route(/\/api\/conversations(?:[/?]|$)/, (route) => {
    const request = route.request();
    const url = new URL(request.url());
    switch (url.pathname) {
      case "/api/conversations/thread": {
        const thread = threads.find(({ id }) => id === url.searchParams.get("thread_id"));
        return route.fulfill({ json: { thread: thread ? { ...thread, latest_turn: undefined } : undefined } });
      }
      case "/api/conversations/create": {
        const body = request.postDataJSON() as (typeof creates)[number];
        creates.push(body);
        const existing = createdThreads.get(body.request_id);
        if (existing) return route.fulfill({ json: { thread: existing } });
        const thread = task(crypto.randomUUID(), body.title, body.agent_id);
        createdThreads.set(body.request_id, thread);
        threads.unshift(thread);
        if (loseFirstCreateResponse && creates.length === 1) {
          return route.fulfill({ status: 503, json: { error: "unreadable_response" } });
        }
        return route.fulfill({ json: { thread } });
      }
      case "/api/conversations/preview":
        previews.push(request.postDataJSON());
        return route.fulfill({
          json: { token: "fixture-preview", provider: connection.provider, model: connection.model, external: true },
        });
      case "/api/conversations/send": {
        const body = request.postDataJSON() as (typeof sends)[number];
        sends.push(body);
        const thread = threads.find(({ id }) => id === body.thread_id);
        if (!thread) return route.fulfill({ status: 404, json: { error: "conversation.missing" } });
        thread.running = true;
        thread.revision += 1;
        thread.latest_error = null;
        thread.messages.push({ id: body.request_id, role: "user", text: body.text, at: acceptedAt });
        thread.latest_turn = {
          provider: connection.provider,
          requested_model: connection.model,
          model: null,
          preview: body.text,
          at: acceptedAt,
          status: "running",
        };
        return route.fulfill({ json: { thread } });
      }
      default:
        return route.fulfill({ json: { threads } });
    }
  });
  await page.goto("/#session=fixture");
  await page.getByRole("link", { name: "Chats", exact: true }).click();
  return { creates, createdThreads, previews, sends };
}

test("employee tasks and drafts stay separate when both employees use the same model connection", async ({ page }) => {
  const running = task("aa000000-0000-4000-8000-000000000001", "Check assumptions", analyst.id, "running");
  const failed = task("aa000000-0000-4000-8000-000000000002", "Test a counterexample", analyst.id, "failed");
  const proof = task("aa000000-0000-4000-8000-000000000003", "Review the proof", reviewer.id, "answered");
  const legacy = {
    ...task("aa000000-0000-4000-8000-000000000004", "Earlier conversation", null, "answered"),
    claim_id: "11111111-1111-4111-8111-111111111111",
    node_id: "obligation:55555555-5555-4555-8555-555555555555:2",
  };
  await page.clock.install({ time: new Date("2026-11-20T02:00:02Z") });
  const { creates, previews, sends } = await openChats(page, [running, failed, proof, legacy]);
  const roster = page.getByRole("list", { name: "Employees" });
  const analystRow = roster.getByRole("link", { name: /Research Analyst/ });
  const reviewerRow = roster.getByRole("link", { name: /Proof Reviewer/ });
  const search = page.getByRole("searchbox", { name: "Search employees" });
  await expect(roster.getByRole("link")).toHaveCount(2);
  await search.fill("review");
  await expect(reviewerRow).toBeVisible();
  await expect(analystRow).toHaveCount(0);
  await search.clear();
  await expect(analystRow).toContainText("1 running");
  await expect(reviewerRow).toContainText("Idle");

  await analystRow.click();
  const switcher = page.getByRole("button", { name: /^Switch task:/ });
  const options = page.getByRole("menu", { name: "Switch task" }).getByRole("menuitemradio");
  const runningOption = options.filter({ hasText: running.title });
  const failedOption = options.filter({ hasText: failed.title });
  const status = page.locator(".employee-chat-head .employee-chat-status");
  await expect(switcher).toHaveText(running.title);
  await switcher.press("Enter");
  await expect(options).toHaveCount(2);
  await expect(runningOption).toHaveAttribute("aria-checked", "true");
  await expect(runningOption).toContainText("Working");
  await expect(runningOption).toBeFocused();
  await expect(failedOption).toContainText("Send failed");
  await runningOption.press("ArrowDown");
  await expect(failedOption).toBeFocused();
  await failedOption.press("Escape");
  await expect(options).toHaveCount(0);
  await expect(switcher).toBeFocused();
  await expect(status).toHaveText("1 running · 1 failed");
  const activity = page.locator(".chat-feed").getByRole("status");
  await expect(activity).toHaveCount(1);
  await expect(activity).toContainText("Waiting for Research Analyst to reply");
  await expect(page.locator(".chat-send-elapsed")).toHaveText(/^\d+ sec$/);

  await switcher.press("ArrowUp");
  await expect(failedOption).toBeFocused();
  await failedOption.press("Enter");
  await expect(switcher).toHaveText(failed.title);
  await expect(switcher).toBeFocused();
  const analystDraft = page.getByRole("textbox", { name: "Message Research Analyst" });
  await analystDraft.fill("Compare the boundary cases.");
  await reviewerRow.click();
  await expect(switcher).toHaveText(proof.title);
  await switcher.click();
  await expect(options).toHaveCount(1);
  await expect(options.first()).toHaveText(proof.title);
  await expect(options.first()).toHaveAttribute("aria-checked", "true");
  await options.first().press("Enter");
  await expect(switcher).toBeFocused();
  await expect(status).toHaveText("Idle");
  const reviewerDraft = page.getByRole("textbox", { name: "Message Proof Reviewer" });
  await expect(reviewerDraft).toHaveValue("");
  await reviewerDraft.fill("Check the use of induction.");
  await analystRow.click();
  await expect(switcher).toHaveText(failed.title);
  await expect(analystDraft).toHaveValue("Compare the boundary cases.");
  await switcher.click();
  await runningOption.click();
  await expect(analystDraft).toHaveValue("");

  expect([creates.length, previews.length, sends.length]).toEqual([0, 0, 0]);
  running.running = false;
  running.latest_turn!.status = "answered";
  running.messages.push({
    id: "first-reply",
    role: "assistant",
    text: "The assumptions are explicit.",
    at: acceptedAt,
  });
  await page.clock.fastForward(8100);
  await expect(status).toHaveText("Idle · 1 failed");
  await expect(page.getByText("The assumptions are explicit.", { exact: true })).toBeVisible();
  await expect(activity).toHaveCount(0);
  await expect(page.locator(".chat-send-elapsed")).toHaveCount(0);

  await switcher.click();
  await failedOption.click();
  await expect(analystDraft).toHaveValue("Compare the boundary cases.");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
  await analystDraft.press("Enter");
  await expect(activity).toContainText("Waiting for Research Analyst to reply");
  expect(previews).toHaveLength(1);
  expect(previews[0]).toEqual({
    thread_id: failed.id,
    connection_id: connection.id,
    text: "Compare the boundary cases.",
    request_id: expect.any(String),
  });
  expect(sends).toEqual([{ ...previews[0], token: "fixture-preview", confirm_external: true }]);
  expect(creates).toHaveLength(0);

  await reviewerRow.click();
  await expect(reviewerDraft).toHaveValue("Check the use of induction.");
  await expect(status).toHaveText("Idle");
  const chatNav = page.getByRole("navigation", { name: "Chats", exact: true });
  await expect(chatNav.getByRole("link", { name: /Earlier conversation|Proof obligation 2/ })).toHaveCount(0);
  await expect(chatNav.getByRole("button", { name: "New conversation", exact: true })).toHaveCount(0);
  await chatNav.getByRole("link", { name: "Conversation history" }).click();
  await expect(page.getByRole("heading", { name: "Conversation history" })).toBeVisible();
  await page.getByRole("link", { name: /Earlier conversation/ }).click();
  await expect(page.getByRole("heading", { name: legacy.title })).toBeVisible();
  await expect(page.getByText("The argument checks out.", { exact: true })).toBeVisible();
  await expect(page.getByText("Linked to a proof node", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Conversation model" })).toBeVisible();
  await chatNav.getByRole("link", { name: "Conversation history" }).click();
  await page.getByRole("link", { name: /Proof obligation 2/ }).click();
  await expect(page.getByRole("heading", { name: "Proof obligation 2" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open in Workbench" })).toBeVisible();

  await analystRow.click();
  await switcher.click();
  await runningOption.click();
  await page.reload();
  await expect(switcher).toHaveText(running.title);
  await expect(analystDraft).toBeVisible();
  await expect(page.getByText("The assumptions are explicit.", { exact: true })).toBeVisible();
  expect(sends).toHaveLength(1);
});

test("creating a task binds it to the selected employee and retries the same request", async ({ page }) => {
  const { creates, createdThreads, previews, sends } = await openChats(page, [], true);
  const roster = page.getByRole("list", { name: "Employees" });
  await roster.getByRole("link", { name: /Proof Reviewer/ }).click();
  await expect(page.getByRole("heading", { name: "Proof Reviewer", exact: true })).toBeVisible();
  expect(creates).toHaveLength(0);

  await page.getByRole("button", { name: "New task", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New task" });
  const name = dialog.getByRole("textbox", { name: "Task name" });
  const submit = dialog.getByRole("button", { name: "Create task" });
  await name.fill("Prove the base case");
  await submit.click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(name).toHaveValue("Prove the base case");
  expect(creates).toEqual([{ title: "Prove the base case", agent_id: reviewer.id, request_id: expect.any(String) }]);
  await submit.click();
  await expect(dialog).toBeHidden();
  const selected = page.getByRole("button", { name: /^Switch task:/ });
  await expect(selected).toHaveText("Prove the base case");
  expect(creates).toEqual([creates[0], creates[0]]);
  await expect(page).toHaveURL(new RegExp(createdThreads.get(creates[0]!.request_id)!.id + "$"));
  await selected.click();
  const options = page.getByRole("menu", { name: "Switch task" }).getByRole("menuitemradio");
  await expect(options).toHaveCount(1);
  await expect(options.first()).toHaveAttribute("aria-checked", "true");
  await options.first().press("Escape");
  await expect(page.getByRole("textbox", { name: "Message Proof Reviewer" })).toBeVisible();
  expect(previews).toHaveLength(0);
  expect(sends).toHaveLength(0);
  await roster.getByRole("link", { name: /Research Analyst/ }).click();
  await expect(page.getByRole("heading", { name: "Research Analyst", exact: true })).toBeVisible();
  await expect(selected).toHaveCount(0);
});

test("the task picker and legacy history are reachable in a narrow window", async ({ page }) => {
  await page.setViewportSize({ width: 570, height: 760 });
  const longTitle = "Check every assumption that would be needed to make the comparison hold across all cases";
  const assigned = task("aa000000-0000-4000-8000-000000000005", longTitle, analyst.id, "running");
  const legacy = task("aa000000-0000-4000-8000-000000000006", "Unbound research chat", null, "answered");
  const { creates, previews, sends } = await openChats(page, [assigned, legacy]);
  await page.getByRole("link", { name: /Research Analyst/ }).click();
  const picker = page.getByRole("button", { name: "Switch task: " + longTitle });
  await expect(picker).toBeVisible();
  await picker.click();
  const entry = page.getByRole("menuitemradio", { name: new RegExp(longTitle) });
  await expect(entry).toBeInViewport({ ratio: 1 });
  await expect(entry).toHaveAttribute("aria-checked", "true");
  await entry.press("Escape");
  await expect(page.getByRole("button", { name: "New task", exact: true })).toBeInViewport({ ratio: 1 });
  await page.getByRole("link", { name: "Back to chats" }).click();
  await page.getByRole("link", { name: "Conversation history" }).click();
  await expect(page.getByRole("heading", { name: "Conversation history" })).toBeVisible();
  await page.getByRole("link", { name: /Unbound research chat/ }).click();
  await page.reload();
  await expect(page.getByRole("heading", { name: legacy.title })).toBeVisible();
  await expect(page.getByText("The argument checks out.", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Conversation history" }).click();
  await page.getByRole("link", { name: "Back to chats" }).click();
  await expect(page.getByRole("list", { name: "Employees" })).toBeVisible();
  expect([creates.length, previews.length, sends.length]).toEqual([0, 0, 0]);
});
