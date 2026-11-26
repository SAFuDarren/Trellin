import { expect, test, type Locator, type Page } from "@playwright/test";
import { SECTIONS } from "../src/shell/route";
import { formatViolations, layoutViolations } from "./layout-invariants";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";
import { openDestination, railSpace } from "./navigation";

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

async function titleFont(title: Locator) {
  return title.evaluate((element) => {
    const { fontFamily, fontSize, fontWeight, lineHeight } = getComputedStyle(element);
    return { fontFamily, fontSize, fontWeight, lineHeight };
  });
}

async function panelGeometry(panel: Locator) {
  const selected = panel.locator('.nav-item[aria-current="page"]');
  await expect(selected).toBeVisible();
  return {
    panel: await panel.evaluate((element) => {
      const { left, width } = element.getBoundingClientRect();
      return { left, width };
    }),
    titleFont: await titleFont(panel.locator(".section-nav-header h1")),
    selection: await selected.evaluate((element) => {
      const { left, width } = element.getBoundingClientRect();
      const { paddingInlineStart, borderRadius } = getComputedStyle(element);
      return { left, width, paddingInlineStart, borderRadius };
    }),
  };
}

test("spaces share panel and header geometry and Home gives the map the full width", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.route("**/api/team-agents", (route) =>
    route.fulfill({
      json: {
        agents: [
          {
            id: "a9a3cb02-fa5c-40f4-bc2e-591c368e754e",
            revision: 1,
            connection_id: "9a33cb02-fa5c-40f4-bc2e-591c368e754e",
            name: "Geometry Agent",
            description: "Fixture for an active Chats row",
            avatar: "compass",
            instructions: "",
            starters: [],
          },
        ],
      },
    }),
  );
  await page.goto("/#session=fixture");
  const panel = page.locator("nav.section-nav");
  const header = page.locator("main .page-head");
  const heading = header.getByRole("heading");
  const rail = page.locator("nav.rail");
  const map = page.locator(".project-map-area");
  const footer = map.locator(".canvas-footer");

  await expect(heading).toHaveText("Project map");
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("separator", { name: "Resize navigation panel", exact: true })).toHaveCount(0);
  await expect(footer.getByRole("status")).toHaveText(/^Connected · \d+ claims?$/);
  const status = (await footer.getByRole("status").boundingBox())!;
  const zoom = (await footer.getByRole("group", { name: "Zoom", exact: true }).boundingBox())!;
  expect(status.x + status.width).toBeLessThanOrEqual(zoom.x);
  const railBox = (await rail.boundingBox())!;
  const mapBox = (await map.boundingBox())!;
  expect(mapBox.x).toBeCloseTo(railBox.x + railBox.width, 0);
  expect(mapBox.x + mapBox.width).toBeCloseTo(1440, 0);
  const pageFont = await titleFont(heading);
  const pageHeight = (await header.boundingBox())!.height;

  await railSpace(page, "Work").click();
  await expect(panel).toHaveAccessibleName("Work");
  // #326: Work opens on the claims board; its panel's records lead to the other Work pages.
  await expect(panel.locator(".space-entries .label")).toHaveText(["Decision records", "Agent tasks", "Library"]);
  const workPanel = await panelGeometry(panel);
  expect(workPanel.panel.left).toBeCloseTo(railBox.x + railBox.width, 0);
  expect(await titleFont(heading)).toEqual(pageFont);
  expect((await header.boundingBox())!.height).toBe(pageHeight);

  for (const space of ["Chats", "Team", "Automation"]) {
    await railSpace(page, space).click();
    await expect(panel).toHaveAccessibleName(space);
    if (space === "Chats") {
      await panel.getByRole("link", { name: /^Geometry Agent/ }).click();
    }
    expect(await panelGeometry(panel), `${space} panel`).toEqual(workPanel);
    expect(await titleFont(heading), `${space} page title`).toEqual(pageFont);
    expect((await header.boundingBox())!.height, `${space} page header height`).toBe(pageHeight);
  }
});

// Selection must never move rows: a Shift-click aimed at a row must land on that row (#272).
for (const layout of ["List", "Table"] as const) {
  test(`selecting ${layout.toLowerCase()} rows by pointer does not move any row`, async ({ page }) => {
    await page.goto("/#session=fixture");
    await openDestination(page, "Claims");
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
