import { expect, test, type Locator, type Page } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

const TARGET = "单射映射的复合";

function getMap(page: Page) {
  return page.getByRole("application", { name: "Project map", exact: true });
}

async function center(locator: Locator) {
  const bounds = await locator.boundingBox();
  if (!bounds) throw new Error("Expected a visible map control");
  return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
}

async function startLinkDrag(page: Page) {
  await mapClaim(page, FIXTURE_CLAIM_TITLE).hover();
  const handle = page.getByRole("button", { name: `Connect ${FIXTURE_CLAIM_TITLE}`, exact: true });
  await expect(handle).toHaveCSS("opacity", "1");
  const at = await center(handle);
  await page.mouse.move(at.x, at.y);
  await page.mouse.down();
  await expect(getMap(page).locator(".project-canvas-link-preview")).toBeAttached();
}

async function moveTo(page: Page, locator: Locator) {
  const at = await center(locator);
  await page.mouse.move(at.x, at.y, { steps: 6 });
}

async function clickLink(page: Page) {
  const path = getMap(page).locator(".project-canvas-link-hit");
  const at = await path.evaluate((element) => {
    const line = element as SVGPathElement;
    const middle = line.getPointAtLength(line.getTotalLength() / 2);
    const point = middle.matrixTransform(line.getScreenCTM()!);
    return { x: point.x, y: point.y };
  });
  await page.mouse.move(at.x, at.y);
  await expect(getMap(page).locator(".project-canvas-link:hover")).toHaveCount(1);
  await page.mouse.click(at.x, at.y);
  await expect(getMap(page).locator(".project-canvas-link")).toHaveAttribute("aria-pressed", "true");
}

async function expectLinkDetailClearOfCards(page: Page, details: Locator) {
  const position = (await details.boundingBox())!;
  const cards = await page
    .locator(".project-canvas-node")
    .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
  expect(
    cards.every(
      (card) =>
        position.x >= card.right ||
        position.x + position.width <= card.left ||
        position.y >= card.bottom ||
        position.y + position.height <= card.top,
    ),
  ).toBe(true);
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

for (const colorScheme of ["light", "dark"] as const) {
  test(`links can be drawn and removed in ${colorScheme} appearance`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/api/project-canvas")) {
        writes.push(request.postDataJSON().action);
      }
    });
    await page.goto("/#session=fixture");
    const map = getMap(page);
    const target = mapClaim(page, TARGET);
    const paths = map.locator("[data-research-link]");
    await expect(target).toBeVisible();

    await startLinkDrag(page);
    await moveTo(page, target);
    await expect(map.locator(".project-canvas-link-preview")).toHaveCSS("stroke-width", "2.5px");
    await expect(target).toHaveAttribute("data-link-target", "true");
    await page.keyboard.press("Escape");
    await page.mouse.up();
    await expect(paths).toHaveCount(0);
    expect(writes).toEqual([]);

    await startLinkDrag(page);
    const bounds = (await map.boundingBox())!;
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 57);
    await page.mouse.up();
    await expect(paths).toHaveCount(0);
    expect(writes).toEqual([]);

    await startLinkDrag(page);
    await moveTo(page, mapClaim(page, FIXTURE_CLAIM_TITLE));
    await page.mouse.up();
    await expect(map.getByRole("status")).toHaveText("Choose a different claim to create a link.");
    expect(writes).toEqual([]);

    await startLinkDrag(page);
    await moveTo(page, target);
    await expect(target).toHaveAttribute("data-link-target", "true");
    await expect(map).toHaveCSS("outline-style", "none");
    const previewPath = await map.locator(".project-canvas-link-preview").getAttribute("d");
    expect(previewPath).toBeTruthy();
    await page.mouse.up();
    await expect(paths).toHaveCount(1);
    await expect(paths).toHaveAttribute("d", previewPath!);
    await expect(map).toHaveCSS("outline-style", "none");
    await expect.poll(() => writes).toEqual(["link"]);
    await expect(page.locator(".project-canvas-link-handle").first()).toBeAttached();

    await startLinkDrag(page);
    await moveTo(page, target);
    await expect(target).not.toHaveAttribute("data-link-target", "true");
    await page.mouse.up();
    await expect(map.getByRole("status")).toHaveText("These claims are already linked.");
    expect(writes).toEqual(["link"]);

    await page.reload();
    await expect(paths).toHaveCount(1);
    await expect(paths).toHaveAttribute("d", previewPath!);
    await clickLink(page);
    const details = page.locator(".project-canvas-link-detail");
    const names = details.locator(".project-canvas-link-names span");
    await expect(names).toHaveText([FIXTURE_CLAIM_TITLE, "↔", TARGET]);
    await expect(details).toHaveAccessibleName(`${FIXTURE_CLAIM_TITLE} ↔ ${TARGET}`);
    await expect(map.locator(".project-canvas-link")).toHaveCSS("outline-style", "none");
    await expect(paths).toHaveCSS("stroke-width", "3px");
    await expect(paths).toHaveCSS("stroke-dasharray", "none");
    await expect(map.locator(".project-canvas-link-hit")).toHaveCSS("stroke", "rgba(0, 0, 0, 0)");
    await expectLinkDetailClearOfCards(page, details);
    await page.keyboard.press("Escape");
    await expect(details).toHaveCount(0);
    await clickLink(page);
    if (colorScheme === "light") await details.getByRole("button", { name: "Remove", exact: true }).click();
    else await page.keyboard.press("Delete");
    await expect(paths).toHaveCount(0);
    await expect.poll(() => writes).toEqual(["link", "unlink"]);
    const feedback = page.locator(".workbench-action-feedback");
    await expect(feedback.getByText("Research link removed.", { exact: true })).toBeVisible();
    await feedback.getByRole("button", { name: "Undo", exact: true }).click();
    await expect(feedback.getByText("Research link restored.", { exact: true })).toBeVisible();
    await expect(paths).toHaveAttribute("d", previewPath!);
    await expect.poll(() => writes).toEqual(["link", "unlink", "link"]);
    await page.reload();
    await expect(paths).toHaveAttribute("d", previewPath!);
    await expect(feedback.getByRole("button", { name: "Undo", exact: true })).toHaveCount(0);
  });
}

