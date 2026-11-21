import { expect, test } from "@playwright/test";
import type { AgentWorkSummary } from "../src/api/agentWork";
import type { Conversation, ConversationAnchor, ConversationRequest } from "../src/api/conversations";
import type { TeamAgent } from "../src/api/teamAgents";
import { openProof } from "./project-map";

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
  const proof = page.locator(".main-proof-graph");
  const node = page.getByRole("button", { name: /^Proof obligation 2/ });
  await node.click();
  const nodeId = await node.getAttribute("data-node");
  await page.getByRole("button", { name: "Ask an Agent", exact: true }).click();
  const dock = page.getByRole("region", { name: "Agent chat", exact: true });
  await expect(dock.getByText("Context · Proof obligation 2")).toBeVisible();
  await expect(proof).toBeVisible();
  await expect(node).toHaveAttribute("aria-pressed", "true");
  expect(created).toHaveLength(0);
  const initial = dock.getByRole("textbox", { name: "Write a message" });
  await expect(initial).toBeFocused();
  await initial.press("@");
  const recipients = page.getByRole("combobox", { name: "Search Agents" });
  await expect(recipients).toBeFocused();
  await recipients.fill("lean");
  await recipients.press("Enter");
  const input = dock.getByRole("textbox", { name: "Message Lean Reviewer" });
  await dock.getByRole("button", { name: "Explain this proof step." }).click();
  await expect(input).toHaveValue("Explain this proof step.");
  await expect(input).toBeFocused();
  expect(created).toHaveLength(0);
  expect(sends).toHaveLength(0);
  await input.fill("Check the fixture obligation.");
  await page.getByRole("button", { name: "Ask an Agent", exact: true }).click();
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("Check the fixture obligation.");
  await expect(proof).toBeVisible();
  await page.locator("nav.rail").getByRole("link", { name: /^Team/ }).click();
  await expect(input).toHaveValue("Check the fixture obligation.");
  await dock.getByRole("button", { name: "Collapse Agent panel" }).click();
  await expect(dock).toBeHidden();
  await page.getByRole("button", { name: "Agent chat", exact: true }).click();
  await expect(input).toHaveValue("Check the fixture obligation.");
  expect(created).toHaveLength(0);
  expect(previews).toHaveLength(0);

  try {
    await input.press("Enter");
    await expect.poll(() => sends.length).toBe(1);
    await page.locator("nav.rail").getByRole("link", { name: "Home", exact: true }).click();
    await expect(proof).toBeVisible();
    await expect(dock.getByText("Sending…", { exact: true })).toBeVisible();
  } finally {
    accept();
  }
  await expect(dock.getByText("Check the first assumption.", { exact: true })).toBeVisible();
  expect(created).toEqual([
    {
      agent_id: "agent-1",
      title: "Proof obligation 2",
      claim_id: expect.any(String),
      statement_id: expect.any(String),
      node_id: nodeId,
      request_id: expect.any(String),
    },
  ]);
  expect(sends).toEqual([{ ...previews[0], token: "previewed-context", confirm_external: true }]);

  await input.fill("Review this follow-up only when I send it.");
  await dock.getByRole("button", { name: "Message context" }).click();
  await page.getByRole("menuitem", { name: "Request details" }).click();
  await expect(dock.locator(".conversation-request pre")).toHaveText('{"scope":"selected proof node only"}');
  expect(sends).toHaveLength(1);
  await dock.getByRole("link", { name: "Open full conversation" }).click();
  await expect(page.getByText("Check the first assumption.", { exact: true })).toBeVisible();
  await expect(dock).toBeHidden();
  const fullDraft = page.getByRole("textbox", { name: "Message Lean Reviewer" });
  await expect(fullDraft).toHaveValue("Review this follow-up only when I send it.");
  await page.getByRole("button", { name: "Agent chat", exact: true }).click();
  await expect(fullDraft).toHaveCount(1);
  await expect(fullDraft).toBeFocused();
  await expect(dock).toBeHidden();

  await page.locator("nav.rail").getByRole("link", { name: "Home", exact: true }).click();
  await page.getByRole("button", { name: "Agent chat", exact: true }).click();
  await expect(input).toHaveValue("Review this follow-up only when I send it.");
  await dock.getByLabel("Choose an Agent", { exact: true }).click();
  await page.getByRole("option", { name: "Research Agent" }).click();
  await expect(dock.getByRole("textbox", { name: "Message Research Agent" })).toBeEmpty();
  expect(sends).toHaveLength(1);
  expect(created).toHaveLength(1);
  await dock.getByRole("button", { name: "Choose a conversation" }).click();
  await page.getByRole("option", { name: "Proof obligation 2" }).click();
  await expect(input).toHaveValue("Review this follow-up only when I send it.");
  await page.getByRole("button", { name: /^Agent activity/ }).click();
  const task = page.getByRole("link", { name: /Review model assumptions/ });
  await expect(task).toContainText("Lean Reviewer");
  await expect(task).toHaveAttribute("href", "#claims/" + encodeURIComponent("agent-work/" + work.id));
});

