import { expect, test, type Locator } from "@playwright/test";
import { openProof } from "./project-map";

function nodeLayout(canvas: Locator) {
  return canvas.locator(".graph-node").evaluateAll((nodes) =>
    nodes.map((node) => {
      const card = node as HTMLElement;
      return {
        id: card.dataset.node,
        code: card.querySelector(".node-code")?.textContent,
        left: card.parentElement?.offsetLeft,
        top: card.parentElement?.offsetTop,
        width: card.offsetWidth,
        height: card.offsetHeight,
      };
    }),
  );
}

test("localizing proof titles keeps positions, references and saved card sizes", async ({ page }) => {
  await page.goto("/#session=fixture");
  await openProof(page);
  const canvas = page.locator(".main-proof-graph");
  const obligation = canvas.locator('.graph-node[data-kind="obligation"]').filter({ hasText: "Proof obligation 1" });
  await expect(obligation).toBeVisible();
  await expect(canvas.locator(".graph-node")).toHaveCount(7);
  const resizedId = await obligation.getAttribute("data-node");
  const resize = canvas.getByRole("button", { name: "Resize node Proof obligation 1", exact: true });
  const widthBefore = await obligation.evaluate((card) => (card as HTMLElement).offsetWidth);
  await resize.focus();
  await resize.press("Shift+ArrowRight");
  await expect.poll(() => obligation.evaluate((card) => (card as HTMLElement).offsetWidth)).toBe(widthBefore + 36);
  const before = await nodeLayout(canvas);
  expect(before.find(({ id }) => id === resizedId)?.code).toMatch(/^C111-\d{2}$/);

  await page.evaluate(() => {
    location.hash = "#settings";
  });
  await page.getByRole("button", { name: "简体中文", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await page.evaluate(() => {
    location.hash = "#workbench";
  });
  await openProof(page);

  const translated = canvas.locator('.graph-node[data-kind="obligation"]').filter({ hasText: "证明义务 1" });
  await expect(translated).toHaveAttribute("data-node", resizedId!);
  await expect(translated).toHaveAttribute("data-sized", "true");
  await expect.poll(() => nodeLayout(canvas)).toEqual(before);
});