test("keyboard linking cycles through cards and focuses a saved link for removal", async ({ page }) => {
  await page.goto("/#session=fixture");
  const map = getMap(page);
  const source = mapClaim(page, FIXTURE_CLAIM_TITLE);
  await source.press("Shift+F10");
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("menuitem", { name: "Link selected claim", exact: true })).toHaveAttribute(
    "aria-keyshortcuts",
    "L",
  );
  await page.keyboard.press("Escape");
  await page.keyboard.press("?");
  const shortcuts = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(shortcuts.getByText("Link selected claim", { exact: true }).locator("..").locator("kbd")).toHaveText(
    "L",
  );
  await page.keyboard.press("Escape");

  await source.press("l");
  await expect(map.getByRole("status")).toContainText(FIXTURE_CLAIM_TITLE);
  await page.keyboard.press("ArrowRight");
  const current = await page.locator(".project-canvas-node:focus").getAttribute("data-claim-id");
  await page.keyboard.press("Tab");
  await expect(page.locator(".project-canvas-node:focus")).not.toHaveAttribute("data-claim-id", current!);
  await page.keyboard.press("Enter");
  const link = map.getByRole("button", { name: /Research link between/ });
  await expect(link).toBeAttached();
  await link.press("Enter");
  await expect(link).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".project-canvas-link-detail").getByRole("button", { name: "Remove" })).toBeEnabled();
  await page.keyboard.press("Backspace");
  await expect(link).toHaveCount(0);
});

