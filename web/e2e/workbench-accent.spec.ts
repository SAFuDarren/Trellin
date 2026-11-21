import { expect, test, type Locator } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

type Contrast = { foreground: string; background: string };

async function contrast(element: Locator, options: Contrast) {
  return element.evaluate((node, { foreground, background }) => {
    const style = getComputedStyle(node);
    const root = getComputedStyle(document.documentElement);
    const context = document.createElement("canvas").getContext("2d")!;
    function luminance(property: string) {
      const color = (property.startsWith("--") ? root : style).getPropertyValue(property).trim();
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
    await expect(activeNav).toHaveCSS("box-shadow", new RegExp(accent.replace(/[()]/g, "\\$&")));
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

test("the board has a distinct page, section and card heading scale", async ({ page }) => {
  await page.goto("/#session=fixture");
  await page.locator("nav.rail").getByRole("link", { name: "Claims", exact: true }).click();
  await expect(page.locator(".page-head h2")).toHaveCSS("font-size", "22px");
  const column = page.locator(".board-column").first();
  await expect(column.locator("header").first()).toHaveCSS("font-size", "16px");
  await expect(column.locator(".board-card .title").first()).toHaveCSS("font-size", "14px");
});
