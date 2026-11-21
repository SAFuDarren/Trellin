import { expect, test, type Locator } from "@playwright/test";
import type { GraphSnapshot } from "../src/workbench/types";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";

test("Enter opens proof in the main workspace and Escape restores the panned map and selection", async ({ page }) => {
  await page.goto("/#session=fixture");
  const card = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const map = page.getByRole("application", { name: "Project map", exact: true });
  await card.click();
  await map.focus();
  await map.press("ArrowRight");
  const camera = () => page.locator(".project-canvas-plot").getAttribute("style");
  const previous = await camera();

  await card.press("Enter");
  const proof = page.locator("main .main-proof-graph");
  await expect(proof).toBeVisible();
  await expect(map).toBeHidden();
  await expect(page.locator("#review .graph-node")).toHaveCount(0);
  await expect(page.locator(".proof-return")).toBeFocused();
  await page.getByRole("button", { name: "References", exact: true }).click();
  await expect(proof.locator("[data-proof-view=references]")).toBeVisible();
  await page.getByRole("button", { name: "If withdrawn", exact: true }).click();
  await expect(proof.locator("[data-proof-view=impact]")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(map).toBeVisible();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  await expect(card).toBeFocused();
  expect(await camera()).toBe(previous);

  await page.getByRole("button", { name: "Collapse review column" }).click();
  await expect(page.locator("#review")).toBeHidden();
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Search or run a command" }).fill("open proof");
  await page.getByRole("option", { name: "Open proof", exact: true }).click();
  await expect(proof).toBeVisible();
  await page.locator(".proof-return").click();
  await expect(map).toBeVisible();
  await expect(page.locator("#review")).toBeHidden();
  await expect(card).toBeFocused();
  expect(await camera()).toBe(previous);
});

function proofScale(proof: Locator) {
  return proof.locator(".plot").evaluate((node) => new DOMMatrixReadOnly(getComputedStyle(node).transform).a);
}

function expectAllCardsInside(proof: Locator) {
  return expect
    .poll(() =>
      proof.locator(".stage").evaluate((stage) => {
        const viewport = stage.getBoundingClientRect();
        return [...stage.querySelectorAll(".graph-node")].every((node) => {
          const card = node.getBoundingClientRect();
          return (
            card.left >= viewport.left &&
            card.top >= viewport.top &&
            card.right <= viewport.right &&
            card.bottom <= viewport.bottom
          );
        });
      }),
    )
    .toBe(true);
}

function expectUnclippedTitles(cards: Locator) {
  return expect
    .poll(() =>
      cards
        .locator(".node-title")
        .evaluateAll((titles) =>
          titles.every((title) => title.scrollWidth <= title.clientWidth && title.scrollHeight <= title.clientHeight),
        ),
    )
    .toBe(true);
}

test("the seven-node proof opens fitted, full titles wrap and idle grips stay out of the way", async ({ page }) => {
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    const goal = graph.nodes.find((node) => node.kind === "goal")!;
    goal.title = "Symmetry of equality on the natural numbers under the recorded hypothesis";
    await route.fulfill({ response, json: graph });
  });
  await page.goto("/#session=fixture");
  await openProof(page);
  const proof = page.locator(".main-proof-graph");
  const cards = proof.locator(".graph-node");
  await expect(cards).toHaveCount(7);
  await expectAllCardsInside(proof);
  await expectUnclippedTitles(cards);
  const openingScale = await proofScale(proof);
  expect(openingScale).toBeGreaterThanOrEqual(1);
  await expect(proof.locator('.graph-node[data-kind="goal"] .node-title')).toHaveText(/recorded hypothesis$/);

  const frame = proof.locator(".graph-node-frame", { has: page.locator('.graph-node[data-kind="hypothesis"]') });
  const grip = frame.getByRole("button", { name: /^Resize node/ });
  await expect(grip).toHaveCSS("opacity", "0");
  await frame.locator(".graph-node").hover();
  await expect(grip).toHaveCSS("opacity", "1");
  await proof.getByRole("button", { name: "Zoom in" }).hover();
  await expect(grip).toHaveCSS("opacity", "0");
  await grip.focus();
  await expect(grip).toHaveCSS("opacity", "1");
  await frame.locator(".graph-node").click();
  await proof.getByRole("button", { name: "Zoom in" }).click();
  await expect(grip).toHaveCSS("opacity", "1");
  await expect.poll(() => proofScale(proof)).toBeGreaterThan(openingScale);
  await proof.getByRole("button", { name: "Fit to window" }).click();
  await expectAllCardsInside(proof);
  await expect.poll(() => proofScale(proof)).toBeCloseTo(openingScale);

  await page.locator(".proof-return").click();
  await page.setViewportSize({ width: 820, height: 720 });
  await openProof(page);
  await expect(cards).toHaveCount(7);
  await expectAllCardsInside(proof);
  await expectUnclippedTitles(cards);
});