test("the map still reads claims when editing is unavailable", async ({ page }) => {
  await page.route("**/api/project-canvas", (route) => route.fulfill({ status: 503, json: {} }));
  await page.goto("/#session=fixture");
  const source = mapClaim(page, FIXTURE_CLAIM_TITLE);
  await expect(source).toBeVisible();
  await source.hover();
  await expect(page.getByRole("button", { name: /^Connect / })).toHaveCount(0);
  await source.press("l");
  await expect(getMap(page).locator(".project-canvas-link-preview")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Link selected claim", exact: true })).toBeDisabled();
});

test("a link follows the pointer near the canvas edge and tracks both cards after layout changes", async ({ page }) => {
  await page.goto("/#session=fixture");
  const map = getMap(page);
  const plot = map.locator(".project-canvas-plot");
  const source = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const target = mapClaim(page, TARGET);
  await expect(source).toBeVisible();
  const original = await plot.getAttribute("style");
  const bounds = (await map.boundingBox())!;
  await startLinkDrag(page);
  await page.mouse.move(bounds.x + 6, bounds.y + bounds.height / 2);
  await expect.poll(() => plot.getAttribute("style")).not.toEqual(original);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(map.locator("[data-research-link]")).toHaveCount(0);
  const zoom = page.locator(".project-canvas").getByRole("group", { name: "Zoom" });
  await zoom.getByRole("button", { name: "Fit to window" }).click();

  await startLinkDrag(page);
  await moveTo(page, target);
  await page.mouse.up();
  const path = map.locator("[data-research-link]");
  await expect(path).toHaveCount(1);
  await zoom.getByRole("button", { name: "Zoom in" }).click();
  await map.press("ArrowLeft");
  await expect(path).toHaveCount(1);

  for (const card of [source, target]) {
    const previous = await path.getAttribute("d");
    const point = await center(card);
    await page.mouse.move(point.x, point.y);
    await page.mouse.down();
    await page.mouse.move(point.x + 15, point.y - 27, { steps: 7 });
    await page.mouse.up();
    await expect.poll(() => path.getAttribute("d")).not.toEqual(previous);
  }
});

test("selected links expose both names in the compact map and retain a keyboard focus indicator", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  const map = getMap(page);
  const target = mapClaim(page, TARGET);
  await expect(target).toBeVisible();
  await map.focus();
  await page.keyboard.press("0");
  await expect(map).toHaveCSS("outline-style", "solid");
  await startLinkDrag(page);
  await moveTo(page, target);
  await expect(target).toHaveAttribute("data-link-target", "true");
  await expect(map).toHaveCSS("outline-style", "none");
  await page.mouse.up();
  const path = map.locator("[data-research-link]");
  await expect(path).toHaveCount(1);
  const link = map.getByRole("button", { name: /Research link between/ });
  await link.press("Enter");
  const details = page.getByRole("group", { name: `${FIXTURE_CLAIM_TITLE} ↔ ${TARGET}` });
  await expect(details).toBeVisible();
  await expectLinkDetailClearOfCards(page, details);
  await expect(link).toHaveCSS("outline-style", "none");
  await expect(path).toHaveCSS("stroke-width", "4px");
  await page.keyboard.press("Escape");
  await expect(details).toHaveCount(0);
  await expect(path).toHaveCount(1);

  await link.press("Enter");
  await expect(details.getByRole("button", { name: "Remove", exact: true })).toBeEnabled();
  await page.keyboard.press("Delete");
  const notice = page.locator(".workbench-action-toast").filter({ hasText: "Research link removed." });
  await expect(notice).toBeVisible();
  const noticeBounds = (await notice.boundingBox())!;
  const zoom = page.locator(".project-canvas").getByRole("group", { name: "Zoom", exact: true });
  const footer = page.getByRole("contentinfo", { name: "Agent chat", exact: true });
  expect(noticeBounds.y + noticeBounds.height).toBeLessThanOrEqual((await zoom.boundingBox())!.y);
  expect(noticeBounds.y + noticeBounds.height).toBeLessThanOrEqual((await footer.boundingBox())!.y);
  await notice.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(path).toHaveCount(1);
});

test("failed link changes roll back and Undo can be retried", async ({ page }) => {
  await page.goto("/#session=fixture");
  const map = getMap(page);
  const paths = map.locator("[data-research-link]");
  await startLinkDrag(page);
  await moveTo(page, mapClaim(page, TARGET));
  await page.mouse.up();
  await expect(paths).toHaveCount(1);

  let failure: "unlink" | "link" | null = "unlink";
  let settle!: () => void;
  const waitToFail = new Promise<void>((resolve) => {
    settle = resolve;
  });
  await page.route("**/api/project-canvas", async (route) => {
    if (!failure || route.request().method() !== "POST" || route.request().postDataJSON().action !== failure) {
      await route.continue();
      return;
    }
    await waitToFail;
    await route.fulfill({ status: 503, json: { error: "project_canvas.unavailable" } });
  });

  await clickLink(page);
  const details = page.locator(".project-canvas-link-detail");
  const feedback = page.locator(".workbench-action-feedback");
  try {
    await details.getByRole("button", { name: "Remove", exact: true }).click();
    await expect(paths).toHaveCount(0);
    await expect(feedback.getByRole("button", { name: "Undo", exact: true })).toHaveCount(0);
  } finally {
    settle();
  }
  await expect(page.getByRole("alert").filter({ hasText: "Reload" })).toBeVisible();
  await expect(paths).toHaveCount(1);
  await expect(feedback.getByText("Research link removed.", { exact: true })).toHaveCount(0);
  const undo = feedback.getByRole("button", { name: "Undo", exact: true });
  await expect(undo).toHaveCount(0);

  failure = null;
  await clickLink(page);
  await details.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(undo).toBeEnabled();
  failure = "link";
  const failedUndo = page.waitForResponse(
    (response) => response.url().endsWith("/api/project-canvas") && response.status() === 503,
  );
  await undo.click();
  await failedUndo;
  await expect(page.getByRole("alert").filter({ hasText: "Reload" })).toBeVisible();
  await expect(paths).toHaveCount(0);
  await expect(undo).toBeEnabled();

  failure = null;
  await undo.click();
  await expect(feedback.getByText("Research link restored.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(paths).toHaveCount(1);
});
