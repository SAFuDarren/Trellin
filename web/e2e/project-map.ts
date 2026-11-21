import type { Page } from "@playwright/test";

export const FIXTURE_CLAIM_TITLE = "自然数加零的对称性";

export function mapClaim(page: Page, title: string) {
  return page.locator(".project-canvas-stage").getByRole("button", { name: title, exact: true });
}

export async function openProof(page: Page, title = FIXTURE_CLAIM_TITLE, label = /^(Open proof|打开证明)$/) {
  await mapClaim(page, title).click();
  await page.getByRole("button", { name: label }).click();
}
