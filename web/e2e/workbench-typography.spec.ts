import { expect, test, type Locator, type Page } from "@playwright/test";
import type { Conversation, ConversationSummary } from "../src/api/conversations";
import type { GraphSnapshot } from "../src/workbench/types";
import { openProof } from "./project-map";

const roles = {
  page: ["22px", "600"],
  heading: ["15px", "600"],
  body: ["14px", "400"],
  control: ["13px", "500"],
  metadata: ["12px", "400"],
} as const;

async function expectTextRole(elements: Locator[], role: keyof typeof roles) {
  const [size, weight] = roles[role];
  for (const element of elements) {
    await expect(element).toHaveCSS("font-size", size);
    await expect(element).toHaveCSS("font-weight", weight);
  }
}

async function moveDivider(page: Page, divider: Locator, distance: number) {
  const bounds = await divider.boundingBox();
  if (!bounds) throw new Error("Divider is not visible");
  const x = bounds.x + bounds.width / 2;
  const y = bounds.y + bounds.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + distance, y, { steps: 5 });
  await page.mouse.up();
}

async function expectWrapped(element: Locator) {
  expect(await element.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
}

for (const variant of [
  {
    language: "en",
    width: 1440,
    map: "Project map",
    chats: "Chats",
    activity: "Proof activity",
    zoom: "Zoom in",
    openProof: /^Open proof$/,
    received: "Included in a local proof attempt",
    replied: "Replied",
    title: "Natural-number addition in a workspace with a deliberately long claim and conversation title",
  },
  {
    language: "zh-CN",
    width: 860,
    map: "项目图谱",
    chats: "对话",
    activity: "证明活动",
    zoom: "放大",
    openProof: /^打开证明$/,
    received: "已纳入本机证明尝试",
    replied: "已回复",
    title: "自然数加法的证明研究与工作台长标题换行测试用于检查对话和证明活动在紧凑窗口中的布局",
  },
] as const) {
  test(`text roles and proof content remain readable in ${variant.language} at ${variant.width}px`, async ({
    page,
    request,
  }) => {
    await request.post("/__reset", { data: {} });
    await page.setViewportSize({ width: variant.width, height: 720 });
    await page.addInitScript(
      (language) => localStorage.setItem("research-multiplier-language", language),
      variant.language,
    );

    const claimId = "11111111-1111-4111-8111-111111111111";
    const statementId = "22222222-2222-4222-8222-222222222222";
    const goalId = `goal:${claimId}`;
    const obligationId = "obligation:55555555-5555-4555-8555-555555555555:2";
    const date = "2026-11-20T02:00:00Z";
    const identifier = "LongMathematicalIdentifier".repeat(10);
    const thread: ConversationSummary = {
      id: "abcdefab-1000-4000-8000-000000000001",
      title: variant.title,
      claim_id: claimId,
      statement_id: statementId,
      node_id: goalId,
      created_at: date,
      updated_at: date,
      running: false,
      revision: 1,
      latest_turn: {
        provider: "codex-cli",
        requested_model: "gpt-fixture",
        model: null,
        preview: variant.title,
        at: date,
        status: "answered",
      },
    };
    const conversation: Conversation = {
      ...thread,
      messages: [
        { id: "request", role: "user", text: variant.title, at: date },
        { id: "reply", role: "assistant", text: variant.title + "\n" + identifier, at: date },
      ],
    };
    await page.route("**/api/graph?*", async (route) => {
      const response = await route.fetch();
      const graph = (await response.json()) as GraphSnapshot;
      graph.claim.title = variant.title;
      const goal = graph.nodes.find((node) => node.id === goalId);
      if (goal) {
        goal.title = variant.title;
        goal.formalization = (goal.formalization ?? "") + "\n-- " + identifier;
      }
      await route.fulfill({ response, json: graph });
    });
    await page.route(/\/api\/conversations(?:[/?]|$)/, (route) =>
      route.fulfill({
        json: new URL(route.request().url()).pathname.endsWith("/thread")
          ? { thread: conversation }
          : { threads: [thread] },
      }),
    );
    await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [] } }));
    await page.route("**/api/project-intake/models", (route) => route.fulfill({ json: { models: [] } }));

    await page.goto("/#session=fixture");
    await expect(page.locator("html")).toHaveAttribute("lang", variant.language);
    const mapTitle = page.getByRole("main").getByRole("heading", { name: variant.map, exact: true });
    await expectTextRole([mapTitle], "page");
    await openProof(page, undefined, variant.openProof);
    const goalCard = page.locator(`.graph-node[data-node="${goalId}"]`);
    await goalCard.press("Enter");
    await expect(page.locator("#node-detail h2")).toHaveText(variant.title);
    await expectTextRole([page.locator(".proof-path h1")], "page");

    const inspector = page.locator(".node-detail");
    await expectTextRole([inspector.locator("h2")], "page");
    const formula = inspector.locator(".formula");
    const lean = inspector.locator(".lean");
    await expect(formula).toContainText("∀ n, n = 0 + n");
    await expect(formula).toHaveCSS("font-family", /STIX Two Text/);
    await expect(lean).toContainText(identifier);
    await expect(lean).toHaveCSS("font-family", /monospace/);
    await expect(lean).toHaveCSS("white-space", "pre-wrap");

    const inspectorDivider = page.locator('.splitter[data-split="right"]');
    if (variant.width >= 900) {
      await moveDivider(page, inspectorDivider, 48);
      await expect(inspectorDivider).toHaveAttribute("aria-valuenow", "292");
    } else {
      await expect(inspectorDivider).toBeHidden();
      await expect(page.locator("#review")).toHaveCSS("position", "absolute");
    }
    await expectWrapped(lean);
    await expectWrapped(inspector.locator("h2"));

    const plot = page.locator(".main-proof-graph .plot");
    const canvasScale = () => plot.evaluate((node) => new DOMMatrixReadOnly(getComputedStyle(node).transform).a);
    const beforeZoom = await canvasScale();
    await page.locator(".main-proof-graph").getByRole("button", { name: variant.zoom, exact: true }).click();
    await expect.poll(canvasScale).toBeGreaterThan(beforeZoom);
    await expectTextRole([page.locator(".proof-path h1")], "page");

    await page.locator(`.graph-node[data-node="${obligationId}"]`).press("Enter");
    const proof = page.locator(".chat");
    const proofTab = page.locator("#review summary").filter({ hasText: variant.activity });
    await proofTab.click();
    const note = proof.locator(".guidance-note-status");
    await expect(note).toContainText(variant.received);
    await expectTextRole([proof.locator(".chat-head h2")], "heading");
    await expectTextRole([proof.locator("textarea"), proof.locator(".bubble")], "body");
    await expectTextRole([proof.locator("#guidance-send")], "control");
    await expectTextRole([note], "metadata");
    await proof.locator("#guidance-send").scrollIntoViewIfNeeded();
    await expect(proof.locator("#guidance-send")).toBeInViewport();

    if (variant.width < 900) {
      await page.setViewportSize({ width: 640, height: 720 });
      const tabs = page.locator(".proof-lenses");
      await tabs.scrollIntoViewIfNeeded();
      await expect
        .poll(() =>
          tabs.evaluate((element) => {
            const stage = element.closest(".proof-surface")!.getBoundingClientRect();
            return [...element.querySelectorAll("button")].every((button) => {
              const { left, right, top, bottom } = button.getBoundingClientRect();
              return left >= stage.left && right <= stage.right && top >= stage.top && bottom <= stage.bottom;
            });
          }),
        )
        .toBe(true);
    }
    await page.locator("nav.rail").getByRole("link", { name: variant.chats, exact: true }).click();
    const chats = page.locator(".chats-page");
    const row = chats.locator(".chat-list-item").first();
    const status = row.locator(".chat-list-status");
    await expect(status).toHaveText(variant.replied);
    await expect(status).toBeInViewport();
    await expectTextRole([chats.locator(".section-nav-header h1")], "heading");
    await expectTextRole([status], "metadata");
    await row.click();
    const main = chats.locator(".chats-main");
    const reply = main.locator(".message");
    await expect(reply).toContainText(identifier);
    await expectTextRole([main.locator(".page-head h2")], "page");
    await expectTextRole([main.locator("textarea"), reply, main.locator(".bubble")], "body");
    await expectTextRole([main.locator(".composer-model-trigger").first()], "control");
    await expectTextRole([main.locator(".speaker").last(), main.locator(".page-head .note")], "metadata");
    await expectWrapped(reply);
    await expect(main.locator("textarea")).toBeInViewport();
  });
}
