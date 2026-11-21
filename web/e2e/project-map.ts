import type { Page } from "@playwright/test";

export const FIXTURE_CLAIM_TITLE = "自然数加零的对称性";

export function mapClaim(page: Page, title: string) {
  return page.locator(".project-canvas-plot").getByRole("button", { name: title, exact: true });
}

export async function openProof(page: Page, title = FIXTURE_CLAIM_TITLE, label = /^(Proof details|证明详情)$/) {
  await mapClaim(page, title).click();
  const summary = page.locator("summary").filter({ hasText: label });
  if (!(await summary.evaluate((element) => (element.parentElement as HTMLDetailsElement).open))) {
    await summary.click();
  }
}
