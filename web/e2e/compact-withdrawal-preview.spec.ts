import { expect, test } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

test("withdrawal preview gives the affected titles and graph their own room at 640×460", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  const claim = mapClaim(page, FIXTURE_CLAIM_TITLE);
  await claim.click();
  await claim.press("Enter");
  await page.getByRole("button", { name: "If withdrawn", exact: true }).click();
  const inspector = page.locator("#review");
  const queue = inspector.locator(".queue");
  await expect(queue.locator("#queue-count")).toHaveText("3 affected · 1 premise previewed");
  const rows = queue.locator(".queue-item");
  await expect(rows).toHaveCount(4);
  await expect(rows.filter({ hasText: "Proof obligation 1" })).toBeVisible();
  await expect
    .poll(() =>
      rows.evaluateAll((items) =>
        items.map((item) => {
          const title = item.querySelector(".label")!;
          const status = item.querySelector(".kind")!;
          return {
            title: title.textContent,
            fullTitleFits: title.scrollWidth <= title.clientWidth && title.scrollHeight <= title.clientHeight,
            fullStatusFits: status.scrollWidth <= status.clientWidth,
            rowFits: item.scrollWidth <= item.clientWidth,
          };
        }),
      ),
    )
    .toEqual(
      ["假设 H₁", "reverseZeroAdd", "Proof obligation 1", "Reduction 1"].map((title) => ({
        title,
        fullTitleFits: true,
        fullStatusFits: true,
        rowFits: true,
      })),
    );

  await expect
    .poll(() =>
      queue.evaluate((section) => {
        const heading = section.querySelector("#queue-title")!;
        const count = section.querySelector("#queue-count")!;
        const text = document.createRange();
        text.selectNodeContents(heading);
        return {
          headingLines: text.getClientRects().length,
          countBelowHeading: count.getBoundingClientRect().top >= heading.getBoundingClientRect().bottom - 1,
        };
      }),
    )
    .toEqual({ headingLines: 1, countBelowHeading: true });
  await expect
    .poll(() =>
      page.locator(".proof-surface").evaluate((proof) => {
        const bar = proof.querySelector(".impact-bar")!.getBoundingClientRect();
        const stage = proof.querySelector(".stage")!.getBoundingClientRect();
        return stage.top >= bar.bottom - 1 && stage.height > 0;
      }),
    )
    .toBe(true);
});
