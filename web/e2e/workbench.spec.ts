import { expect, test, type Page } from "@playwright/test";

// Parity checklist from docs/roadmap.md (P1.4), run against tests/fixtures/workbench_fixture.py.

async function open(page: Page) {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/app/#session=fixture");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("自然数加零的对称性");
  return errors;
}

const card = (page: Page, name: RegExp) =>
  page.locator(".graph-node", { has: page.locator(".node-title", { hasText: name }) });

test("claims, graph, live work, legend and details load without errors", async ({ page }) => {
  const errors = await open(page);
  await expect(page.locator(".claim")).toHaveCount(5);
  await expect(page.locator(".graph-node")).toHaveCount(7);
  await expect(page.locator(".graph-edge")).toHaveCount(7);
  await expect(card(page, /^Proof obligation 2$/).locator(".work")).toContainText("gpt-5-codex ×2");
  await expect(page.locator(".legend")).toContainText("Running");
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
  await page.getByRole("button", { name: "All nodes" }).click();
  await expect(page.locator("#chat-target")).toHaveText("Whole claim");
});

test("the impact view previews what a withdrawn assumption affects", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Assumption impact" }).click();
  await expect(page.locator(".impact-bar")).toContainText("Read-only preview");
  await expect(page.locator("#queue-title")).toHaveText("Affected records");
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
  await expect(send).toHaveAccessibleName("Confirm sending to Codex");
  await expect(page.locator("#guidance-status")).toContainText("Click again to confirm");
  await expect(page.locator(".chat-feed")).not.toContainText("Try the reduction route first.");
  await send.click();
  await expect(page.locator("#guidance-status")).toHaveText("Note recorded; waiting for a Codex call.");
  await expect(page.locator(".chat-feed")).toContainText("Try the reduction route first.");
});

test("zoom, splitters, sidebar and language are remembered", async ({ page }) => {
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
  await page.getByRole("button", { name: "More" }).click();
  await page.getByRole("button", { name: "简体中文" }).click();
  await page.reload();
  await expect(page.locator("#workbench")).toHaveAttribute("data-sidebar", "collapsed");
  await expect(page.locator('.splitter[data-split="side"]')).toHaveAttribute("aria-valuenow", "272");
  await expect(page.getByRole("button", { name: "证明推进" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
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
