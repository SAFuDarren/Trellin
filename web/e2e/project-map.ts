import { expect, type Page } from "@playwright/test";

export const FIXTURE_CLAIM_TITLE = "自然数加零的对称性";

export function mapClaim(page: Page, title: string) {
  return page.locator(".project-canvas-plot").getByRole("button", { name: title, exact: true });
}

export async function openProof(page: Page, title = FIXTURE_CLAIM_TITLE, label = /^(Open proof|打开证明)$/) {
  const back = page.locator(".proof-return");
  if (await back.isVisible()) await back.click();
  await mapClaim(page, title).click();
  const inspector = page.locator("#review");
  const open = inspector.getByRole("button", { name: label });
  const more = inspector.getByRole("button", { name: /^(More inspector actions|更多课题操作)$/ });
  await expect(open.or(more).first()).toBeVisible();
  if (await open.isVisible()) await open.click();
  else {
    await more.click();
    await page.getByRole("menuitem", { name: label }).click();
  }
}
