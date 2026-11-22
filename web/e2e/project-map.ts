import { expect, type Page } from "@playwright/test";
import { openMenu } from "./navigation";

export const FIXTURE_CLAIM_TITLE = "自然数加零的对称性";

export function mapClaim(page: Page, title: string) {
  return page.locator(".project-canvas-plot").getByRole("button", { name: title, exact: true });
}

export async function clickInspectorAction(page: Page, name: string | RegExp) {
  const inspector = page.locator("#review");
  const action = inspector.getByRole("button", { name, exact: true });
  const more = inspector.getByRole("button", { name: /^(More inspector actions|更多课题操作)$/ });
  await expect(action.or(more).first()).toBeVisible();
  if (await action.isVisible()) await action.click();
  else {
    const menu = await openMenu(more);
    await menu.getByRole("menuitem", { name, exact: true }).click();
  }
}

export async function openProof(page: Page, title = FIXTURE_CLAIM_TITLE, label = /^(Open proof|打开证明)$/) {
  const back = page.locator(".proof-return");
  if (await back.isVisible()) await back.click();
  await mapClaim(page, title).click();
  await clickInspectorAction(page, label);
}