for (const hasAgent of [true, false]) {
  test(`empty Chats and dock share a draft ${hasAgent ? "with the sole saved Agent" : "with no saved Agent"}`, async ({
    page,
    request,
  }) => {
    await request.post("/__reset", { data: {} });
    const connection = {
      id: "saved-connection",
      provider: "codex-cli",
      model: "native-default",
      label: "Fixture Codex",
      status: "connected",
      fingerprint: "f".repeat(64),
    };
    const agent: TeamAgent = {
      id: "the-only-agent",
      revision: 1,
      name: "Only Agent",
      connection_id: connection.id,
      description: "",
      avatar: "spark",
      starters: [],
      instructions: "",
    };
    const written: { path: string; body: Record<string, unknown> }[] = [];
    let thread: Conversation | undefined;
    await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: hasAgent ? [agent] : [] } }));
    await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [connection] } }));
    await page.route(/\/api\/conversations(?:[/?]|$)/, (route) => {
      const path = new URL(route.request().url()).pathname;
      const when = "2026-11-22T00:00:00Z";
      if (route.request().method() === "POST") written.push({ path, body: route.request().postDataJSON() });
      if (path.endsWith("/create")) {
        thread = {
          id: "new-test-thread",
          agent_id: agent.id,
          title: "Research conversation",
          claim_id: null,
          statement_id: null,
          node_id: null,
          created_at: when,
          updated_at: when,
          revision: 0,
          running: false,
          messages: [],
        };
        return route.fulfill({ json: { thread } });
      }
      if (path.endsWith("/preview"))
        return route.fulfill({
          json: {
            token: "fixture-token",
            outgoing: "{}",
            provider: connection.provider,
            model: connection.model,
            external: true,
          },
        });
      if (path.endsWith("/send") && thread) {
        thread = {
          ...thread,
          revision: 1,
          messages: [{ id: "reply", role: "assistant", text: "Fixture reply.", at: when }],
        };
        return route.fulfill({ json: { thread } });
      }
      if (path.endsWith("/thread")) return route.fulfill({ json: { thread } });
      return route.fulfill({ json: { threads: thread ? [thread] : [] } });
    });

    await page.goto("/#session=fixture");
    await page.getByRole("button", { name: "Agent chat", exact: true }).click();
    const dock = page.getByRole("region", { name: "Agent chat", exact: true });
    const textbox = page.getByRole("textbox", { name: hasAgent ? "Message Only Agent" : "Write a message" });
    await expect(textbox).toBeVisible();
    if (hasAgent) await expect(dock.getByLabel("Choose an Agent", { exact: true })).toContainText("Only Agent");
    else {
      await expect(dock.getByRole("link", { name: "Add an Agent" })).toHaveAttribute("href", "#team/agents");
      await expect(dock.getByRole("button", { name: "Send message" })).toBeDisabled();
    }
    const starter = "Help me plan a research task.";
    await dock.getByRole("button", { name: starter }).click();
    await expect(textbox).toHaveValue(starter);
    expect(written).toHaveLength(0);

    await page
      .locator("nav.rail")
      .getByRole("link", { name: /^Chats/ })
      .click();
    await expect(dock).toBeHidden();
    await expect(textbox).toHaveCount(1);
    await expect(textbox).toHaveValue(starter);
    if (!hasAgent) {
      await textbox.press("Enter");
      expect(written).toHaveLength(0);
    } else {
      await textbox.press("Enter");
      await expect(page.getByText("Fixture reply.")).toBeVisible();
      expect(written.map(({ path }) => path)).toEqual([
        "/api/conversations/create",
        "/api/conversations/preview",
        "/api/conversations/send",
      ]);
      expect(written[0]?.body).toMatchObject({ agent_id: agent.id });
      expect(written[0]?.body).not.toHaveProperty("claim_id");
      expect(written[2]?.body).toMatchObject({ text: starter, token: "fixture-token", confirm_external: true });
    }
  });
}
