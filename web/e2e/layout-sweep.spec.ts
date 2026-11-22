import { expect, test, type Page } from "@playwright/test";
import { SECTIONS } from "../src/shell/route";
import { formatViolations, layoutViolations } from "./layout-invariants";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";

// #283: every destination, plus the screens that open from it, keeps the layout invariants
// at three window sizes and in both themes. A new destination is covered by adding it to
// SECTIONS; a new kind of screen by adding one entry to SCREENS.

const SIZES = [
  { width: 1440, height: 900 },
  { width: 1024, height: 700 },
  { width: 640, height: 460 },
] as const;
const THEMES = ["light", "dark"] as const;

type Screen = { name: string; open: (page: Page) => Promise<void> };

const SCREENS: Screen[] = [
  ...SECTIONS.map((section) => ({
    name: `#${section}`,
    open: async (page: Page) => {
      await page.goto(`/#${section}`);
      // Compact layouts may show a list before its main pane (Chats), so wait for the page, not its visibility.
      await expect(page.locator("main").first()).toBeAttached();
    },
  })),
  {
    // The inspector takes room from the canvas, so the map must still fit beside it.
    name: "project map with a claim selected",
    open: async (page) => {
      await page.goto("/#workbench");
      await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
      await expect(page.locator("#review")).toBeVisible();
    },
  },
  {
    name: "proof graph",
    open: async (page) => {
      await page.goto("/#workbench");
      await openProof(page);
      await expect(page.locator(".main-proof-graph .graph-node").first()).toBeVisible();
    },
  },
];

async function settle(page: Page) {
  // Wait for data and for the camera to fit, without depending on a particular screen.
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

for (const theme of THEMES) {
  for (const size of SIZES) {
    test(`layout invariants hold on every screen at ${size.width}×${size.height} in ${theme}`, async ({ page }) => {
      test.setTimeout(120_000);
      const errors: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
      });
      page.on("pageerror", (error) => errors.push(error.message));
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.setViewportSize(size);
      await page.goto("/#session=fixture");
      const report: string[] = [];
      for (const screen of SCREENS) {
        await screen.open(page);
        await settle(page);
        const violations = await layoutViolations(page);
        if (violations.length) report.push(`${screen.name}\n${formatViolations(violations)}`);
      }
      expect(report.join("\n\n"), "layout invariants").toBe("");
      expect(errors, "console errors").toEqual([]);
    });
  }
}

// Selection must never move rows: a Shift-click aimed at a row must land on that row (#272).
for (const layout of ["List", "Table"] as const) {
  test(`selecting ${layout.toLowerCase()} rows by pointer does not move any row`, async ({ page }) => {
    await page.goto("/#session=fixture");
    await page.locator("nav.rail").getByRole("link", { name: "Claims", exact: true }).click();
    await page.getByRole("button", { name: "Display", exact: true }).click();
    await page.getByRole("menuitemradio", { name: layout, exact: true }).click();
    const view = page.getByRole("region", { name: "Select items in All claims" });
    const boxes = view.getByRole("checkbox");
    await expect(boxes).toHaveCount(5);
    const before = await Promise.all((await boxes.all()).map((box) => box.boundingBox()));
    const at = (index: number) => ({
      x: before[index]!.x + before[index]!.width / 2,
      y: before[index]!.y + before[index]!.height / 2,
    });

    await page.mouse.click(at(0).x, at(0).y);
    await expect(view.getByRole("group", { name: "Selection actions" })).toBeVisible();
    expect(await Promise.all((await boxes.all()).map((box) => box.boundingBox()))).toEqual(before);

    await page.keyboard.down("Shift");
    await page.mouse.click(at(3).x, at(3).y);
    await page.keyboard.up("Shift");
    await expect(view.getByText("4 selected", { exact: true })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(view.getByRole("group", { name: "Selection actions" })).toHaveCount(0);
    expect(await Promise.all((await boxes.all()).map((box) => box.boundingBox()))).toEqual(before);
  });
}
