import { expect, test, type Page } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";

// #274: the open proof lives in the address, so links, reload, Back and Forward work.
const CLAIM = "11111111-1111-4111-8111-111111111111";
const OTHER = "aaaaaaaa-0000-4000-8000-000000000001";
const OTHER_TITLE = "单射映射的复合";
const MISSING = "99999999-9999-4999-8999-999999999999";
const at = (claim: string, node?: string) =>
  new RegExp(
    `#workbench/claim/${claim}${node ? "/node/" + encodeURIComponent(node).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") : ""}$`,
  );
// The map is the bare page or #workbench: the launch token leaves the address without a section.
const MAP = /\/(#workbench)?$/;
const proof = (page: Page) => page.locator(".main-proof-graph");
const obligation = (page: Page, index: number) =>
  proof(page).getByRole("button", { name: new RegExp(`^Proof obligation ${index}`) });

test("opening a claim and a node updates the address, and Back and Forward move between them", async ({ page }) => {
  await page.goto("/#session=fixture");
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toBeVisible();
  await openProof(page);
  await expect(page).toHaveURL(at(CLAIM));
  await expect(proof(page)).toBeVisible();

  await obligation(page, 2).click();
  const second = (await obligation(page, 2).getAttribute("data-node"))!;
  await expect(page).toHaveURL(at(CLAIM, second));
  // Another node in the same proof replaces the entry instead of adding one.
  await obligation(page, 1).click();
  const first = (await obligation(page, 1).getAttribute("data-node"))!;
  await expect(page).toHaveURL(at(CLAIM, first));

  await page.goBack();
  await expect(page).toHaveURL(at(CLAIM));
  await expect(proof(page)).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(MAP);
  await expect(proof(page)).toBeHidden();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(at(CLAIM));
  await expect(proof(page)).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(at(CLAIM, first));
  await expect(obligation(page, 1)).toHaveAttribute("aria-pressed", "true");

  // The previously opened claim is one step behind the map.
  await openProof(page, OTHER_TITLE);
  await expect(page).toHaveURL(at(OTHER));
  await expect(page.getByRole("heading", { level: 2, name: "Other claim" })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(MAP);
  await page.goBack();
  await expect(page).toHaveURL(at(CLAIM, first));
  await expect(proof(page)).toBeVisible();
  await expect(obligation(page, 1)).toHaveAttribute("aria-pressed", "true");
});

test("a copied address opens the same claim and node in a new window and after reload", async ({ page, context }) => {
  await page.goto("/#session=fixture");
  await openProof(page);
  await obligation(page, 2).click();
  const node = (await obligation(page, 2).getAttribute("data-node"))!;
  await expect(page).toHaveURL(at(CLAIM, node));
  const link = page.url();

  const other = await context.newPage();
  // A new window of the same workbench already holds the launch token.
  await other.addInitScript(() => window.sessionStorage.setItem("research-multiplier-session", "fixture"));
  await other.goto(link);
  await expect(proof(other)).toBeVisible();
  await expect(obligation(other, 2)).toHaveAttribute("aria-pressed", "true");
  await expect(other).toHaveURL(at(CLAIM, node));

  await other.reload();
  await expect(obligation(other, 2)).toHaveAttribute("aria-pressed", "true");
  await expect(other).toHaveURL(at(CLAIM, node));
});

test("an unknown claim or node shows what still exists with a notice", async ({ page }) => {
  await page.route(`**/api/graph?claim=${MISSING}`, (route) =>
    route.fulfill({ status: 404, json: { error: "Research record unavailable in this project." } }),
  );
  await page.goto("/#session=fixture");
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toBeVisible();

  await page.evaluate((hash) => (window.location.hash = hash), `#workbench/claim/${MISSING}`);
  await expect(page.getByText("That claim is no longer in this project, so the project map is shown.")).toBeVisible();
  await expect(page).toHaveURL(MAP);
  await expect(proof(page)).toBeHidden();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toBeVisible();

  await page.evaluate((hash) => (window.location.hash = hash), `#workbench/claim/${CLAIM}/node/lean%3Aremoved`);
  await expect(page.getByText("That proof node is no longer in this claim, so its goal is shown.")).toBeVisible();
  await expect(page).toHaveURL(at(CLAIM));
  await expect(proof(page)).toBeVisible();
});
