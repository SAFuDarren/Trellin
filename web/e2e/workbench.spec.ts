import { expect, test, type Locator, type Page } from "@playwright/test";
import { openMenu } from "./navigation";
import { openProof } from "./project-map";

// Parity checklist from docs/roadmap.md (P1.4) and the section pages (P1.5),
// run against tests/fixtures/workbench_fixture.py.

test.beforeEach(async ({ request, page }) => {
  await request.post("/__reset", { data: {} });
  await page.route(/\/api\/conversations(?:\?|$)/, (route) => route.fulfill({ json: { threads: [] } }));
});

async function open(page: Page, section?: string) {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#session=fixture");
  await openProof(page);
  await expect(page.locator(".main-proof-graph")).toBeVisible();
  if (section) await goToSection(page, section);
  return errors;
}

const card = (page: Page, name: RegExp) =>
  page.locator(".graph-node", { has: page.locator(".node-title", { hasText: name }) });
const rail = (page: Page) => page.locator("nav.rail");
const railLink = (page: Page, name: string) => rail(page).getByRole("link", { name: new RegExp(`^${name}( |$)`) });

const viewMenu = (page: Page) => openMenu(page.getByRole("button", { name: "Switch view", exact: true }));
const utilityButton = (page: Page) => rail(page).getByRole("button", { name: "Settings and help", exact: true });

async function confirmImeCandidate(input: Locator) {
  await input.dispatchEvent("compositionstart");
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
  await input.dispatchEvent("compositionend");
}

async function goToSection(page: Page, section: string) {
  if (section === "Review") {
    const menu = await viewMenu(page);
    await menu.getByRole("menuitem", { name: /^Review/ }).click();
  } else if (section === "Settings" || section === "Help") {
    const menu = await openMenu(utilityButton(page));
    await menu.getByRole("menuitem", { name: section, exact: true }).click();
  } else {
    await railLink(page, section).click();
  }
}

test("the interface starts in English", async ({ page }) => {
  await open(page);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("button", { name: "Proof", exact: true })).toBeVisible();
});

test("claims, graph, live work, legend and details load without errors", async ({ page }) => {
  const errors = await open(page);
  await expect(page.locator(".graph-node")).toHaveCount(7);
  await expect(page.locator(".graph-edge")).toHaveCount(7);
  const work = card(page, /^Proof obligation 2$/).locator(".work");
  await expect(work).toHaveText("2");
  await expect(work).toHaveAttribute("title", "Codex · GPT-5 Codex ×2");
  await expect(work.locator("svg")).toBeVisible();
  const counts = page.getByRole("status", { name: "Proof status counts" });
  await expect(counts).toContainText("Running");
  await expect(counts).toContainText("Queued");
  expect(await counts.evaluate((row) => row.scrollWidth <= row.clientWidth)).toBe(true);
  const edgeStyles = page.getByRole("button", { name: "Edge styles" });
  const edgeLegend = page.getByRole("dialog", { name: "Edge styles" });
  await expect(edgeStyles).toHaveAttribute("aria-expanded", "false");
  await edgeStyles.click();
  await expect(edgeLegend.locator("i")).toHaveCount(3);
  await expect(counts).toBeVisible();
  expect(
    await counts
      .locator(":scope > span")
      .evaluateAll((labels) => new Set(labels.map((label) => label.getBoundingClientRect().y)).size),
  ).toBe(1);
  await page.keyboard.press("Escape");
  await expect(edgeLegend).toBeHidden();
  await expect(edgeStyles).toBeFocused();
  await expect(page.locator(".node-detail h2")).toHaveText("自然数加零的对称性");
  expect(errors).toEqual([]);
});

