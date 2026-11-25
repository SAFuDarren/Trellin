import { expect, test, type Page } from "@playwright/test";
import type { GraphSnapshot } from "../src/workbench/types";
import { openProof } from "./project-map";
import { openDestination, railSpace } from "./navigation";
import { startOnStatusBoard } from "./claims-display";

const CLAIM_TITLE = "自然数加零的对称性";
const LONG_TITLE = "Symmetry of equality on the natural numbers";
const WORDING = "For each natural number, adding zero on the left returns that number.";
const FORMALIZATION = "∀ n : Nat, n = 0 + n";

async function open(page: Page, singleGoal = false) {
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    const goal = graph.nodes.find((node) => node.kind === "goal");
    if (goal) {
      goal.title = LONG_TITLE;
      goal.statement = WORDING;
      if (singleGoal) {
        graph.nodes = [goal];
        graph.edges = [];
        graph.focus_id = goal.id;
      }
    }
    await route.fulfill({ response, json: graph });
  });
  await page.goto("/#session=fixture");
}

test("a claim expands and collapses in place with its recorded statement", async ({ page }) => {
  await startOnStatusBoard(page);
  await open(page);
  await openDestination(page, "Claims");
  const card = page.locator(".board").getByRole("button", { name: new RegExp(CLAIM_TITLE) });
  await expect(card).toHaveAttribute("aria-expanded", "false");
  await card.click();
  await expect(card).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#section-title")).toHaveText("Work");
  await expect(page.getByText(WORDING, { exact: true })).toBeVisible();
  await expect(page.getByText(FORMALIZATION, { exact: true })).toBeVisible();
  await card.click();
  await expect(page.getByText(WORDING, { exact: true })).toHaveCount(0);

  await card.click();
  await page.getByRole("button", { name: "Open in Workbench", exact: true }).click();
  await expect(page.locator("#node-detail").getByRole("heading", { name: CLAIM_TITLE })).toBeVisible();
  await expect(page.getByRole("application", { name: "Project map", exact: true })).toBeVisible();
});

test("long titles stay inside their proof node when zooming", async ({ page }) => {
  await open(page, true);
  await openProof(page);
  const card = page.getByRole("button", { name: new RegExp(`^${LONG_TITLE}`) });
  const label = card.locator(".node-title");
  await expect(card).toHaveAttribute("title", LONG_TITLE);
  await expect(label).toHaveText(LONG_TITLE);
  await page.locator(".main-proof-graph").getByRole("button", { name: "Fit to window", exact: true }).click();
  await expect(card).toBeInViewport();
  const width = await label.evaluate((title) => {
    const node = title.closest(".graph-node")!;
    return {
      outside: title.getBoundingClientRect().right - node.getBoundingClientRect().right,
      clipped: title.scrollWidth > title.clientWidth || title.scrollHeight > title.clientHeight,
      contained: getComputedStyle(node).overflowX === "hidden",
    };
  });
  expect(width.outside).toBeLessThanOrEqual(0);
  expect(width.clipped).toBe(false);
  expect(width.contained).toBe(true);
});

test("the proof inspector keeps the complete formula and Lean text", async ({ page }) => {
  const title = "A detailed title for the second obligation under the recorded hypothesis and its context";
  const formula = "∀ n, n = 0 + n" + " ∧ n = n".repeat(80);
  const formalization = "∀ n : Nat, n = 0 + n" + "\n  ∧ n = n".repeat(100);
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    const obligation = graph.nodes.find((node) => node.title === "Proof obligation 2")!;
    Object.assign(obligation, { title, statement: WORDING, formula, formalization });
    await route.fulfill({ response, json: graph });
  });
  await page.goto("/#session=fixture");
  await openProof(page);
  const card = page.locator(".main-proof-graph .graph-node", { hasText: title });
  await card.click();
  await expect(card).not.toContainText(formula.slice(0, 16));
  const details = page.locator("#node-detail");
  await expect(details.getByRole("heading", { name: title, exact: true })).toBeVisible();
  await expect(details.getByText(WORDING, { exact: true })).toBeVisible();
  expect(await details.locator(".formula").last().textContent()).toBe(formula);
  expect(await details.locator(".lean").textContent()).toBe(formalization);
});

