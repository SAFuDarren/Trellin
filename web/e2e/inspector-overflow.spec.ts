import { expect, test, type Locator } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

async function expectInspectorFit(inspector: Locator) {
  await expect(inspector).toBeVisible();
  await expect
    .poll(() =>
      inspector.evaluate((panel) => ({
        overflow: panel.scrollWidth - panel.clientWidth,
        width: panel.clientWidth,
        scrollWidth: panel.scrollWidth,
      })),
    )
    .toMatchObject({ overflow: 0 });
}

const viewports = [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
  { width: 640, height: 460 },
];

for (const viewport of viewports) {
  for (const theme of ["light", "dark"] as const) {
    test(`inspector content fits at ${viewport.width}×${viewport.height} in ${theme}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: theme });
      await page.goto("/#session=fixture");
      const inspector = page.locator("#review");
      const claim = mapClaim(page, FIXTURE_CLAIM_TITLE);
      await claim.click();
      await expect(inspector.getByRole("link", { name: "Review", exact: true })).toBeVisible();
      await expect(inspector.getByText("1 result to review", { exact: true })).toBeVisible();
      await expectInspectorFit(inspector);

      const more = inspector.getByRole("button", { name: "More inspector actions", exact: true });
      await more.click();
      const menu = page.getByRole("menu", { name: "More inspector actions", exact: true });
      await expect(menu.getByRole("menuitem", { name: "Proof task", exact: true })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(more).toBeFocused();

      await claim.press("Enter");
      const proof = page.locator(".main-proof-graph");
      const obligation = proof.locator(".graph-node").filter({ hasText: "Proof obligation 2" });
      await obligation.press("Enter");
      await expect(inspector.getByRole("heading", { name: "Proof obligation 2", exact: true })).toBeVisible();
      await expect(inspector.getByRole("button", { name: "Proof task", exact: true })).toBeVisible();
      await expectInspectorFit(inspector);

      await proof.getByRole("button", { name: /^reverseZeroAdd,/ }).press("Enter");
      await expect(inspector.getByRole("heading", { name: "reverseZeroAdd", exact: true })).toBeVisible();
      await expect(inspector.getByRole("link", { name: "Review", exact: true })).toBeVisible();
      await expectInspectorFit(inspector);

      await page.getByRole("button", { name: "If withdrawn", exact: true }).click();
      await expect(inspector.getByText("3 affected · 1 premise previewed", { exact: true })).toBeVisible();
      await expectInspectorFit(inspector);
    });
  }
}