test("selecting a node shows its details and conversation", async ({ page }) => {
  await open(page);
  await card(page, /^Proof obligation 2$/).click();
  await page
    .locator("summary")
    .filter({ hasText: /^Proof activity$/ })
    .click();
  await expect(page.locator(".node-detail h2")).toHaveText("Proof obligation 2");
  await expect(page.locator(".node-detail")).toContainText("Blocked:");
  await expect(page.locator("#node-detail > .facts")).toContainText("Codex · GPT-5 Codex ×2");
  await expect(page.getByRole("region", { name: "Proof activity" }).getByRole("heading")).toContainText(
    "Proof obligation 2",
  );
  await expect(page.locator(".chat-feed .live")).toHaveCount(2);
  await expect(page.locator("#guidance-audience")).toHaveText(/^[A-Z0-9]+-\d\d · Local$/);
  await page.getByRole("button", { name: "All nodes" }).click();
  await expect(page.getByRole("region", { name: "Proof activity" }).getByRole("heading")).toHaveText("Whole claim");
});

test("the impact view previews what withdrawing an assumption affects", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "If withdrawn" }).click();
  await expect(page.locator(".impact-bar")).toContainText("Preview only");
  await expect(page.locator("#queue-title")).toHaveText("Would be affected");
  await expect(page.locator(".queue-item")).toHaveCount(4);
  await expect(page.locator('.graph-node[data-affected="true"]')).toHaveCount(4);
});

test("queues a Codex proof note with one action, retaining a failed note and showing its persisted receipt", async ({
  page,
}) => {
  const sent: Record<string, unknown>[] = [];
  let finishFirstRequest = () => {};
  const waitForRetryCheck = new Promise<void>((resolve) => {
    finishFirstRequest = resolve;
  });
  await page.route("**/api/guidance", async (route) => {
    sent.push(route.request().postDataJSON());
    if (sent.length === 1) {
      await waitForRetryCheck;
      return route.fulfill({ status: 503, json: { error: "fixture guidance unavailable" } });
    }
    return route.continue();
  });

  await open(page);
  await card(page, /^Proof obligation 2$/).click();
  await page
    .locator("summary")
    .filter({ hasText: /^Proof activity$/ })
    .click();
  await page.locator("#guidance-audience").click();
  await page.getByRole("option", { name: /Codex/ }).click();
  const firstLine = "先检查陈述。";
  const secondLine = "Try the reduction route first.";
  const note = firstLine + "\n" + secondLine;
  const input = page.getByRole("textbox", { name: "Proof opinion" });
  await input.fill(firstLine);
  const queue = page.locator("#guidance-send");
  await expect(queue).toHaveText("Queue proof note");
  await expect(queue).toHaveAccessibleDescription(/later authorized Codex proof attempt/);
  await confirmImeCandidate(input);
  await expect(input).toHaveValue(firstLine);
  await expect(queue).toBeEnabled();
  expect(sent).toHaveLength(0);
  await input.press("End");
  await input.press("Shift+Enter");
  await page.keyboard.insertText(secondLine);
  await expect(input).toHaveValue(note);
  expect(sent).toHaveLength(0);

  try {
    // Dispatch both actions in one frame so a rerender cannot hide a duplicate submission.
    await input.evaluate((textarea: HTMLTextAreaElement) => {
      textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
      textarea.form?.querySelector<HTMLButtonElement>("#guidance-send")?.click();
    });
    await expect.poll(() => sent.length).toBe(1);
    await expect(queue).toBeDisabled();
    await expect(queue).toContainText("Queueing proof note");
  } finally {
    finishFirstRequest();
  }
  expect(sent[0]).toMatchObject({
    text: note,
    audience: "codex",
    confirm_release: true,
    request_id: expect.any(String),
  });
  await expect(page.locator("#guidance-status")).toContainText("fixture guidance unavailable");
  await expect(input).toHaveValue(note);
  const saved = page.locator(".chat-feed > li").filter({ hasText: note });
  await expect(saved).toHaveCount(0);
  await expect(queue).toHaveAccessibleName("Retry proof note");
  await input.press("Enter");
  await expect(page.locator("#guidance-status")).toContainText("for a later authorized Codex proof attempt");
  expect(sent).toEqual([sent[0], sent[0]]);
  const status = saved.getByRole("status");
  await expect(status).toContainText("Saved, waiting for an authorized Codex proof attempt");
  await expect(status).toContainText("Open Proof task");
  await expect(status).not.toContainText("Included");

  await page.route(/\/api\/activity\?claim=/, async (route) => {
    const response = await route.fetch();
    const activity = (await response.json()) as { guidance: { text: string }[] };
    const guidance = activity.guidance.map((record) =>
      record.text === note ? { ...record, submitted_at: "2026-11-20T09:05:00Z" } : record,
    );
    await route.fulfill({ response, json: { ...activity, guidance } });
  });
  await page.reload();
  await openProof(page);
  await card(page, /^Proof obligation 2$/).click();
  await page
    .locator("summary")
    .filter({ hasText: /^Proof activity$/ })
    .click();
  await expect(status).toContainText("Included in a Codex proof attempt");
  await expect(status).not.toContainText("Open Proof task");
});

