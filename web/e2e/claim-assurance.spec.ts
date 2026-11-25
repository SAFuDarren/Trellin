import { expect, test, type Page } from "@playwright/test";
import type { AtlasClaim } from "../src/workbench/types";
import { railSpace } from "./navigation";

// #342: the board shows how each claim was established and which claims it relies on.

type Assurance = { kind: string | null; review: string | null; supporting: string[] };
type Fixture = {
  id: string;
  code: string;
  title: string;
  status: string;
  domain: string;
  assurance: Assurance;
  uses: string[];
};

const id = (n: number) => `34234234-0000-4000-8000-${String(n).padStart(12, "0")}`;
const none: Assurance = { kind: null, review: null, supporting: [] };
const CLAIMS: Fixture[] = [
  {
    id: id(1),
    code: "AL01",
    title: "Base lemma",
    status: "proved",
    domain: "Algebra",
    assurance: { kind: "lean", review: "approved", supporting: [] },
    uses: [],
  },
  {
    id: id(2),
    code: "AL02",
    title: "Middle step",
    status: "open",
    domain: "Algebra",
    assurance: { ...none, supporting: ["numeric"] },
    uses: [id(1)],
  },
  {
    id: id(3),
    code: "AL03",
    title: "Main theorem",
    status: "open",
    domain: "Algebra",
    assurance: none,
    uses: [id(1), id(2)],
  },
  {
    id: id(4),
    code: "AL04",
    title: "Certificate fact",
    status: "proved",
    domain: "Algebra",
    assurance: { kind: "exact_certificate", review: "pending", supporting: [] },
    uses: [],
  },
  { id: id(5), code: "AL05", title: "Unrelated claim", status: "open", domain: "Algebra", assurance: none, uses: [] },
];

function atlasClaim({ id, title, domain, status }: Fixture): AtlasClaim {
  return { id, title, domain, status, nodes: [{ id: `goal:${id}`, kind: "goal", status, title }], edges: [] };
}

async function board(page: Page, language?: "zh-CN") {
  if (language)
    await page.addInitScript((value) => localStorage.setItem("research-multiplier-language", value), language);
  const listed = CLAIMS.map(({ id, code, title, status, domain }) => ({ id, code, title, status, domain }));
  await page.route("**/api/claims", (route) => route.fulfill({ json: { claims: listed, claim_id: null } }));
  await page.route("**/api/atlas", (route) => route.fulfill({ json: { claims: CLAIMS.map(atlasClaim) } }));
  await page.route("**/api/project-canvas/claims", (route) =>
    route.fulfill({ json: { claims: CLAIMS.map((claim) => ({ ...claim })), claim_id: null } }),
  );
  await page.goto("/#session=fixture");
  if (language) await page.evaluate(() => (location.hash = "claims"));
  else await railSpace(page, "Work").click();
  await expect(page).toHaveURL(/#claims$/);
  return page.locator(".research-lanes");
}

const card = (page: Page, code: string) =>
  page
    .locator(".lane-card, .lane-row")
    .filter({ has: page.locator(".lane-code", { hasText: new RegExp(`^${code}$`) }) });
const peek = (page: Page) => page.locator(".work-peek");
const views = (page: Page) => page.locator("nav.section-nav .work-views");

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (sessionStorage.getItem("claim-assurance-test")) return;
    sessionStorage.setItem("claim-assurance-test", "1");
    localStorage.removeItem("trellin.claims.display");
    localStorage.removeItem("trellin.claims.collapsed");
    localStorage.removeItem("trellin.work.panel.folded");
  });
});

