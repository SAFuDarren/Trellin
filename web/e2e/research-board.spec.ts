import { expect, test, type Page } from "@playwright/test";
import type { AtlasClaim } from "../src/workbench/types";
import { railSpace } from "./navigation";

// #326: Work opens on the research board — claims in rows by domain and columns by status.

type BoardClaim = { id: string; code: string; title: string; status: string; domain: string | null };

const id = (n: number) => `44444444-4444-4444-8444-${String(n).padStart(12, "0")}`;
const CLAIMS: BoardClaim[] = [
  { id: id(1), code: "NT01", title: "Addition is commutative", status: "open", domain: "Number theory" },
  { id: id(2), code: "NT02", title: "Infinitely many primes", status: "open", domain: "Number theory" },
  { id: id(3), code: "NT03", title: "Divisibility is transitive", status: "open", domain: "Number theory" },
  { id: id(4), code: "NT04", title: "Bezout identity", status: "open", domain: "Number theory" },
  { id: id(5), code: "NT05", title: "Euclid lemma", status: "open", domain: "Number theory" },
  { id: id(6), code: "NT06", title: "Zero is a left identity", status: "proved", domain: "Number theory" },
  { id: id(7), code: "GR01", title: "Lagrange theorem", status: "open", domain: "Groups" },
  {
    id: id(8),
    code: "GR02",
    title: "Order divides the group order",
    status: "awaiting_interpretation_review",
    domain: "Groups",
  },
  { id: id(9), code: "GR03", title: "Subgroups of cyclic groups", status: "needs_recheck", domain: "Groups" },
  { id: id(10), code: "GR04", title: "Identity is unique", status: "proved", domain: "Groups" },
];

function atlasClaim(claim: BoardClaim): AtlasClaim {
  const obligations =
    claim.id === id(9) ? ["verified", "verified", "failed", "open"] : claim.id === id(7) ? ["verified", "open"] : [];
  return {
    id: claim.id,
    title: claim.title,
    domain: claim.domain,
    status: claim.status,
    nodes: [
      { id: `goal:${claim.id}`, kind: "goal", status: claim.status, title: claim.title },
      ...obligations.map((status, index) => ({
        id: `obligation:${claim.id}:${index}`,
        kind: "obligation",
        status,
        title: `Obligation ${index + 1}`,
      })),
    ],
    edges: [],
  };
}