test("a blocked proof node keeps a private draft across reopen", async ({ page }) => {
  const drafts = new Map<string, { text: string; revision: string; updated_at: string }>();
  await page.route(/\/api\/activity\?claim=/, async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: {
        ...(await response.json()),
        guidance_channels: [],
        guidance_blocker: "scope",
        proof_draft_available: true,
      },
    });
  });
  await page.route(/\/api\/proof-drafts(?:[/?]|$)/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET") {
      await route.fulfill({ json: { draft: drafts.get(url.searchParams.get("node_id") ?? "") ?? null } });
      return;
    }
    const body = request.postDataJSON() as { node_id: string; expected_revision: string | null; text: string };
    expect(body.expected_revision).toBe(drafts.get(body.node_id)?.revision ?? null);
    if (url.pathname.endsWith("/discard")) {
      drafts.delete(body.node_id);
    } else {
      drafts.set(body.node_id, { text: body.text, revision: crypto.randomUUID(), updated_at: "2026-11-20T09:05:00Z" });
    }
    await route.fulfill({ json: { draft: drafts.get(body.node_id) ?? null } });
  });
  const sent: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/guidance") sent.push(request.method());
  });

  const select = async () => {
    await card(page, /^Proof obligation 2$/).click();
    await page
      .locator("summary")
      .filter({ hasText: /^Proof activity$/ })
      .click();
  };
  await open(page);
  await select();
  const note = "Check the statement before trying this lemma.";
  const input = page.getByRole("textbox", { name: "Proof opinion" });
  await expect(page.locator("#guidance-status")).toContainText("Review and confirm the Lean statement");
  await expect(page.getByRole("button", { name: "Queue proof note" })).toHaveCount(0);
  await input.fill(note);
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.locator("#guidance-draft")).toHaveText("Saved in this project · Not queued");
  await expect(page.locator(".chat-feed")).not.toContainText(note);

  await page.reload();
  await openProof(page);
  await select();
  await expect(input).toHaveValue(note);
  await page.getByRole("button", { name: "Discard saved draft" }).click();
  await expect(input).toHaveValue("");
  await expect(page.locator("#guidance-draft")).toHaveText("Saved draft discarded");
  expect(drafts.size).toBe(0);
  expect(sent).toEqual([]);
});

test("zoom, splitters and sidebar are remembered", async ({ page }) => {
  await open(page);
  await page.locator(".proof-return").click();
  const canvas = page.getByRole("application", { name: "Project map", exact: true });
  const level = canvas.locator(".zoom-level");
  const before = await level.textContent();
  await canvas.getByRole("button", { name: "Zoom in" }).click();
  await expect(level).not.toHaveText(before ?? "");
  const splitter = page.locator('.splitter[data-split="side"]');
  await splitter.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(splitter).toHaveAttribute("aria-valuenow", "272");
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await page.reload();
  await expect(page.locator("#workbench")).toHaveAttribute("data-sidebar", "collapsed");
  await expect(page.locator('.splitter[data-split="side"]')).toHaveAttribute("aria-valuenow", "272");
});