test("Library shows the same state in the list and the inspector", async ({ page }) => {
  await open(page);
  await openDestination(page, "Library");
  const workspace = page.locator(".page-body");
  const admitted = workspace.getByRole("row", { name: /Nat.add_zero/ });
  const needsRecheck = workspace.getByRole("row", { name: /Nat.mul_comm/ });
  await expect(admitted.locator('.status-dot[data-tone="ok"]')).toBeVisible();
  await expect(admitted.getByText("In use", { exact: true })).toBeVisible();
  await needsRecheck.click();
  const inspector = workspace.getByRole("complementary", { name: "Nat.mul_comm", exact: true });
  for (const surface of [needsRecheck, inspector]) {
    await expect(surface.locator('.status-dot[data-tone="warn"]')).toBeVisible();
    await expect(surface.getByText("Needs recheck", { exact: true })).toBeVisible();
  }
});

const LIBRARY_PROSE = "Adding zero on the left leaves a natural number unchanged.";
const LIBRARY_PROSE_ID = "33333333-3333-4333-8333-333333333333";

for (const { width, height, colorScheme } of [
  { width: 1280, height: 800, colorScheme: "dark" },
  { width: 640, height: 460, colorScheme: "light" },
] as const) {
  test(`Library name typography and row editing work at ${width}×${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme });
    await page.route("**/api/library/item?*", (route) => {
      const id = new URL(route.request().url()).searchParams.get("version");
      expect(id).toBe(LIBRARY_PROSE_ID);
      return route.fulfill({
        json: {
          id,
          version: 1,
          sha256: "a".repeat(64),
          natural: LIBRARY_PROSE,
          lean: "∀ n : Nat, 0 + n = n",
          mode: "basis",
          available: true,
        },
      });
    });
    await page.goto("/#session=fixture");
    await openDestination(page, "Library");

    const table = page.getByRole("table");
    await expect(table.getByRole("columnheader", { name: "Actions", exact: true })).toHaveCount(0);
    const declaration = "Nat.add_zero";
    const declarationName = table.getByText(declaration, { exact: true });
    const identifierFont = await declarationName.evaluate((element) => getComputedStyle(element).fontFamily);
    const bodyFont = await page.locator("body").evaluate((element) => getComputedStyle(element).fontFamily);
    expect(identifierFont).not.toBe(bodyFont);
    expect(identifierFont).toContain("monospace");
    const declarationInspector = page.getByRole("complementary", { name: declaration, exact: true });
    await expect(declarationInspector.getByRole("heading", { name: declaration })).toHaveCSS(
      "font-family",
      identifierFont,
    );

    const proseRow = table.getByRole("row").filter({ hasText: LIBRARY_PROSE });
    const proseName = proseRow.getByText(LIBRARY_PROSE, { exact: true });
    await expect(proseName).toHaveCSS("font-family", bodyFont);
    const action = proseRow.getByRole("button", { name: `Edit statement for ${LIBRARY_PROSE}`, exact: true });
    await expect(action).toHaveCSS("opacity", "0");
    await proseName.hover();
    await expect(action).toHaveCSS("opacity", "1");
    await railSpace(page, "Work").hover();
    await expect(action).toHaveCSS("opacity", "0");

    await proseRow.focus();
    await page.keyboard.press("Tab");
    await expect(action).toBeFocused();
    await expect(action).toHaveCSS("opacity", "1");
    await expect(action).toHaveText("");
    await expect(action).toBeInViewport({ ratio: 0.99 });
    const bounds = (await action.boundingBox())!;
    expect(bounds.width).toBe(bounds.height);
    expect(bounds.width).toBeGreaterThanOrEqual(24);

    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Revise Library statement", exact: true });
    await expect(dialog.getByRole("textbox", { name: "Statement", exact: true })).toHaveValue(LIBRARY_PROSE);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(action).toBeFocused();

    const proseInspector = page.getByRole("complementary", { name: LIBRARY_PROSE, exact: true });
    await expect(proseInspector.getByRole("heading", { name: LIBRARY_PROSE })).toHaveCSS("font-family", bodyFont);
    await expect(proseInspector.getByRole("button", { name: "Edit statement", exact: true })).toBeVisible();
  });
}
