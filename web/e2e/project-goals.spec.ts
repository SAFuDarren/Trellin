import { expect, test } from "@playwright/test";
import { mapClaim, openProof } from "./project-map";

const ORIGINAL_CLAIM = "11111111-1111-4111-8111-111111111111";
const NEW_GOAL = {
  id: "bbbbbbbb-0000-4000-8000-000000000001",
  title: "Natural number reflexivity",
  status: "open",
  domain: "lean-core",
};

test("adding a project goal keeps the current proof open and draws no dependency to the new goal", async ({ page }) => {
  let recorded = false;
  const requestedGraphs: Array<string | null> = [];

  await page.route(/\/api\/(?:project-canvas\/)?claims$/, async (route) => {
    const response = await route.fetch();
    const original = await response.json();
    await route.fulfill({
      response,
      json: { ...original, claims: recorded ? [NEW_GOAL, ...original.claims] : original.claims },
    });
  });
  await page.route("**/api/theorems", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { drafts: [] } });
      return;
    }
    expect(route.request().postDataJSON()).toMatchObject({
      kind: "goal",
      title: NEW_GOAL.title,
      lean: "∀ n : Nat, n = n",
      confirmed: true,
    });
    recorded = true;
    await route.fulfill({ json: { kind: "goal", status: "open", claim_id: NEW_GOAL.id } });
  });
  await page.route("**/api/graph?*", async (route) => {
    const id = new URL(route.request().url()).searchParams.get("claim");
    requestedGraphs.push(id);
    if (id === NEW_GOAL.id) {
      const goalId = "goal:" + NEW_GOAL.id;
      await route.fulfill({
        json: {
          claim: NEW_GOAL,
          focus_id: goalId,
          nodes: [{ id: goalId, kind: "goal", title: NEW_GOAL.title, status: "open" }],
          edges: [],
        },
      });
      return;
    }
    await route.continue();
  });
  await page.route(/\/api\/conversations(?:\?|$)/, (route) => route.fulfill({ json: { threads: [] } }));

  await page.goto("/#session=fixture");
  await openProof(page);
  const heading = page.locator("#node-detail").getByRole("heading", { level: 2 });
  await expect(heading).toHaveText("自然数加零的对称性");
  const originalProof = page.locator(".graph-nodes");
  const originalGoal = originalProof.locator('[data-node="goal:' + ORIGINAL_CLAIM + '"]');
  const originalNode = originalProof.getByRole("button", { name: /Proof obligation 2/ }).first();
  const edges = page.locator(".graph-edges .graph-edge");
  await expect(edges).toHaveCount(7);
  const oldPaths = await edges.evaluateAll((paths) => paths.map((path) => path.getAttribute("d")));
  await originalNode.click();
  await page.locator("#review").getByRole("button", { name: "Zoom in", exact: true }).click();

  await page.getByRole("button", { name: "Add theorem" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Destination · Current project graph")).toBeVisible();
  await dialog.getByLabel("Title", { exact: true }).fill(NEW_GOAL.title);
  await dialog.getByLabel("Mathematical statement in your own words").fill("Every natural number equals itself.");
  await dialog.getByLabel("Lean proposition", { exact: true }).fill("∀ n : Nat, n = n");
  await dialog.getByLabel("Your name").fill("Fixture reviewer");
  await dialog.getByLabel("Reason").fill("Checked the proposition");
  await dialog.getByLabel("The Lean proposition expresses my intended theorem, including its premises.").check();
  await dialog.getByRole("button", { name: "Confirm statement and add goal" }).click();

  const newClaim = mapClaim(page, NEW_GOAL.title);
  await expect(dialog).toBeHidden();
  await expect(heading).toHaveText("Proof obligation 2");
  await expect(newClaim).toBeVisible();
  await expect(originalNode).toHaveAttribute("aria-pressed", "true");
  await expect(originalGoal).toBeVisible();
  await expect(edges).toHaveCount(7);
  expect(await edges.evaluateAll((paths) => paths.map((path) => path.getAttribute("d")))).toEqual(oldPaths);
  expect(requestedGraphs).not.toContain(NEW_GOAL.id);

  await page.getByRole("button", { name: "View on project map", exact: true }).click();
  await expect(newClaim).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#node-detail").getByRole("heading", { name: NEW_GOAL.title })).toBeVisible();
  await expect(originalProof).toHaveCount(0);

  requestedGraphs.length = 0;
  await page.reload();
  await expect(newClaim).toBeVisible();
  await openProof(page);
  await expect(heading).toHaveText("自然数加零的对称性");
  await expect(edges).toHaveCount(7);
  expect(await edges.evaluateAll((paths) => paths.map((path) => path.getAttribute("d")))).toEqual(oldPaths);
  expect(requestedGraphs).not.toContain(NEW_GOAL.id);

  await openProof(page, NEW_GOAL.title);
  await expect(heading).toHaveText(NEW_GOAL.title);
  await expect(originalProof.locator('[data-node="goal:' + NEW_GOAL.id + '"]')).toBeVisible();
  await expect(edges).toHaveCount(0);
  await expect(page.getByText("No supporting proof nodes are recorded yet.")).toBeVisible();
  await expect(page.locator(".project-canvas-node")).toHaveCount(6);
  expect(requestedGraphs).toContain(NEW_GOAL.id);
});