test("a 55-node proof starts at a readable zoom in a minimum-size window and fits every node on request", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    const root = graph.nodes?.find((node) => node.kind === "goal");
    if (!root) throw new Error("The proof fixture requires a goal node.");
    const nodes = Array.from({ length: 54 }, (_, i) => ({
      id: `fixture-proof:${i}`,
      title: `Recorded proof step ${i + 1}`,
      kind: "obligation",
      status: "open",
    }));
    await route.fulfill({
      response,
      json: {
        ...graph,
        nodes: [root, ...nodes],
        blockers: [],
        edges: nodes.map((node, i) => ({
          source: node.id,
          target: nodes[i - 1]?.id ?? root.id,
          kind: "planned",
        })),
      },
    });
  });
  await page.goto("/#session=fixture");
  await openProof(page);
  const graph = page.locator(".main-proof-graph");
  await expect(graph.locator(".graph-node")).toHaveCount(55);
  await expect(graph.locator(".zoom-level")).toHaveText("80%");
  await expect(graph.locator(".graph-node[aria-pressed=true]")).toBeInViewport({ ratio: 0.95 });
  await expectUnclippedTitles(graph.locator(".graph-node"));
  const counts = graph.getByRole("status", { name: "Proof status counts" });
  await expect(counts).toContainText("Queued");
  expect(await counts.evaluate((row) => row.scrollWidth <= row.clientWidth)).toBe(true);
  const fit = graph.getByRole("button", { name: "Fit to window" });
  await expect(fit).toBeInViewport({ ratio: 1 });
  expect((await graph.boundingBox())?.height ?? 0).toBeGreaterThan(160);
  await fit.click();
  await expectAllCardsInside(graph);
  await expect.poll(() => proofScale(graph)).toBeLessThan(0.3);
  await page.locator(".proof-return").click();
  await openProof(page);
  await expect(graph.locator(".zoom-level")).toHaveText("80%");
});

test("resizing the inspector keeps the selected card and its nearest neighbor in the drawing", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/#session=fixture");
  const stage = page.getByRole("application", { name: "Project map", exact: true });
  const selected = mapClaim(page, "连续自然数之积的奇偶性");
  const neighbor = mapClaim(page, "单射映射的复合");
  await selected.click();
  for (let step = 0; step < 3; ++step) await stage.press("ArrowLeft");
  const divider = page.locator('[data-split="right"]');
  await divider.press("Shift+ArrowLeft");
  await expect(divider).toHaveAttribute("aria-valuenow", "388");
  await expect
    .poll(async () => {
      const drawing = (await stage.boundingBox())!;
      const cards = await Promise.all([selected.boundingBox(), neighbor.boundingBox()]);
      return cards.every((card) => card && card.x >= drawing.x && card.x + card.width <= drawing.x + drawing.width);
    })
    .toBe(true);
});
