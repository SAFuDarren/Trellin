// SPDX-License-Identifier: Apache-2.0
import { expect, test, type Locator } from "@playwright/test";
import { openProof } from "./project-map";

async function lineCount(description: Locator) {
  return description.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return new Set(Array.from(range.getClientRects(), (rect) => rect.top)).size;
  });
}

test("References emphasizes recorded uses and premises and explains the edge styles", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/#session=fixture");
  await openProof(page);
  const lensGroup = page.getByRole("group", { name: "Graph view" });
  const stage = page.locator(".main-proof-graph .stage");
  const explanation = page.getByText(
    "Solid lines show recorded proof uses, dotted lines show allowed premises, and dashed lines show planned steps.",
    { exact: true },
  );
  const evidence = stage.getByRole("button", { name: /^reverseZeroAdd,/ });
  const library = stage.getByRole("button", { name: /^Nat.add_zero,/ });
  const reduction = stage.getByRole("button", { name: /^Reduction 1,/ });
  const recorded = stage.locator('.graph-edge[data-kind="actual"]');
  const premise = stage.locator('.graph-edge[data-kind="context"]');
  const planned = stage.locator('.graph-edge[data-kind="planned"]');
  const legendToggle = page.getByRole("button", { name: "Edge styles", exact: true });
  const legend = page.getByRole("dialog", { name: "Edge styles", exact: true });

  await evidence.click();
  await expect(stage).toHaveAttribute("data-proof-view", "proof");
  await expect(explanation).toBeHidden();
  await expect(premise).toHaveAttribute("data-emphasis", "dim");
  const proofStroke = await recorded.first().evaluate((edge) => getComputedStyle(edge).stroke);

  await lensGroup.getByRole("button", { name: "References", exact: true }).click();
  await expect(stage).toHaveAttribute("data-proof-view", "references");
  await expect(explanation).toBeVisible();
  expect(await lineCount(explanation)).toBe(1);
  await expect(lensGroup).toHaveAccessibleDescription(await explanation.innerText());
  await expect(evidence).toHaveAttribute("aria-pressed", "true");
  await expect(library).toHaveAttribute("data-emphasis", "strong");
  await expect(reduction).toHaveAttribute("data-emphasis", "dim");
  await expect(recorded.and(stage.locator('[data-emphasis="strong"]'))).toHaveCount(3);
  await expect(premise).toHaveAttribute("data-emphasis", "strong");
  await expect(planned.and(stage.locator('[data-emphasis="dim"]'))).toHaveCount(3);
  const referenceStroke = await recorded.first().evaluate((edge) => getComputedStyle(edge).stroke);
  expect(referenceStroke).not.toBe(proofStroke);
  await expect(premise).toHaveCSS("stroke", referenceStroke);
  await expect(planned.first()).not.toHaveCSS("stroke", referenceStroke);
  await expect(recorded.first()).toHaveCSS("stroke-dasharray", "none");
  await expect(premise).toHaveCSS("stroke-dasharray", "0.5px, 4px");
  await expect(planned.first()).toHaveCSS("stroke-dasharray", "4px, 3px");

  await legendToggle.click();
  await expect(legend.getByRole("listitem")).toHaveText(["Used in the proof", "Allowed premise", "Planned step"]);
  await page.keyboard.press("Escape");
  await expect(legend).toBeHidden();

  await lensGroup.getByRole("button", { name: "Proof", exact: true }).click();
  await expect(explanation).toBeHidden();
  await expect(evidence).toHaveAttribute("aria-pressed", "true");
  await expect(library).toHaveAttribute("data-emphasis", "strong");
  await expect(reduction).toHaveAttribute("data-emphasis", "strong");
  await expect(recorded.first()).toHaveCSS("stroke", proofStroke);
  await legendToggle.click();
  await expect(legend.getByRole("listitem")).toHaveText(["Used in the proof"]);
  await page.keyboard.press("Escape");
  await expect(legend).toBeHidden();

  await lensGroup.getByRole("button", { name: "If withdrawn", exact: true }).click();
  await expect(stage).toHaveAttribute("data-proof-view", "impact");
  await expect(stage.locator('.graph-edge[data-kind="planned"][data-emphasis="strong"]')).toHaveCount(1);
  await expect(explanation).toBeHidden();
  await legendToggle.click();
  await expect(legend.getByRole("listitem")).toHaveText(["Used in the proof", "Planned step"]);
});

test("References explains the edge styles in Simplified Chinese", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.addInitScript(() => localStorage.setItem("research-multiplier-language", "zh-CN"));
  await page.goto("/#session=fixture");
  await openProof(page, undefined, /^打开证明$/);
  const lensGroup = page.getByRole("group", { name: "图谱视图" });
  await lensGroup.getByRole("button", { name: "引用关系", exact: true }).click();
  await expect(lensGroup).toHaveAccessibleDescription(
    "实线表示证明中已使用的引用，点线表示允许使用的前提，虚线表示计划中的步骤。",
  );
  const stage = page.locator('.main-proof-graph [data-proof-view="references"]');
  const recorded = stage.locator('.graph-edge[data-kind="actual"]').first();
  const planned = stage.locator('.graph-edge[data-kind="planned"]').first();
  const recordedStroke = await recorded.evaluate((edge) => getComputedStyle(edge).stroke);
  await expect(planned).not.toHaveCSS("stroke", recordedStroke);
});

test("References wraps at the minimum window size and leaves the graph and zoom usable", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  await openProof(page);
  const inspector = page.locator("#review");
  await expect(inspector).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();

  await page.getByRole("button", { name: "References", exact: true }).click();
  const explanation = page.locator(".proof-view-description");
  await expect(explanation).toBeInViewport({ ratio: 0.99 });
  expect(await lineCount(explanation)).toBeGreaterThan(1);
  expect(await explanation.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  const stage = page.locator('.main-proof-graph [data-proof-view="references"]');
  const textBounds = (await explanation.boundingBox())!;
  const graphBounds = (await stage.boundingBox())!;
  expect(graphBounds.y).toBeGreaterThanOrEqual(textBounds.y + textBounds.height - 1);
  expect(graphBounds.height).toBeGreaterThan(textBounds.height);

  const fit = page.locator(".main-proof-graph").getByRole("button", { name: "Fit to window", exact: true });
  await expect(fit).toBeInViewport({ ratio: 0.99 });
  await fit.click();
  await expect(stage.locator('.graph-node[aria-pressed="true"]')).toBeInViewport({ ratio: 0.99 });
});
