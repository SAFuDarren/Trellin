import type { Page } from "@playwright/test";

/**
 * #326: Claims opens by domain. Tests about the status board, list or table start from the status board, the
 * layout they were written for; a layout the test chooses later is kept across reloads.
 */
export async function startOnStatusBoard(page: Page) {
  await page.addInitScript(() => {
    if (localStorage.getItem("trellin.claims.display")) return;
    localStorage.setItem("trellin.claims.display", JSON.stringify({ layout: "board" }));
  });
}