test("the global map can be moved and resized", async ({ page }) => {
  await open(page);
  await page.locator(".proof-return").click();
  await page.getByRole("button", { name: "Global map", exact: true }).click();
  const caption = page.locator(".atlas figcaption");
  const start = await caption.boundingBox();
  if (!start) throw new Error("atlas not visible");
  await page.mouse.move(start.x + 20, start.y + 5);
  await page.mouse.down();
  await page.mouse.move(start.x + 120, start.y + 105, { steps: 5 });
  await page.mouse.up();
  const moved = await caption.boundingBox();
  expect(Math.round((moved?.x ?? 0) - start.x)).toBeGreaterThan(80);
  const resize = page.locator(".atlas-resize");
  await resize.focus();
  const width = (await page.locator(".atlas").boundingBox())?.width ?? 0;
  await page.keyboard.press("ArrowRight");
  await expect.poll(async () => (await page.locator(".atlas").boundingBox())?.width ?? 0).toBeGreaterThan(width);
});

test("graph controls and Help retain accessible structure", async ({ page }) => {
  await open(page);
  const inspect = page.locator("#node-detail");
  const nodes = page.locator('.graph-node[aria-controls="node-detail"]');
  await expect(nodes).toHaveCount(7);
  await expect(inspect).toBeVisible();
  await page.locator(".proof-return").click();
  const map = page.getByRole("application", { name: "Project map", exact: true });
  await page.getByRole("button", { name: "Collapse review column" }).click();
  await expect(inspect).toBeHidden();
  await expect(map).toBeVisible();
  await page.getByRole("button", { name: "Global map", exact: true }).click();

  const resize = page.getByRole("separator", { name: "Drag to resize the overview" });
  const width = () => page.locator(".atlas").evaluate((map) => Math.round(map.getBoundingClientRect().width));
  await expect(resize).toHaveAttribute("aria-valuemin", "110");
  await expect(resize).toHaveAttribute("aria-valuemax", "420");
  const initialWidth = await width();
  await expect(resize).toHaveAttribute("aria-valuenow", String(initialWidth));
  await resize.press("ArrowRight");
  await expect.poll(width).toBeGreaterThan(initialWidth);
  await expect(resize).toHaveAttribute("aria-valuenow", String(await width()));

  await page.setViewportSize({ width: 360, height: 560 });
  await expect
    .poll(async () => {
      const stageWidth = await map.evaluate((stage) => stage.clientWidth);
      const maximumWidth = Number(await resize.getAttribute("aria-valuemax"));
      return maximumWidth < 420 && maximumWidth === Math.max(110, stageWidth - 24);
    })
    .toBe(true);
  expect(Number(await resize.getAttribute("aria-valuenow"))).toBeLessThanOrEqual(
    Number(await resize.getAttribute("aria-valuemax")),
  );

  await goToSection(page, "Help");
  const definitions = page.locator(".help dl.keys");
  await expect(definitions).toHaveCount(2);
  await expect(definitions.locator(":scope > div > dt + dd")).toHaveCount(9);
});

test("every section opens through the rail or a navigation menu without errors", async ({ page }) => {
  const errors = await open(page);
  const sections: [string, string][] = [
    ["Review", "Review"],
    ["Claims", "Claims"],
    ["Library", "Library"],
    ["Chats", "Chats"],
    ["Team", "Team"],
    ["Automation", "Automation"],
    ["Help", "Help"],
    ["Settings", "Settings"],
  ];
  for (const [link, heading] of sections) {
    await goToSection(page, link);
    await expect(page.locator("#section-title")).toHaveText(heading);
  }
  await railLink(page, "Home").click();
  await expect(page.locator(".graph-node")).toHaveCount(7);
  expect(errors).toEqual([]);
});