async function board(page: Page, claims = CLAIMS) {
  await page.route("**/api/claims", (route) => route.fulfill({ json: { claims, claim_id: null } }));
  await page.route("**/api/atlas", (route) => route.fulfill({ json: { claims: claims.map(atlasClaim) } }));
  await page.route("**/api/project-canvas/claims", (route) =>
    route.fulfill({ json: { claims: claims.map((claim) => ({ ...claim })), claim_id: null } }),
  );
  await page.goto("/#session=fixture");
  await railSpace(page, "Work").click();
  await expect(page).toHaveURL(/#claims$/);
  return page.locator(".research-lanes");
}

const lane = (page: Page, name: string) => page.getByRole("region", { name, exact: true });
const cell = (page: Page, laneName: string, status: string) =>
  page.getByRole("group", { name: `${laneName}, ${status}`, exact: true });

test.beforeEach(async ({ page }) => {
  // Start each test from the default display; reloads inside a test keep what it saved.
  await page.addInitScript(() => {
    if (sessionStorage.getItem("research-board-test")) return;
    sessionStorage.setItem("research-board-test", "1");
    localStorage.removeItem("trellin.claims.display");
    localStorage.removeItem("trellin.claims.collapsed");
  });
});

test("Work opens on the board with rows by domain, obligation progress and a limited cell", async ({ page }) => {
  const lanes = await board(page);
  await expect(lanes).toBeVisible();
  await expect(page.locator(".page-head")).toContainText("10 claims");
  await expect(lane(page, "Groups").getByRole("button", { name: /Groups/ })).toContainText("4 claims · 1 proved");

  const lagrange = cell(page, "Groups", "In progress").getByRole("button", { name: /Lagrange theorem/ });
  await expect(lagrange).toContainText("GR01");
  await expect(lagrange).toContainText("1/2");
  await expect(cell(page, "Groups", "Needs recheck")).toContainText("2/4 · 1 failed");

  // Number theory has five claims in progress: three show, then "Show 2 more".
  const busy = cell(page, "Number theory", "In progress");
  await expect(busy.getByRole("listitem")).toHaveCount(3);
  await busy.getByRole("button", { name: "Show 2 more" }).click();
  await expect(busy.getByRole("listitem")).toHaveCount(5);

  // Proved claims show as one line and still open their preview.
  const proved = cell(page, "Groups", "Proved").getByRole("button", { name: /Identity is unique/ });
  await expect(proved).toContainText("GR04");
  await proved.click();
  await expect(proved).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Open in Workbench" })).toBeVisible();
});

test("a folded row shows counts and stays folded after reload", async ({ page }) => {
  await board(page);
  const toggle = lane(page, "Number theory").getByRole("button", { name: /Number theory/ });
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(cell(page, "Number theory", "In progress")).toHaveCount(0);
  await expect(lane(page, "Number theory").locator(".lane-folded").first()).toContainText("5");
  await page.reload();
  await expect(lane(page, "Number theory").getByRole("button", { name: /Number theory/ })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  await expect(cell(page, "Groups", "In progress")).toBeVisible();
});

test("filters show as removable chips, search matches codes, and Display changes the layout", async ({ page }) => {
  await board(page);
  await expect(page.locator(".board-chips")).toHaveCount(0);

  await page.getByRole("button", { name: "Filter", exact: true }).click();
  const proved = page.getByRole("menuitemcheckbox", { name: "Proved" });
  await expect(proved).toHaveAttribute("aria-checked", "false");
  await proved.click();
  await expect(page.getByRole("button", { name: "Filter · 1" })).toBeVisible();
  await expect(page.locator(".page-head")).toContainText("2 of 10");
  await expect(page.getByRole("group", { name: "Active filters" })).toContainText("Status: Proved");
  await expect(page.locator(".lane-card")).toHaveCount(0);
  await page.getByRole("button", { name: "Remove Status: Proved" }).click();
  await expect(page.locator(".board-chips")).toHaveCount(0);

  await page.getByRole("button", { name: "Search claims" }).click();
  await page.getByRole("searchbox").fill("gr03");
  await expect(page.locator(".page-head")).toContainText("1 of 10");
  await expect(page.getByRole("button", { name: /Subgroups of cyclic groups/ })).toBeVisible();
  await page.getByRole("searchbox").press("Escape");
  await expect(page.locator(".page-head")).toContainText("10 claims");

  await page.getByRole("button", { name: "Display", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "Cards", exact: true }).click();
  await expect(cell(page, "Groups", "Proved").locator(".lane-card")).toHaveCount(1);
  await page.getByRole("button", { name: "Display", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "Status board" }).click();
  await expect(page.locator(".research-lanes")).toHaveCount(0);
  await expect(page.locator(".board-column")).not.toHaveCount(0);
  await page.reload();
  await expect(page.locator(".board-column")).not.toHaveCount(0);
});

test("New claim opens New theorem", async ({ page }) => {
  await board(page);
  await page.getByRole("button", { name: "New claim" }).click();
  await expect(page.getByRole("dialog").getByText("Destination · Current project graph")).toBeVisible();
});

test("the board reads in Chinese", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("research-multiplier-language", "zh-CN"));
  await page.route("**/api/claims", (route) => route.fulfill({ json: { claims: CLAIMS, claim_id: null } }));
  await page.route("**/api/atlas", (route) => route.fulfill({ json: { claims: CLAIMS.map(atlasClaim) } }));
  await page.goto("/#session=fixture");
  await page.evaluate(() => {
    location.hash = "#claims";
  });
  await expect(page.locator(".page-head")).toContainText("10 项课题");
  await expect(page.getByRole("button", { name: "显示", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建课题" })).toBeVisible();
  await expect(
    page
      .getByRole("group", { name: "Number theory，进行中", exact: true })
      .getByRole("button", { name: "再显示 2 项" }),
  ).toBeVisible();
});

test("the board fits a wide window and drops empty status columns", async ({ page }) => {
  const settled = CLAIMS.filter((claim) => claim.status === "open" || claim.status === "proved");
  const lanes = await board(page, settled);
  await expect(page.locator(".lanes-column")).toHaveText([/^In progress/, /^Proved/]);
  expect(await lanes.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0);
  // Cards keep a reading width instead of stretching across a wide window.
  const width = (await page.locator(".lane-card").first().boundingBox())!.width;
  expect(width).toBeLessThanOrEqual(340);
});

test("in a narrow window the domain names stay put while cards slide under them", async ({ page }) => {
  await page.setViewportSize({ width: 860, height: 720 });
  const lanes = await board(page);
  const code = page.getByText("GR01", { exact: true });
  // Nothing covers a card before the board scrolls.
  expect(
    await code.evaluate((element) => {
      const box = element.getBoundingClientRect();
      return document.elementFromPoint(box.left + 2, box.top + box.height / 2) === element;
    }),
  ).toBe(true);
  const name = lane(page, "Groups").getByRole("button", { name: /Groups/ });
  const corner = page.locator(".lanes-corner");
  const before = (await name.boundingBox())!.x;
  expect(await lanes.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await expect(lanes).toHaveAttribute("data-scrolled", "false");
  await lanes.evaluate((element) => element.scrollBy({ left: 240 }));
  await expect(lanes).toHaveAttribute("data-scrolled", "true");
  await expect.poll(async () => (await name.boundingBox())!.x).toBeCloseTo(before, 0);
  expect((await corner.boundingBox())!.x).toBeCloseTo(before, 0);
});

test("a wide window shows every column without scrolling sideways", async ({ page }) => {
  await page.setViewportSize({ width: 1790, height: 900 });
  const lanes = await board(page);
  await expect(page.locator(".lanes-column")).toHaveCount(4);
  expect(await lanes.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0);
});
