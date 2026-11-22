import { expect, test, type Locator, type Page } from "@playwright/test";
import type { GraphSnapshot } from "../src/workbench/types";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";
import { openDestination } from "./navigation";

type Contrast = { foreground: string; background: string };

async function contrast(element: Locator, options: Contrast) {
  return element.evaluate((node, { foreground, background }) => {
    const style = getComputedStyle(node);
    const root = getComputedStyle(document.documentElement);
    const context = document.createElement("canvas").getContext("2d")!;
    function luminance(property: string) {
      const value = (property.startsWith("--") ? root : style).getPropertyValue(property).trim();
      const color = ["box-shadow", "background-image"].includes(property)
        ? value.match(/(?:rgba?|color)\([^)]*\)/)?.[0]
        : value;
      if (!color) throw new Error(`No color declared for ${property}: ${value}`);
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const data = context.getImageData(0, 0, 1, 1).data;
      if (data[3] !== 255) throw new Error(`Expected an opaque color for ${property}: ${color}`);
      const linear = Array.from(data.slice(0, 3), (value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
    }
    const values = [luminance(foreground), luminance(background)];
    return (Math.max(...values) + 0.05) / (Math.min(...values) + 0.05);
  }, options);
}

for (const colorScheme of ["light", "dark"] as const) {
  test(`Navy focus, selection, running and action contrast in ${colorScheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
    await page.goto("/#session=fixture");
    const selected = mapClaim(page, FIXTURE_CLAIM_TITLE);
    const other = mapClaim(page, "单射映射的复合");
    await expect(selected).toHaveAttribute("aria-pressed", "true");
    await expect(other).toHaveAttribute("aria-pressed", "false");
    const accent = colorScheme === "light" ? "rgb(22, 35, 63)" : "rgb(164, 183, 223)";
    await expect(selected).toHaveCSS("border-top-color", accent);
    expect(await contrast(selected, { foreground: "border-top-color", background: "--canvas" })).toBeGreaterThanOrEqual(
      3,
    );
    expect(await contrast(selected, { foreground: "color", background: "background-color" })).toBeGreaterThanOrEqual(
      4.5,
    );

    await other.hover();
    await expect(other).not.toHaveCSS(
      "background-color",
      await selected.evaluate((node) => getComputedStyle(node).backgroundColor),
    );
    await page.getByRole("application", { name: "Project map", exact: true }).focus();
    await expect(page.getByRole("application", { name: "Project map", exact: true })).toHaveCSS(
      "outline-color",
      accent,
    );
    expect(
      await contrast(page.getByRole("application", { name: "Project map", exact: true }), {
        foreground: "outline-color",
        background: "--canvas",
      }),
    ).toBeGreaterThanOrEqual(3);

    const activeNav = page.locator("nav.rail a[aria-current='page']");
    await expect(activeNav).toHaveCSS("box-shadow", "none");
    expect(await contrast(activeNav, { foreground: "color", background: "background-color" })).toBeGreaterThanOrEqual(
      4.5,
    );
    const live = page.locator(".rail-badge.live");
    await expect(live).toHaveText("2");
    expect(await contrast(live, { foreground: "color", background: "background-color" })).toBeGreaterThanOrEqual(4.5);

    await page.getByRole("button", { name: "Add theorem", exact: true }).click();
    const primary = page.getByRole("dialog", { name: "Add theorem", exact: true }).locator("button.primary");
    await expect(primary).toHaveCSS("background-color", accent);
    expect(await contrast(primary, { foreground: "color", background: "background-color" })).toBeGreaterThanOrEqual(
      4.5,
    );
    await primary.hover();
    expect(await contrast(primary, { foreground: "color", background: "background-color" })).toBeGreaterThanOrEqual(
      4.5,
    );
  });
}

async function stateOf(item: Locator) {
  return item.evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      fill: style.backgroundColor,
      edge: style.borderTopColor,
      ring: style.boxShadow,
      focus: node.matches(":focus-visible"),
    };
  });
}

async function expectKeyboardRing(page: Page, item: Locator, surface: string) {
  await page.keyboard.press("Tab");
  await item.focus();
  await expect(item).toHaveCSS("outline-style", "solid");
  await expect(item).toHaveCSS("outline-width", "2px");
  await expect(item).toHaveCSS("outline-offset", "2px");
  expect(await contrast(item, { foreground: "outline-color", background: surface })).toBeGreaterThanOrEqual(3);
}

for (const colorScheme of ["light", "dark"] as const) {
  test(`map cards, proof nodes and review rows distinguish selection, hover and focus in ${colorScheme}`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
    await page.route("**/api/graph?*", async (route) => {
      const response = await route.fetch();
      const graph = (await response.json()) as GraphSnapshot;
      const reduction = graph.nodes.find((node) => node.kind === "reduction");
      if (reduction) reduction.status = "needs_recheck";
      await route.fulfill({ response, json: graph });
    });
    await page.goto("/#session=fixture");

    const selected = mapClaim(page, FIXTURE_CLAIM_TITLE);
    const other = mapClaim(page, "单射映射的复合");
    await expect(selected).toHaveAttribute("aria-pressed", "true");
    const selectedMap = await stateOf(selected);
    expect(selectedMap.ring).not.toBe("none");
    expect(await contrast(selected, { foreground: "border-top-color", background: "--canvas" })).toBeGreaterThanOrEqual(
      3,
    );
    expect(
      await contrast(selected, { foreground: "border-top-color", background: "background-color" }),
    ).toBeGreaterThanOrEqual(3);
    await other.hover();
    const hoveredMap = await stateOf(other);
    expect(hoveredMap.fill).not.toBe(selectedMap.fill);
    expect(hoveredMap.edge).not.toBe(selectedMap.edge);
    expect(hoveredMap.focus).toBe(false);
    await expectKeyboardRing(page, other, "--canvas");
    await expect(other).toHaveAttribute("aria-pressed", "false");
    await selected.hover();
    expect(await stateOf(selected)).toEqual(selectedMap);

    const canvas = page.getByRole("application", { name: "Project map", exact: true });
    if (colorScheme === "light") {
      const zoom = page.locator(".project-canvas").getByRole("button", { name: "Zoom to 100%" });
      await zoom.click();
      await expect(zoom).toHaveText("100%");
      await expect(canvas).toHaveCSS("background-image", /radial-gradient/);
      await expect(canvas).toHaveCSS("background-size", "24px 24px");
      const dot = await contrast(canvas, { foreground: "background-image", background: "--canvas" });
      expect(dot).toBeGreaterThan(1.3);
      expect(dot).toBeLessThan(2);
    }

    await openProof(page);
    const graph = page.locator(".main-proof-graph");
    const selectedNode = graph.locator('.graph-node[aria-pressed="true"]');
    const otherNode = graph.getByRole("button", { name: /^Proof obligation 1,/ });
    const selectedProof = await stateOf(selectedNode);
    expect(selectedProof.ring).not.toBe("none");
    expect(
      await contrast(selectedNode, { foreground: "border-top-color", background: "--canvas" }),
    ).toBeGreaterThanOrEqual(3);
    expect(
      await contrast(selectedNode, { foreground: "border-top-color", background: "background-color" }),
    ).toBeGreaterThanOrEqual(3);
    await otherNode.hover();
    const hoveredProof = await stateOf(otherNode);
    expect(hoveredProof.fill).not.toBe(selectedProof.fill);
    expect(hoveredProof.edge).not.toBe(selectedProof.edge);
    expect(hoveredProof.focus).toBe(false);
    await expectKeyboardRing(page, otherNode, "--canvas");
    await expect(otherNode).toHaveAttribute("aria-pressed", "false");

    const reviewLink = page.locator("a.queue-item").first();
    await reviewLink.hover();
    expect((await stateOf(reviewLink)).ring).toBe("none");
    await expectKeyboardRing(page, reviewLink, "--side");

    const review = page.locator("button.queue-item").first();
    await review.hover();
    const hoveredRow = await stateOf(review);
    expect(hoveredRow.ring).toBe("none");
    await review.click();
    await expect(review).toHaveAttribute("aria-current", "true");
    const selectedRow = await stateOf(review);
    expect(selectedRow.fill).not.toBe(hoveredRow.fill);
    expect(selectedRow.ring).toContain("inset");
    expect(await contrast(review, { foreground: "box-shadow", background: "--side" })).toBeGreaterThanOrEqual(3);
    expect(await contrast(review, { foreground: "box-shadow", background: "background-color" })).toBeGreaterThanOrEqual(
      3,
    );
    await expectKeyboardRing(page, review, "--side");
    expect((await stateOf(review)).ring).toBe(selectedRow.ring);
  });
}

test("the board has a distinct page, section and card heading scale", async ({ page }) => {
  await page.goto("/#session=fixture");
  await openDestination(page, "Claims");
  await expect(page.locator(".page-head h2")).toHaveCSS("font-size", "22px");
  const column = page.locator(".board-column").first();
  await expect(column.locator("header").first()).toHaveCSS("font-size", "16px");
  await expect(column.locator(".board-card .title").first()).toHaveCSS("font-size", "14px");
});