test("the view menu shows review counts and the rail keeps Team and utilities reachable", async ({ page }) => {
  await open(page);
  // The rail sits beside the page, not above it.
  const railBox = await rail(page).boundingBox();
  const sidebarBox = await page.locator(".sidebar").boundingBox();
  expect(railBox?.height ?? 0).toBeGreaterThan(800);
  expect(sidebarBox?.x ?? 0).toBeGreaterThanOrEqual((railBox?.x ?? 0) + (railBox?.width ?? 0) - 1);
  const menu = await viewMenu(page);
  const review = menu.getByRole("menuitem", { name: /^Review/ });
  await expect(review).toHaveAccessibleName(/2(?: waiting for review)?$/);
  await expect(review.getByText("2", { exact: true })).toBeVisible();
  await expect(rail(page).getByLabel("2 running")).toBeVisible();
  const settings = await utilityButton(page).boundingBox();
  const bar = await rail(page).boundingBox();
  expect((bar?.y ?? 0) + (bar?.height ?? 0) - ((settings?.y ?? 0) + (settings?.height ?? 0))).toBeLessThan(24);
});

test("approving a verified result records it and clears it from the inbox", async ({ page }) => {
  await open(page, "Review");
  await expect(page.locator(".page-head h2")).toHaveText("reverseZeroAdd");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status")).toHaveText("Add your name and a reason first.");
  await page.getByLabel("Your name").fill("Fixture reviewer");
  await page.getByLabel("Reason").fill("Scope matches; only H1 is used.");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.locator(".page-notice")).toHaveText("Approved and recorded.");
  const review = (await viewMenu(page)).getByRole("menuitem", { name: /^Review/ });
  await expect(review).toHaveAccessibleName(/1(?: waiting for review)?$/);
  await expect(review.getByText("1", { exact: true })).toBeVisible();
  await expect(review).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".page-head h2")).not.toHaveText("reverseZeroAdd");
});

test("a result can be opened from review in the workbench", async ({ page }) => {
  await open(page, "Review");
  await page.getByRole("button", { name: "Open in workbench" }).click();
  await expect(page.locator(".node-detail h2")).toHaveText("reverseZeroAdd");
});

