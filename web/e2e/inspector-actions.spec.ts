import { expect, test, type Page } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

async function expectOneActionRow(page: Page) {
  const actions = page.locator(".inspector-actions");
  await expect(actions.getByRole("button").first()).toHaveText("Proof task");
  await expect(actions.getByRole("button", { name: "Proof task" })).toHaveClass(/\bprimary\b/);
  await expect
    .poll(() =>
      actions.evaluate((row) => {
        const boundary = row.getBoundingClientRect();
        const controls = Array.from(row.querySelectorAll("button"), (button) => button.getBoundingClientRect());
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
    const actions = await expectOneActionRow(page);
    // Larger controls exercise the overflow menu even if all three labels fit at the default font size.
    await actions.evaluate((row: HTMLElement) => row.style.setProperty("--type-control-size", "17px"));
    const more = actions.getByRole("button", { name: "More inspector actions", exact: true });
    await more.click();
    await expect(page.getByRole("menuitem", { name: "Ask an Agent", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");

    await claim.press("Enter");
    await expect(page.locator(".main-proof-graph")).toBeVisible();
    const node = page.locator(".main-proof-graph .graph-node").filter({ hasText: "Proof obligation 2" });
    await node.press("Enter");
    await expect(node).toHaveAttribute("aria-pressed", "true");
    await expectOneActionRow(page);
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
