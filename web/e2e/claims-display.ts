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

/** Where the board is scrolled to and where each card sits, to show that opening the window moves nothing. */
export async function boardLayout(page: Page) {
  return page.locator(".research-lanes").evaluate((board) => ({
    scroll: [board.scrollLeft, board.scrollTop],
    cards: [...board.querySelectorAll(".lane-card, .lane-row")].map((card) => {
      const box = card.getBoundingClientRect();
      return [Math.round(box.x), Math.round(box.y), Math.round(box.width), Math.round(box.height)];
    }),
  }));
}
