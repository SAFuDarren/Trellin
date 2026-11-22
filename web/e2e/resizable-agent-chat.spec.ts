import { expect, test, type Locator, type Page } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

const SIZE_KEY = "research-multiplier-agent-chat-size";
const precise = { exact: true };

const chat = (page: Page) => page.getByRole("region", { name: "Agent chat", ...precise });
const launcher = (page: Page) => page.getByRole("button", { name: "Agent chat", ...precise });
const bounds = async (element: Locator) => {
  await expect(element).toBeVisible();
  return (await element.boundingBox())!;
};
const size = async (element: Locator) => {
  const { width, height } = await bounds(element);
  return { width: Math.round(width), height: Math.round(height) };
};

async function dragEdge(page: Page, edge: string, dx: number, dy: number) {
  const hit = await bounds(chat(page).locator(`.agent-chat-resize[data-edge="${edge}"]`));
  const x = hit.x + hit.width / 2;
  const y = hit.y + hit.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 6 });
  await page.mouse.up();
}

async function expectCanvasClear(page: Page) {
  await expect(page.locator("#review")).toBeVisible();
  await expect
    .poll(async () => {
      const panel = await bounds(chat(page));
      const inspector = await bounds(page.locator("#review"));
      const zoom = await bounds(page.locator(".project-canvas .zoom"));
      return panel.x + panel.width <= inspector.x && panel.y + panel.height <= zoom.y;
    })
    .toBe(true);
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
]) {
  test(`one floating Agent chat can be resized and restored at ${viewport.width}×${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await page.route(/\/api\/conversations(?:[/?]|$)/, (route) => route.fulfill({ json: { threads: [] } }));
    await page.goto("/#session=fixture");
    await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
    const zoom = page.locator(".project-canvas .zoom");
    const initialZoom = await bounds(zoom);
    const dock = chat(page);
    await launcher(page).click();
    await expect(dock).toHaveCSS("position", "fixed");
    await expectCanvasClear(page);
    expect(await bounds(zoom)).toEqual(initialZoom);
    const noResize = await size(dock);

    await dragEdge(page, "top", 0, -48);
    await expect.poll(() => size(dock)).toEqual({ width: noResize.width, height: noResize.height + 48 });
    await dragEdge(page, "left", 42, 0);
    const afterLeft = { width: noResize.width - 42, height: noResize.height + 48 };
    await expect.poll(() => size(dock)).toEqual(afterLeft);
    await dragEdge(page, "corner", 26, -30);
    const manual = { width: afterLeft.width - 26, height: afterLeft.height + 30 };
    await expect.poll(() => size(dock)).toEqual(manual);
    await expectCanvasClear(page);

    const widthHandle = dock.getByRole("separator", { name: "Resize chat width", ...precise });
    const heightHandle = dock.getByRole("separator", { name: "Resize chat height", ...precise });
    await widthHandle.press("ArrowLeft");
    await heightHandle.press("ArrowUp");
    const chosen = { width: manual.width + 16, height: manual.height + 16 };
    await expect.poll(() => size(dock)).toEqual(chosen);
    const chosenBox = await bounds(dock);
    await expect(widthHandle).toHaveAttribute("aria-valuenow", String(chosen.width));
    await expect(heightHandle).toHaveAttribute("aria-valuenow", String(chosen.height));
    await expect
      .poll(() => page.evaluate((key) => JSON.parse(localStorage.getItem(key) || "null"), SIZE_KEY))
      .toEqual(chosen);

    await dock.getByRole("button", { name: "Maximize Agent chat", ...precise }).click();
    await expectCanvasClear(page);
    expect((await size(dock)).width).toBeGreaterThan(chosen.width);
    expect((await size(dock)).height).toBeGreaterThan(chosen.height);
    await expect(dock.getByRole("separator")).toHaveCount(0);
    await dock.getByRole("button", { name: "Restore chat size", ...precise }).click();
    await expect.poll(() => bounds(dock)).toEqual(chosenBox);

    const draft = dock.getByRole("textbox", { name: "Write a message", ...precise });
    await draft.fill("Keep this floating-chat draft across pages.");
    await dock.getByRole("button", { name: "Collapse Agent panel", ...precise }).click();
    await expect(dock).toBeHidden();
    await launcher(page).click();
    await expect.poll(() => bounds(dock)).toEqual(chosenBox);
    await expect(draft).toHaveValue("Keep this floating-chat draft across pages.");

    const rail = page.getByRole("navigation", { name: "Sections", ...precise });
    for (const label of [/^Library$/, /^Claims$/, /^Team/]) {
      await rail.getByRole("link", { name: label }).click();
      await expect(dock).toHaveCSS("position", "fixed");
      await expect.poll(() => size(dock)).toEqual(chosen);
      await expect(draft).toHaveValue("Keep this floating-chat draft across pages.");
    }
    await page.reload();
    await launcher(page).click();
    await expect.poll(() => size(dock)).toEqual(chosen);

    await rail.getByRole("link", { name: "Home", ...precise }).click();
    await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");
    const details = page.locator(".project-canvas-tools").getByRole("button", { name: "Details", ...precise });
    if ((await details.getAttribute("aria-pressed")) === "false") await details.click();
    await expectCanvasClear(page);
    await zoom.getByRole("button", { name: "Zoom in", ...precise }).click();
    expect(await bounds(zoom)).toEqual(initialZoom);
    await page.getByRole("button", { name: "Global map", ...precise }).click();
    const atlas = await bounds(page.locator(".project-canvas .atlas"));
    const withoutOverlap = (panel: Awaited<ReturnType<typeof bounds>>) =>
      panel.x + panel.width <= atlas.x ||
      panel.x >= atlas.x + atlas.width ||
      panel.y + panel.height <= atlas.y ||
      panel.y >= atlas.y + atlas.height;
    await expect.poll(async () => withoutOverlap(await bounds(dock))).toBe(true);
    await dock.getByRole("button", { name: "Maximize Agent chat", ...precise }).click();
    await expect.poll(async () => withoutOverlap(await bounds(dock))).toBe(true);
    await expectCanvasClear(page);
  });
}

test("desktop restores the per-user size and writes only dimensions after resizing", async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, {
      trellinAgentChatSize: { width: 384, height: 316 },
      webkit: {
        messageHandlers: {
          trellinAgentChatSize: {
            postMessage: (size: { width: number; height: number }) =>
              sessionStorage.setItem("last-fixture-bridge-size", JSON.stringify(size)),
          },
        },
      },
    });
  });
  await page.goto("/#session=fixture");
  await page.getByRole("link", { name: "Library", ...precise }).click();
  await launcher(page).click();
  await expect.poll(() => size(chat(page))).toEqual({ width: 384, height: 316 });
  await chat(page)
    .getByRole("separator", { name: "Resize chat width", ...precise })
    .press("ArrowRight");
  expect(await page.evaluate(() => sessionStorage.getItem("last-fixture-bridge-size"))).toBe(
    JSON.stringify({ width: 368, height: 316 }),
  );
  expect(await page.evaluate((key) => localStorage.getItem(key), SIZE_KEY)).toBeNull();
});

test("chat resizing clamps at both limits in the minimum window", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.addInitScript((key) => localStorage.setItem(key, JSON.stringify({ width: 1200, height: 900 })), SIZE_KEY);
  await page.goto("/#session=fixture");
  await launcher(page).click();
  const dock = chat(page);
  const oldRight = await bounds(dock).then((box) => box.x + box.width);
  await page
    .locator(".project-canvas-tools")
    .getByRole("button", { name: "Details", ...precise })
    .click();
  await expectCanvasClear(page);
  const newRight = await bounds(dock).then((box) => box.x + box.width);
  expect(newRight).toBeLessThan(oldRight);
  const widthHandle = dock.getByRole("separator", { name: "Resize chat width", ...precise });
  const heightHandle = dock.getByRole("separator", { name: "Resize chat height", ...precise });
  await expect(widthHandle).toHaveAttribute("aria-valuenow", (await widthHandle.getAttribute("aria-valuemax"))!);
  await expect(heightHandle).toHaveAttribute("aria-valuenow", (await heightHandle.getAttribute("aria-valuemax"))!);

  await dragEdge(page, "left", -50, 0);
  await dragEdge(page, "top", 0, -50);
  await expectCanvasClear(page);
  await expect(widthHandle).toHaveAttribute("aria-valuenow", (await widthHandle.getAttribute("aria-valuemax"))!);
  await expect(heightHandle).toHaveAttribute("aria-valuenow", (await heightHandle.getAttribute("aria-valuemax"))!);

  await dragEdge(page, "corner", 250, 250);
  await expect(widthHandle).toHaveAttribute("aria-valuenow", (await widthHandle.getAttribute("aria-valuemin"))!);
  await expect(heightHandle).toHaveAttribute("aria-valuenow", (await heightHandle.getAttribute("aria-valuemin"))!);
  await expectCanvasClear(page);
});
