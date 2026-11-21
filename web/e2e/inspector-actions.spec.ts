import { expect, test, type Page } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

async function expectOneActionRow(page: Page, mainAction: string) {
  const actions = page.locator(".inspector-actions");
  await expect(actions.locator(".button.primary:visible")).toHaveAccessibleName(mainAction);
  await expect
    .poll(() =>
      actions.evaluate((row) => {
        const boundary = row.getBoundingClientRect();
        const controls = Array.from(row.querySelectorAll("button, a"), (button) => button.getBoundingClientRect());
        const first = controls[0];
        return (
          !!first && controls.every((rect) => Math.abs(rect.top - first.top) < 1 && rect.right <= boundary.right + 1)
        );
      }),
    )
    .toBe(true);
  return actions;
}

for (const viewport of [
  { width: 1280, height: 800 },
  { width: 640, height: 460 },
]) {
  test(`claim and node actions stay on one row at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      localStorage.setItem("research-multiplier-workbench-layout", JSON.stringify({ right: 280 }));
    });
    await page.goto("/#session=fixture");
    const claim = mapClaim(page, FIXTURE_CLAIM_TITLE);
    await claim.click();
    const actions = await expectOneActionRow(page, "Review");
    // Exercise the same overflow path when translations or available space make the labels too wide.
    await actions.evaluate((row: HTMLElement) => row.style.setProperty("max-width", "190px"));
    const more = actions.getByRole("button", { name: "More inspector actions", exact: true });
    await more.click();
    await expect(page.getByRole("menuitem", { name: "Ask an Agent", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");

    await claim.press("Enter");
    await expect(page.locator(".main-proof-graph")).toBeVisible();
    const node = page.locator(".main-proof-graph .graph-node").filter({ hasText: "Proof obligation 2" });
    await node.press("Enter");
    await expect(node).toHaveAttribute("aria-pressed", "true");
    await expectOneActionRow(page, "Proof task");
    await expect(actions.getByRole("button", { name: "Open proof", exact: true })).toHaveCount(0);
  });
}

test("a single click inspects a card and double-click opens its proof", async ({ page }) => {
  await page.goto("/#session=fixture");
  const stage = page.getByRole("application", { name: "Project map", exact: true });
  const claim = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const other = mapClaim(page, "单射映射的复合");
  await other.click();
  await expect(stage).toBeVisible();
  await expect(other).toHaveAttribute("aria-pressed", "true");
  await claim.dblclick();
  await expect(page.locator(".main-proof-graph")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(stage).toBeVisible();
  await expect(claim).toHaveAttribute("aria-pressed", "true");
  await other.press("Enter");
  await expect(page.locator(".main-proof-graph")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(stage).toBeVisible();
});

for (const title of ["单射映射的复合", "连续自然数之积的奇偶性"]) {
  test(`a timed double-click opens ${title} from a fresh compact map`, async ({ page }) => {
    await page.setViewportSize({ width: 640, height: 460 });
    await page.goto("/#session=fixture");
    const stage = page.getByRole("application", { name: "Project map", exact: true });
    const inspector = page.locator("#review");
    const card = mapClaim(page, title);
    await expect(inspector).toBeHidden();
    await expect(card).toHaveAttribute("aria-pressed", "false");

    const point = await card.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
    });
    await expect
      .poll(() =>
        card.evaluate((element, point) => element.contains(document.elementFromPoint(point.x, point.y)), point),
      )
      .toBe(true);
    // Let real pointer events span more than one rendering frame at the same coordinates.
    await page.mouse.dblclick(point.x, point.y, { delay: 120 });
    await expect(page.locator(".main-proof-graph")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(stage).toBeVisible();
    await expect(inspector).toBeHidden();
    await expect(card).toHaveAttribute("aria-pressed", "true");
    await expect(card).toBeFocused();
  });
}

test("compact single click highlights immediately and keeps explicit proof entry", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  const card = mapClaim(page, "连续自然数之积的奇偶性");
  const inspector = page.locator("#review");
  const proof = page.locator(".main-proof-graph");
  await card.click();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  await expect(inspector).toBeHidden();
  await expect(inspector).toBeVisible();
  await expect(proof).toBeHidden();

  const actions = page.locator(".inspector-actions");
  await expect(actions.getByRole("button", { name: "Proof task", exact: true })).toBeVisible();
  const open = actions.getByRole("button", { name: "Open proof", exact: true });
  if (await open.isVisible()) {
    await open.click();
  } else {
    await actions.getByRole("button", { name: "More inspector actions", exact: true }).click();
    await page.getByRole("menuitem", { name: "Open proof", exact: true }).click();
  }
  await expect(proof).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(proof).toBeHidden();
  await expect(inspector).toBeVisible();

  await inspector.getByRole("button", { name: "Collapse review column", exact: true }).click();
  await card.press("Enter");
  await expect(proof).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();
});