test("Team shows model management at the local runtime", async ({ page }) => {
  const errors = await open(page, "Team");
  await page.getByRole("link", { name: "Runtimes", exact: true }).click();
  const connections = page.getByRole("region", { name: "Model connections" });
  await expect(connections.getByText("Add the models you want to use.")).toBeVisible();
  await expect(connections.getByRole("button", { name: "Add connection" })).toBeEnabled();
  await expect(page.getByText("Model services", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the language is chosen in settings and remembered", async ({ page }) => {
  await open(page, "Settings");
  await page.getByRole("button", { name: "简体中文" }).click();
  await expect(page.locator("#section-title")).toHaveText("设置");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await railLink(page, "主页").click();
  await expect(page.getByRole("application", { name: "项目图谱" })).toBeVisible();
});

test("settings show the installed version and offer a manual stable release check", async ({ page }) => {
  const errors = await open(page, "Settings");
  const settings = page.getByRole("region", { name: "Updates" });
  await expect(settings.getByText("Installed version 1.9.0")).toBeVisible();
  await expect(settings.getByRole("checkbox", { name: "Automatically check for updates" })).not.toBeChecked();
  await expect(settings.getByRole("status")).toHaveText("No update check has run yet.");
  await settings.getByRole("button", { name: "Check for updates" }).click();
  const notice = page.getByRole("complementary", { name: "Updates" });
  await expect(notice.getByRole("status")).toHaveText("Version 1.10.0 is available.");
  await expect(notice.getByRole("link", { name: "Release notes" })).toHaveAttribute(
    "href",
    "https://github.com/SAFuDarren/Trellin/releases/tag/v1.10.0",
  );
  await notice.getByRole("button", { name: "Dismiss" }).click();
  await expect(notice).toHaveCount(0);
  await expect(settings.getByRole("link", { name: "Release notes" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("the browser tab icons load", async ({ page, request }) => {
  await open(page);
  const icons = await page
    .locator('link[rel="icon"], link[rel="apple-touch-icon"]')
    .evaluateAll((links) => links.map((link) => (link as HTMLLinkElement).href));
  expect(icons.length).toBe(4);
  for (const icon of icons) {
    const response = await request.get(icon);
    expect(response.status(), icon).toBe(200);
    expect(response.headers()["content-type"]).toMatch(/^image\//);
  }
});

for (const inspect of [false, true]) {
  test(`Chats sends once ${inspect ? "with inspected request details" : "with an on-click preview"} and reopens the conversation`, async ({
    page,
  }) => {
    const createdAt = "2026-11-20T02:00:00Z";
    const id = "12345678-1234-4234-8234-123456789abc";
    const model = {
      id: "claude-choice",
      label: "Claude research",
      fingerprint: "known",
      status: "connected",
      provider: "claude-code",
      model: "sonnet",
    };
    const sent: Record<string, unknown>[] = [];
    const previews: Record<string, unknown>[] = [];
    const archived: Record<string, unknown>[] = [];
    const created: Record<string, unknown>[] = [];
    const story = {
      id,
      title: "Research conversation",
      created_at: createdAt,
      updated_at: createdAt,
      claim_id: null,
      statement_id: null,
      node_id: null,
      running: false,
      revision: 0,
      messages: [] as { id: string; role: string; at: string; text: string; provider?: string; model?: string }[],
    };
    let previewModel = model.model;
    let acceptRequest = () => {};
    const waitForAcceptance = new Promise<void>((resolve) => {
      acceptRequest = resolve;
    });
    const outgoing = JSON.stringify({ system: "Discuss the question.", user: "What structure is missing?" });
    const reply = {
      id: "a",
      role: "assistant",
      provider: model.provider,
      model: model.model,
      at: createdAt,
      text: "Check closure.",
    };
    await page.route("**/api/claims", (route) => route.fulfill({ json: { claims: [], claim_id: null } }));
    await page.route("**/api/project-intake/models", (route) =>
      route.fulfill({ json: { models: ["local-research"] } }),
    );
    await page.route("**/api/model-connections", (route) =>
      route.fulfill({
        json: { connections: [model, { ...model, id: "unavailable", model: "unavailable-model", fingerprint: null }] },
      }),
    );
    await page.route(/\/api\/conversations(?:[/?]|$)/, async (route) => {
      switch (new URL(route.request().url()).pathname) {
        case "/api/conversations/create":
          created.push(route.request().postDataJSON());
          return route.fulfill({ json: { thread: story } });
        case "/api/conversations/preview":
          previews.push(route.request().postDataJSON());
          return route.fulfill({
            json: {
              token: "approved-context",
              provider: model.provider,
              model: previewModel,
              outgoing,
              external: true,
            },
          });
        case "/api/conversations/send":
          sent.push(route.request().postDataJSON());
          if (inspect) await waitForAcceptance;
          story.revision++;
          story.messages.push({ id: "q", role: "user", at: createdAt, text: "What structure is missing?" });
          story.running = inspect;
          if (!inspect) story.messages.push(reply);
          return route.fulfill({ json: { thread: story } });
        case "/api/conversations/archive":
          archived.push(route.request().postDataJSON());
          return route.fulfill({ json: { archived: true } });
        case "/api/conversations/thread":
          return route.fulfill({ json: { thread: story } });
        default:
          return route.fulfill({ json: { threads: created.length && !archived.length ? [story] : [] } });
      }
    });
    await page.goto("/#session=fixture");
    await railLink(page, "Chats").click();
    await page.getByRole("button", { name: "New conversation" }).click();
    await page.getByLabel("Choose an Agent", { exact: true }).click();
    await page.getByRole("option", { name: "Use a model directly", exact: true }).click();
    const picker = page.getByRole("button", { name: "Conversation model" });
    await expect(picker).toHaveText("Choose a model");
    expect(created).toHaveLength(0);
    if (inspect) await page.setViewportSize({ width: 820, height: 720 });
    await picker.press("ArrowDown");
    const pickerMenu = page.getByRole("listbox", { name: "Search models" });
    const localChoice = pickerMenu.getByRole("group", { name: "On this device" }).getByRole("option");
    const remoteChoice = pickerMenu.getByRole("group", { name: "Connected services" }).getByRole("option");
    const modelSearch = page.getByRole("combobox", { name: "Search models" });
    await expect(modelSearch).toBeFocused();
    await expect(modelSearch).toHaveAttribute("aria-activedescendant", (await localChoice.getAttribute("id"))!);
    await expect(remoteChoice).toHaveText(/Claude Code · sonnet/);
    await expect(pickerMenu.getByText("unavailable-model")).toHaveCount(0);
    await page.keyboard.press("ArrowDown");
    await expect(modelSearch).toHaveAttribute("aria-activedescendant", (await remoteChoice.getAttribute("id"))!);
    await page.keyboard.press("Enter");
    await expect(pickerMenu).toBeHidden();
    await expect(picker).toBeFocused();
    await expect(picker).toHaveText(/sonnet/);
    await picker.press("Enter");
    await expect(remoteChoice).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Escape");
    await expect(pickerMenu).toBeHidden();
    await expect(picker).toBeFocused();
    const input = page.getByRole("textbox", { name: "Write a message" });
    await input.fill("What structure is missing?");
    const send = page.getByRole("button", { name: "Send message" });
    await expect(send).toHaveAccessibleDescription("External model · Only this draft will be sent");
    expect(sent).toHaveLength(0);
    expect(previews).toHaveLength(0);
    if (inspect) {
      await confirmImeCandidate(input);
      await expect(input).toHaveValue("What structure is missing?");
      await expect(send).toBeEnabled();
      expect(previews).toHaveLength(0);
      const context = page.getByRole("button", { name: "Message context" });
      await context.press("Enter");
      const contextMenu = page.getByRole("menu", { name: "Message context" });
      await expect(page.locator(".floating-popover").getByText("Only this draft will be sent")).toBeVisible();
      const detailsItem = contextMenu.getByRole("menuitem", { name: "Request details" });
      await expect(detailsItem).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(contextMenu).toBeHidden();
      await expect(page.locator(".conversation-request pre")).toHaveText(outgoing);
      expect(sent).toHaveLength(0);
      expect(previews).toHaveLength(1);
    } else {
      previewModel = "different-model";
      await send.click();
      await expect(page.getByRole("alert")).toHaveText(
        "The message, model or conversation changed. Review what will be sent again.",
      );
      expect(sent).toHaveLength(0);
      previewModel = model.model;
    }
    if (inspect) {
      const feed = page.locator(".conversation-panel .chat-feed");
      const status = feed.getByRole("status");
      try {
        await input.press("Enter");
        await expect(status).toHaveText("Sending…");
      } finally {
        acceptRequest();
      }
      await expect(status).toHaveText("Message sent. Waiting for a reply…");
      await expect(feed.getByText("What structure is missing?")).toBeVisible();
      story.running = false;
      story.revision++;
      story.messages.push(reply);
    } else {
      await send.click();
    }
    await expect(page.getByText("Check closure.")).toBeVisible();
    expect(created).toEqual([{ title: story.title }]);
    expect(previews).toHaveLength(inspect ? 1 : 2);
    expect(sent).toEqual([{ ...previews.at(-1), token: "approved-context", confirm_external: true }]);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.reload();
    await expect(page.getByText("Check closure.")).toBeVisible();
    await page.getByLabel("Conversation actions").click();
    await page.getByRole("menuitem", { name: "Remove conversation" }).click();
    const remove = page.getByRole("dialog", { name: "Remove conversation" });
    await expect(remove.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    expect(archived).toHaveLength(0);
    await remove.getByRole("button", { name: "Confirm removal" }).click();
    await expect(page.getByRole("button", { name: "Help me plan a research task.", exact: true })).toBeVisible();
    expect(archived).toEqual([{ thread_id: id }]);
  });
}

test("Workbench retries one anchored local conversation message by Enter without losing the draft", async ({
  page,
}) => {
  const now = "2026-11-20T02:00:00Z";
  const created: Record<string, unknown>[] = [];
  const sent: Record<string, unknown>[] = [];
  let thread: Record<string, unknown> | null = null;
  await page.route("**/api/project-intake/models", (route) => route.fulfill({ json: { models: ["local-research"] } }));
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [] } }));
  await page.route(/\/api\/conversations(?:[/?]|$)/, async (route) => {
    switch (new URL(route.request().url()).pathname) {
      case "/api/conversations/create": {
        const body = route.request().postDataJSON();
        created.push(body);
        thread = {
          ...body,
          id: "anchored-thread",
          updated_at: now,
          created_at: now,
          running: false,
          revision: 0,
          messages: [],
        };
        return route.fulfill({ json: { thread } });
      }
      case "/api/conversations/thread":
        return route.fulfill({ json: { thread } });
      case "/api/conversations/preview":
        return route.fulfill({
          json: {
            token: "local-reviewed",
            provider: "ollama-native",
            model: "local-research:local",
            external: false,
            outgoing: "{}",
          },
        });
      case "/api/conversations/send": {
        const body = route.request().postDataJSON();
        sent.push(body);
        if (sent.length === 1) {
          return route.fulfill({ status: 503, json: { error: { code: "conversation.unavailable" } } });
        }
        thread = {
          ...thread,
          messages: [{ id: "local-answer", role: "assistant", text: "Local reply.", at: now }],
          revision: 1,
        };
        return route.fulfill({ json: { thread } });
      }
      default:
        return route.fulfill({ json: { threads: thread ? [thread] : [] } });
    }
  });
  await open(page);
  const node = card(page, /^Proof obligation 2$/);
  await node.click();
  await page.getByRole("button", { name: "Ask an Agent" }).click();
  await page.getByLabel("Choose an Agent", { exact: true }).click();
  await page.getByRole("option", { name: "Use a model directly", exact: true }).click();
  const picker = page.getByRole("button", { name: "Conversation model" });
  const pickerMenu = page.getByRole("listbox", { name: "Search models" });
  await picker.click();
  await page.getByRole("textbox", { name: "Write a message" }).click();
  await expect(pickerMenu).toBeHidden();
  await picker.click();
  await pickerMenu.getByRole("option", { name: "Local · local-research" }).click();
  expect(created).toHaveLength(0);
  const input = page.getByRole("textbox", { name: "Write a message" });
  const question = "Can this obligation be reduced?";
  await input.fill(question);
  const send = page.getByRole("button", { name: "Send message" });
  await expect(send).toHaveAccessibleDescription("On this device · Context · Proof obligation 2");
  await input.press("Enter");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(input).toHaveValue(question);
  await input.press("Enter");
  await expect(page.getByText("Local reply.")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(created).toEqual([
    {
      title: "Proof obligation 2",
      claim_id: expect.any(String),
      statement_id: expect.any(String),
      node_id: await node.getAttribute("data-node"),
    },
  ]);
  expect(sent[0]).toEqual({
    thread_id: "anchored-thread",
    connection_id: "local:local-research",
    text: question,
    request_id: expect.any(String),
    token: "local-reviewed",
    confirm_external: false,
  });
  expect(sent).toEqual([sent[0], sent[0]]);
});
