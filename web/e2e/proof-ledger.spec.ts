import { expect, test } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

// #356: the inspector shows what earlier rounds left behind, from the graph's own records.

const ROUTE = "55555555-5555-4555-8555-555555555555";

test("a claim's ledger lists checked lemmas and tried routes and opens their nodes", async ({ page }) => {
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = await response.json();
    graph.ledger = {
      lemmas: [
        {
          lemma_id: "44444444-4444-4444-8444-444444444444",
          proposition: "∀ n : Nat, 0 + n = n",
          verification_attempt_id: null,
          recorded_at: "2026-11-26T10:01:00",
          used_by: [ROUTE],
        },
        {
          lemma_id: "66666666-6666-4666-8666-666666666666",
          proposition: "∀ n : Nat, n * 1 = n",
          verification_attempt_id: null,
          recorded_at: "2026-11-26T10:02:00",
          used_by: [],
        },
      ],
      tried_routes: [
        {
          implication_id: ROUTE,
          status: "closure_rejected",
          obligations: ["∀ n, 0 + n = n", "∀ n, n + 0 = n"],
          open_obligation: "∀ n, n + 0 = n",
          reason: "type mismatch in the composed proof",
          attempt_id: null,
          recorded_at: "2026-11-26T10:00:00",
        },
      ],
      counts: { lemmas: 2, tried_routes: 1 },
    };
    await route.fulfill({ response, json: graph });
  });
  await page.goto("/#session=fixture");
  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  const ledger = page.getByRole("region", { name: "Verified ledger" });
  await expect(ledger).toBeVisible();
  await expect(ledger.getByRole("heading", { name: "Checked lemmas · 2" })).toBeVisible();
  await expect(ledger.getByText("∀ n : Nat, n * 1 = n")).toBeVisible();
  await expect(ledger.getByText("Not used yet; later rounds can use it")).toBeVisible();
  await expect(ledger.getByRole("heading", { name: "Tried route · 1" })).toBeVisible();
  const tried = ledger.getByRole("button").filter({ hasText: "Lean rejected the closing proof" });
  await expect(ledger.getByText("Used by route Reduction 1")).toBeVisible();
  await expect(tried).toContainText("Stopped at");
  await expect(tried).toContainText("∀ n, n + 0 = n");
  await expect(tried).toContainText("type mismatch in the composed proof");
  // The route is on the graph, so its entry selects it.
  await tried.click();
  await expect(page.locator("#node-detail h2")).toHaveText("Reduction 1");
});

test("a claim without earlier rounds shows no ledger", async ({ page }) => {
  await page.goto("/#session=fixture");
  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  await expect(page.locator("#node-detail")).toBeVisible();
  await expect(page.getByRole("region", { name: "Verified ledger" })).toHaveCount(0);
});
