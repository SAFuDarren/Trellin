import { expect, test, type Page } from "@playwright/test";

// Parity checklist from docs/roadmap.md (P1.4) and the section pages (P1.5),
// run against tests/fixtures/workbench_fixture.py.

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

async function open(page: Page, section?: string) {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#session=fixture");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("自然数加零的对称性");
  if (section) await railLink(page, section).click();
  return errors;
}

const card = (page: Page, name: RegExp) =>
  page.locator(".graph-node", { has: page.locator(".node-title", { hasText: name }) });
const rail = (page: Page) => page.locator("nav.rail");
// Rail links include their badge count in the accessible name ("Review 2 waiting for review").
const railLink = (page: Page, name: string) => rail(page).getByRole("link", { name: new RegExp(`^${name}( |$)`) });

test("the interface starts in English", async ({ page }) => {
  await open(page);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("button", { name: "Proof", exact: true })).toBeVisible();
});

test("claims, graph, live work, legend and details load without errors", async ({ page }) => {
  const errors = await open(page);
  await expect(page.locator(".claim")).toHaveCount(5);
  await expect(page.locator(".graph-node")).toHaveCount(7);
  await expect(page.locator(".graph-edge")).toHaveCount(7);
  const work = card(page, /^Proof obligation 2$/).locator(".work");
  await expect(work.locator(".work-text")).toHaveText("GPT-5 Codex");
  await expect(work.locator(".work-count")).toHaveText("×2");
  await expect(page.locator(".legend")).toContainText("Running");
  // The legend's dividers stay thin lines; a page style once turned them into cards.
  expect((await page.locator(".legend .rule").first().boundingBox())?.width ?? 99).toBeLessThan(4);
  await expect(page.locator(".atlas")).toBeVisible();
  await expect(page.locator(".node-detail h2")).toHaveText("reverseZeroAdd");
  expect(errors).toEqual([]);
});

test("selecting a node shows its details and conversation", async ({ page }) => {
  await open(page);
  await card(page, /^Proof obligation 2$/).click();
  await expect(page.locator(".node-detail h2")).toHaveText("Proof obligation 2");
  await expect(page.locator(".node-detail")).toContainText("Blocked:");
  await expect(page.locator("#chat-target")).toHaveText("Proof obligation 2");
  await expect(page.locator(".chat-feed .live")).toHaveCount(2);
  await expect(page.locator("#guidance-audience option").first()).toHaveText(/^[A-Z0-9]+-\d\d · Local$/);
  await page.getByRole("button", { name: "All nodes" }).click();
  await expect(page.locator("#chat-target")).toHaveText("Whole claim");
});

test("the impact view previews what withdrawing an assumption affects", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "If withdrawn" }).click();
  await expect(page.locator(".impact-bar")).toContainText("Preview only");
  await expect(page.locator("#queue-title")).toHaveText("Would be affected");
  await expect(page.locator(".queue-item")).toHaveCount(4);
  await expect(page.locator('.graph-node[data-affected="true"]')).toHaveCount(4);
});

test("sending a note to Codex takes two explicit clicks", async ({ page }) => {
  await open(page);
  await card(page, /^Proof obligation 2$/).click();
  await page.locator("#guidance-audience").selectOption("codex");
  await page.locator("#guidance-text").fill("Try the reduction route first.");
  const send = page.locator("#guidance-send");
  await send.click();
  await expect(send).toHaveAccessibleName("Send to Codex");
  await expect(page.locator("#guidance-status")).toContainText("Click again to send");
  await expect(page.locator(".chat-feed")).not.toContainText("Try the reduction route first.");
  await send.click();
  await expect(page.locator("#guidance-status")).toHaveText("Saved. Codex will read it on its next run.");
  await expect(page.locator(".chat-feed")).toContainText("Try the reduction route first.");
});

test("zoom, splitters and sidebar are remembered", async ({ page }) => {
  await open(page);
  const level = page.locator(".zoom-level");
  const before = await level.textContent();
  await page.getByRole("button", { name: "Zoom in" }).click();
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

test("every rail section opens without errors", async ({ page }) => {
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
    await railLink(page, link).click();
    await expect(page.locator("#section-title")).toHaveText(heading);
  }
  await railLink(page, "Workbench").click();
  await expect(page.locator(".graph-node")).toHaveCount(7);
  expect(errors).toEqual([]);
});

test("the rail shows review and running counts, with help and settings at the bottom", async ({ page }) => {
  await open(page);
  // The rail sits beside the page, not above it.
  const railBox = await rail(page).boundingBox();
  const sidebarBox = await page.locator(".sidebar").boundingBox();
  expect(railBox?.height ?? 0).toBeGreaterThan(800);
  expect(sidebarBox?.x ?? 0).toBeGreaterThanOrEqual((railBox?.x ?? 0) + (railBox?.width ?? 0) - 1);
  await expect(rail(page).getByLabel("2 waiting for review")).toBeVisible();
  await expect(rail(page).getByLabel("2 running")).toBeVisible();
  const settings = await railLink(page, "Settings").boundingBox();
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
  await expect(rail(page).getByLabel("1 waiting for review")).toBeVisible();
  await expect(page.locator(".page-head h2")).not.toHaveText("reverseZeroAdd");
});

test("a result can be opened from review in the workbench", async ({ page }) => {
  await open(page, "Review");
  await page.getByRole("button", { name: "Open in workbench" }).click();
  await expect(page.locator(".node-detail h2")).toHaveText("reverseZeroAdd");
});

test("Team shows model management at the local runtime", async ({ page }) => {
  const errors = await open(page, "Team");
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
  await railLink(page, "工作台").click();
  await expect(page.getByRole("button", { name: "证明进展" })).toBeVisible();
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
