import { expect, test, type Page } from "@playwright/test";
import type { GraphSnapshot } from "../src/workbench/types";
import { openProof } from "./project-map";

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
  await open(page);
  await page.locator("nav.rail").getByRole("link", { name: "Claims", exact: true }).click();
  const card = page.getByRole("button", { name: new RegExp(CLAIM_TITLE) });
  await expect(card).toHaveAttribute("aria-expanded", "false");
  await card.click();
  await expect(card).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#section-title")).toHaveText("Claims");
  await expect(page.getByText(WORDING, { exact: true })).toBeVisible();
  await expect(page.getByText(FORMALIZATION, { exact: true })).toBeVisible();
  await card.click();
  await expect(page.getByText(WORDING, { exact: true })).toHaveCount(0);

  await card.click();
  await page.getByRole("button", { name: "Open in Workbench", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(CLAIM_TITLE);
});

test("long titles stay inside their proof node when zooming", async ({ page }) => {
  await open(page, true);
  await openProof(page);
  const card = page.getByRole("button", { name: new RegExp(`^${LONG_TITLE}`) });
  const label = card.locator(".node-title");
  await expect(card).toHaveAttribute("title", LONG_TITLE);
  await expect(label).toHaveText(LONG_TITLE);
  await page.getByRole("button", { name: "Fit to window", exact: true }).click();
  await expect(page.locator(".zoom-level")).toHaveText("125%");
  const width = await label.evaluate((title) => {
    const node = title.closest(".graph-node")!;
    return {
      outside: title.getBoundingClientRect().right - node.getBoundingClientRect().right,
      clipped: title.scrollWidth > title.clientWidth,
      contained: getComputedStyle(node).overflowX === "hidden",
    };
  });
  expect(width.outside).toBeLessThanOrEqual(0);
  expect(width.clipped).toBe(true);
  expect(width.contained).toBe(true);
});

test("Library shows the same state in the list and the inspector", async ({ page }) => {
  await open(page);
  await page.locator("nav.rail").getByRole("link", { name: "Library", exact: true }).click();
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