test("cards mark how claims were established and what they rely on, and tracing follows the pointer", async ({
  page,
}) => {
  const lanes = await board(page);
  await expect(card(page, "AL01").locator(".trust-badge")).toHaveText("Lean-checked, reviewed");
  await expect(card(page, "AL01").locator(".trust-badge")).toHaveAttribute(
    "title",
    "Proved by a Lean check, approved in Review",
  );
  await expect(card(page, "AL04").locator(".trust-badge")).toHaveText("Certificate-checked");
  await expect(card(page, "AL02").locator(".trust-badge")).toHaveText("Numeric support");
  await expect(card(page, "AL03").locator(".trust-badge")).toHaveCount(0);
  await expect(card(page, "AL05").locator(".trust-badge")).toHaveCount(0);

  await expect(card(page, "AL01").locator(".lane-deps")).toHaveText(/Used by 2/);
  await expect(card(page, "AL02").locator(".lane-deps")).toHaveText(/Uses AL01.*Used by 1/);
  await expect(card(page, "AL03").locator(".lane-deps")).toHaveText("Uses AL01, AL02");
  await expect(card(page, "AL05").locator(".lane-deps")).toHaveCount(0);

  // Nothing dims until a card with relations is pointed at.
  await expect(lanes).toHaveAttribute("data-tracing", "false");
  await card(page, "AL02").hover();
  await expect(lanes).toHaveAttribute("data-tracing", "true");
  await expect(card(page, "AL02")).toHaveAttribute("data-relation", "self");
  await expect(card(page, "AL01")).toHaveAttribute("data-relation", "uses");
  await expect(card(page, "AL03")).toHaveAttribute("data-relation", "usedBy");
  await expect(card(page, "AL05")).toHaveAttribute("data-relation", "other");
  await expect(card(page, "AL05")).toHaveCSS("opacity", "0.38");
  await expect(card(page, "AL01").getByText("Used by the selected claim")).toBeAttached();

  // A card with no relations traces nothing.
  await card(page, "AL05").hover();
  await expect(lanes).toHaveAttribute("data-tracing", "false");
});

test("the side panel lists dependencies and dependents and opens them, keeping the trace", async ({ page }) => {
  const lanes = await board(page);
  await card(page, "AL02").getByRole("button").first().click();
  await expect(peek(page)).toBeVisible();
  await page.mouse.move(0, 0);
  // The selected claim keeps the board traced after the pointer leaves.
  await expect(card(page, "AL01")).toHaveAttribute("data-relation", "uses");
  await expect(peek(page).locator(".trust-badge")).toHaveText("Numeric support");
  const relies = peek(page).getByRole("region", { name: "Relies on" });
  const usedBy = peek(page).getByRole("region", { name: "Used by" });
  await expect(relies.getByRole("button")).toHaveText([/AL01.*Base lemma.*Proved/]);
  await expect(usedBy.getByRole("button")).toHaveText([/AL03.*Main theorem/]);

  await relies.getByRole("button", { name: /Base lemma/ }).click();
  await expect(peek(page).getByRole("heading", { name: "Base lemma" })).toBeVisible();
  await expect(peek(page).locator(".trust-badge")).toHaveText("Lean-checked, reviewed");
  await expect(peek(page).getByRole("region", { name: "Used by" }).getByRole("button")).toHaveText([/AL02/, /AL03/]);
  await expect(lanes).toHaveAttribute("data-tracing", "true");
  await expect(card(page, "AL01")).toHaveAttribute("data-relation", "self");
});

test("Ready to advance lists open claims whose dependencies are all proved", async ({ page }) => {
  await board(page);
  const ready = views(page).getByRole("link", { name: /^Ready to advance/ });
  await expect(ready).toContainText("2");
  await ready.click();
  await expect(page).toHaveURL(/#claims\/advance$/);
  await expect(ready).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".lane-card")).toHaveCount(2);
  await expect(card(page, "AL02")).toBeVisible();
  await expect(card(page, "AL05")).toBeVisible();
  await expect(card(page, "AL03")).toHaveCount(0);
});

test("marks, dependencies, side panel and Ready to advance read in Chinese", async ({ page }) => {
  await board(page, "zh-CN");
  await expect(card(page, "AL01").locator(".trust-badge")).toHaveText("Lean 核验，已审阅");
  await expect(card(page, "AL04").locator(".trust-badge")).toHaveText("证书核验");
  await expect(card(page, "AL02").locator(".trust-badge")).toHaveText("数值支持");
  await expect(card(page, "AL01").locator(".lane-deps")).toHaveText("被 2 项引用");
  await expect(card(page, "AL03").locator(".lane-deps")).toHaveText("依赖 AL01, AL02");

  await card(page, "AL02").getByRole("button").first().click();
  await expect(peek(page).getByRole("region", { name: "依赖" }).getByRole("button")).toHaveText([/AL01/]);
  await expect(peek(page).getByRole("region", { name: "被引用" }).getByRole("button")).toHaveText([/AL03/]);
  await page.keyboard.press("Escape");

  const ready = views(page).getByRole("link", { name: /^现在可推进/ });
  await expect(ready).toContainText("2");
});
