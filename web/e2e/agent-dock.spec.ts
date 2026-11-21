import { expect, test } from "@playwright/test";
import type { AgentWorkSummary } from "../src/api/agentWork";
import type { Conversation, ConversationAnchor, ConversationRequest } from "../src/api/conversations";
import type { TeamAgent } from "../src/api/teamAgents";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";

test("Agent chat keeps map context explicit and preserves the selected Agent and draft across pages", async ({
  page,
  request,
}) => {
  await request.post("/__reset", { data: {} });
  const connection = {
    id: "shared-codex",
    provider: "codex-cli",
    model: "native-default",
    label: "Fixture Codex",
    status: "connected",
    fingerprint: "a".repeat(64),
  };
  const agents = ["Research Agent", "Lean Reviewer"].map(
    (name, index): TeamAgent => ({
      id: "agent-" + index,
      revision: 1,
      name,
      description: "",
      connection_id: connection.id,
      avatar: "book",
      starters: [],
      instructions: "",
    }),
  );
  const work = {
    id: "4b9987f9-9836-485b-892f-4f11ca4fc215",
    title: "Review model assumptions",
    agent_id: "agent-1",
    agent_name: "Former Reviewer",
    status: "delivered",
  } satisfies Partial<AgentWorkSummary>;
  const when = "2026-11-21T01:00:00Z";
  const threads: Conversation[] = [];
  const created: ({ agent_id: string; title: string; request_id: string } & ConversationAnchor)[] = [];
  const previews: ConversationRequest[] = [];
  const sends: unknown[] = [];
  let accept = () => {};
  const canAccept = new Promise<void>((resolve) => (accept = resolve));
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents } }));
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [connection] } }));
  await page.route("**/api/agent-work", (route) => route.fulfill({ json: { tasks: [work], available: true } }));
  await page.route(/\/api\/conversations(?:[/?]|$)/, async (route) => {
    const incoming = route.request();
    const url = new URL(incoming.url());
    if (url.pathname.endsWith("/create")) {
      const body = incoming.postDataJSON() as (typeof created)[number];
      created.push(body);
      const thread: Conversation = {
        id: crypto.randomUUID(),
        title: body.title,
        agent_id: body.agent_id,
        claim_id: body.claim_id ?? null,
        statement_id: body.statement_id ?? null,
        node_id: body.node_id ?? null,
        created_at: when,
        updated_at: when,
        running: false,
        revision: 0,
        messages: [],
      };
      threads.unshift(thread);
      return route.fulfill({ json: { thread } });
    }
    if (url.pathname.endsWith("/preview")) {
      previews.push(incoming.postDataJSON());
      return route.fulfill({
        json: {
          token: "previewed-context",
          provider: connection.provider,
          model: connection.model,
          external: true,
          outgoing: '{"scope":"selected proof node only"}',
        },
      });
    }
    if (url.pathname.endsWith("/send")) {
      const body = incoming.postDataJSON() as ConversationRequest;
      sends.push(body);
      await canAccept;
      const thread = threads.find(({ id }) => id === body.thread_id);
      if (!thread) return route.fulfill({ status: 404, json: { error: "conversation.missing" } });
      thread.revision++;
      thread.messages = [
        { id: "question", role: "user", text: body.text, at: when },
        { id: "reply", role: "assistant", text: "Check the first assumption.", at: when },
      ];
      return route.fulfill({ json: { thread } });
    }
    if (url.pathname.endsWith("/thread")) {
      return route.fulfill({ json: { thread: threads.find(({ id }) => id === url.searchParams.get("thread_id")) } });
    }
    return route.fulfill({ json: { threads } });
  });

  await page.goto("/#session=fixture");
  await openProof(page);
  const map = page.getByRole("application", { name: "Project map", exact: true });
  const node = page.getByRole("button", { name: /^Proof obligation 2/ });
  await node.click();
  await page.getByRole("button", { name: "Ask an Agent", exact: true }).click();
  const dock = page.getByRole("region", { name: "Agent chat", exact: true });
  await expect(dock.getByText("Context · Proof obligation 2")).toBeVisible();
  await expect(map).toBeVisible();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toBeVisible();
  expect(created).toHaveLength(0);
  await dock.getByLabel("Choose an Agent", { exact: true }).click();
  await dock.getByRole("searchbox", { name: "Search Agents" }).fill("lean");
  await dock.getByRole("button", { name: "Lean Reviewer" }).click();
  await dock.getByRole("button", { name: "Open conversation", exact: true }).click();
  const input = dock.getByRole("textbox", { name: "Message Lean Reviewer" });
  await input.fill("Check the fixture obligation.");
  expect(created).toEqual([
    {
      agent_id: "agent-1",
      title: "Proof obligation 2",
      claim_id: expect.any(String),
      statement_id: expect.any(String),
      node_id: await node.getAttribute("data-node"),
      request_id: expect.any(String),
    },
  ]);
  await page.locator("nav.rail").getByRole("link", { name: /^Team/ }).click();
  await expect(input).toHaveValue("Check the fixture obligation.");
  await dock.getByRole("button", { name: "Collapse Agent panel" }).click();
  await expect(dock).toBeHidden();
  await page.getByRole("button", { name: "Agent chat", exact: true }).click();
  await expect(input).toHaveValue("Check the fixture obligation.");
  expect(previews).toHaveLength(0);

  await dock.getByRole("button", { name: "Message context" }).click();
  await dock.getByRole("menuitem", { name: "Request details" }).click();
  await expect(dock.locator(".conversation-request pre")).toHaveText('{"scope":"selected proof node only"}');
  expect(sends).toHaveLength(0);
  try {
    await input.press("Enter");
    await expect.poll(() => sends.length).toBe(1);
    await page.locator("nav.rail").getByRole("link", { name: "Home", exact: true }).click();
    await expect(map).toBeVisible();
    await expect(dock.getByText("Sending…", { exact: true })).toBeVisible();
  } finally {
    accept();
  }
  await expect(dock.getByText("Check the first assumption.", { exact: true })).toBeVisible();
  expect(sends).toEqual([{ ...previews[0], token: "previewed-context", confirm_external: true }]);
  await dock.getByRole("link", { name: "Open full conversation" }).click();
  await expect(page.getByText("Check the first assumption.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Agent chat", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Message Lean Reviewer" })).toHaveCount(1);
  await expect(dock.getByText("This conversation is open in Chats.")).toBeVisible();
  await page.getByRole("button", { name: /^Agent activity/ }).click();
  const task = page.getByRole("link", { name: /Review model assumptions/ });
  await expect(task).toContainText("Lean Reviewer");
  await expect(task).toHaveAttribute("href", "#claims/" + encodeURIComponent("agent-work/" + work.id));
});
