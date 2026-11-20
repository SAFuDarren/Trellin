import { expect, test } from "@playwright/test";

test("Chats shows recorded recipients and statuses, keeps the input visible, and preserves reading position", async ({
  page,
}) => {
  const at = "2026-11-20T02:00:00Z";
  const claim = "11111111-1111-4111-8111-111111111111";
  const first = "aaa00000-0000-4000-8000-000000000001";
  const second = "aaa00000-0000-4000-8000-000000000002";
  const empty = "aaa00000-0000-4000-8000-000000000003";
  const shared = { created_at: at, updated_at: at, running: false, revision: 1 };
  const chats = [
    {
      ...shared,
      id: first,
      title: "Check closure",
      claim_id: claim,
      statement_id: "22222222-2222-4222-8222-222222222222",
      node_id: `goal:${claim}`,
      latest_turn: {
        provider: "claude-code",
        requested_model: "sonnet",
        model: "claude-sonnet-fixture",
        preview: "The image is closed under composition.",
        at,
        status: "answered",
      },
    },
    {
      ...shared,
      id: second,
      title: "Test an alternative",
      claim_id: null,
      statement_id: null,
      node_id: null,
      latest_turn: {
        provider: "codex-cli",
        requested_model: "gpt-fixture",
        model: null,
        preview: "Compare the two cases.",
        at,
        status: "failed",
      },
    },
    {
      ...shared,
      id: empty,
      title: "Unsent conversation",
      claim_id: null,
      statement_id: null,
      node_id: null,
      latest_turn: null,
    },
  ];
  const history = Array.from({ length: 40 }, (_, index) => ({
    id: "turn-" + index,
    text: `Research note ${index + 1}.\n${"Synthetic mathematical discussion. ".repeat(5)}`,
    role: index % 2 ? "assistant" : "user",
    at,
  }));
  const readIds: string[] = [];
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [] } }));
  await page.route("**/api/project-intake/models", (route) => route.fulfill({ json: { models: [] } }));
  await page.route(/\/api\/conversations(?:[/?]|$)/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/thread")) {
      const id = url.searchParams.get("thread_id");
      readIds.push(id ?? "");
      const thread = chats.find((item) => item.id === id);
      return route.fulfill({ json: { thread: { ...thread, messages: id === first ? history : [] } } });
    }
    return route.fulfill({ json: { threads: chats } });
  });
  await page.clock.install();
  await page.goto("/#session=fixture");
  await page.getByRole("link", { name: "Chats", exact: true }).click();
  const list = page.getByRole("navigation", { name: "Chats" });
  await expect(list.getByRole("button", { name: "New conversation" })).toBeVisible();
  const firstRow = list.getByRole("link", { name: /Check closure/ });
  await expect(firstRow).toHaveAttribute("aria-current", "page");
  await expect(firstRow).toContainText("Claude Code · claude-sonnet-fixture");
  await expect(firstRow).toContainText("The image is closed under composition.");
  await expect(firstRow).toContainText("Replied");
  await expect(firstRow.locator("time")).toHaveAttribute("datetime", at);
  const secondRow = list.getByRole("link", { name: /Test an alternative/ });
  await expect(secondRow).toContainText("Codex · gpt-fixture");
  await expect(secondRow).toContainText("Send failed");
  const emptyRow = list.getByRole("link", { name: /Unsent conversation/ });
  await expect(emptyRow).toContainText("No messages yet.");
  expect(readIds).toEqual([first]);

  const main = page.locator(".chats-main");
  const feed = main.locator(".chat-feed");
  const composer = main.locator(".direct-composer");
  await expect(main.getByText("Linked to a proof node")).toBeVisible();
  await expect(composer).toBeInViewport();
  await expect.poll(() => feed.evaluate((node) => node.scrollHeight - node.clientHeight)).toBeGreaterThan(300);
  await feed.evaluate((node) => {
    node.scrollTop = 0;
    node.dispatchEvent(new Event("scroll"));
  });
  history.push({ id: "latest", text: "A newly arriving reply.", role: "assistant", at });
  await page.clock.fastForward(8100);
  await expect(feed.getByText("A newly arriving reply.")).toBeAttached();
  expect(await feed.evaluate((node) => node.scrollTop)).toBe(0);
  await expect(composer).toBeInViewport();
  await main.getByLabel("Conversation actions").click();
  await expect(main.getByRole("button", { name: "Remove conversation" })).toBeVisible();
  await expect(main.getByRole("button", { name: "Open in workbench" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(main.getByRole("button", { name: "Remove conversation" })).toBeHidden();

  await page.setViewportSize({ width: 640, height: 720 });
  await secondRow.click();
  await expect(list).toBeHidden();
  await expect(main.getByText("Project conversation")).toBeVisible();
  await main.getByRole("link", { name: "Back to chats" }).click();
  await expect(list).toBeVisible();
  await expect(main).toBeHidden();
});
