import { expect, test } from "@playwright/test";
import { clickInspectorAction, FIXTURE_CLAIM_TITLE, IMPORT_ENTRY, mapClaim, openAddEntry } from "./project-map";

const PRIMARY = ".button.primary:visible";

test("the inspector and current dialog each have one filled main action, and the map header none", async ({ page }) => {
  await page.route("**/api/proof-task?*", (route) => {
    const claim_id = new URL(route.request().url()).searchParams.get("claim");
    return route.fulfill({
      json: {
        claim_id,
        title: FIXTURE_CLAIM_TITLE,
        state: "ready",
        can_start: true,
        can_stop: false,
        blocker: null,
        local_model_allowed: false,
        scope: { token: "fixture-proof-scope", proposition: "∀ n : Nat, n + 0 = n", basis: [], omitted_basis: 0 },
        decisions: [],
        policy: null,
        receipt: null,
        adopted: false,
      },
    });
  });
  await page.goto("/#session=fixture");
  const header = page.locator("#workbench .page-head .page-actions");
  // #353: the map header's only action is the Add menu, an icon button like the map's other tools.
  const add = header.getByRole("button", { name: "Add to project", exact: true });
  await expect(header.locator(PRIMARY)).toHaveCount(0);
  await expect(add).toHaveText("");
  const toolbar = page.locator(".project-canvas-tools");
  await expect(toolbar.locator(PRIMARY)).toHaveCount(0);
  const find = toolbar.getByRole("button", { name: "Find claim", exact: true });
  const link = toolbar.getByRole("button", { name: "Link selected claim", exact: true });
  await expect(find).toHaveCSS(
    "border-top-color",
    await link.evaluate((button) => getComputedStyle(button).borderTopColor),
  );
  await expect(find).toHaveCSS("height", await link.evaluate((button) => getComputedStyle(button).height));

  await add.click();
  await page.getByRole("menuitem", { name: /^New theorem…/ }).click();
  const theorem = page.getByRole("dialog", { name: "New theorem", exact: true });
  await expect(theorem.locator(PRIMARY)).toHaveCount(1);
  await expect(theorem.locator(PRIMARY)).toHaveAttribute("type", "submit");
  await page.keyboard.press("Escape");
  await expect(theorem).toBeHidden();

  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  const inspector = page.locator("#review");
  await expect(inspector.locator(PRIMARY)).toHaveCount(1);
  await expect(inspector.locator(PRIMARY)).toHaveAccessibleName("Review");
  await clickInspectorAction(page, "Proof task");
  const proof = page.getByRole("dialog", { name: "Proof task", exact: true });
  await expect(proof.locator(PRIMARY)).toHaveCount(1);
  await expect(proof.locator(PRIMARY)).toHaveAccessibleName("Authorize and start");
  await expect(proof.locator(PRIMARY)).toBeDisabled();
});

test("the view icons show pressed state and only handle letter shortcuts in the map", async ({ page }) => {
  await page.goto("/#session=fixture");
  const toolbar = page.locator(".project-canvas-tools");
  const map = page.getByRole("application", { name: "Project map", exact: true });
  const details = toolbar.getByRole("button", { name: "Details", exact: true });
  const atlas = toolbar.getByRole("button", { name: "Global map", exact: true });
  await expect(details).toHaveAttribute("aria-keyshortcuts", "I");
  await expect(atlas).toHaveAttribute("aria-keyshortcuts", "M");

  await map.focus();
  await page.keyboard.press("i");
  await expect(details).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#review")).toBeVisible();
  await details.hover();
  await expect(toolbar.getByRole("tooltip", { name: "Details (I)", exact: true })).toBeVisible();
  await details.click();
  await expect(details).toHaveAttribute("aria-pressed", "false");

  await map.focus();
  await page.keyboard.press("m");
  await expect(atlas).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("img", { name: /^Recent claims in the workspace/ })).toBeVisible();

  await toolbar.getByRole("button", { name: "Find claim", exact: true }).click();
  const search = page.getByRole("combobox", { name: "Search claims on map" });
  await search.press("i");
  await search.press("m");
  await expect(search).toHaveValue("im");
  await expect(atlas).toHaveAttribute("aria-pressed", "true");
  await expect(details).toHaveAttribute("aria-pressed", "false");
  await search.press("Escape");

  await atlas.hover();
  await expect(toolbar.getByRole("tooltip", { name: "Global map (M)", exact: true })).toBeVisible();
  await atlas.press("m");
  await expect(atlas).toHaveAttribute("aria-pressed", "false");
});

test("allowing a cloud model for imports transfers the intake dialog's one primary action", async ({ page }) => {
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({
      json: {
        connections: [
          {
            id: "fixture-model",
            label: "Fixture remote",
            model: "fixture",
            provider: "openai",
            fingerprint: "x",
            status: "connected",
          },
        ],
      },
    }),
  );
  await page.route("**/api/project-intake/grants", (route) => route.fulfill({ json: { grants: [] } }));
  await page.goto("/#session=fixture");
  await openAddEntry(page, IMPORT_ENTRY);
  const dialog = page.getByRole("dialog", { name: "Import paper, notes or code", exact: true });
  await dialog.getByRole("button", { name: "Paste text instead" }).click();
  await dialog.getByLabel("Research notes or Lean text").fill("Fixture notes");
  await dialog.getByRole("button", { name: "Advanced options" }).click();
  await dialog.getByRole("button", { name: "Model", exact: true }).click();
  await dialog.getByRole("option", { name: /Fixture remote/ }).click();
  await expect(dialog.locator(PRIMARY)).toHaveCount(1);
  await expect(dialog.locator(PRIMARY)).toHaveAccessibleName("Organize");
  await dialog.locator(PRIMARY).click();
  // #355: a cloud model asks once per project; the ask carries the one primary action.
  await expect(dialog.getByRole("group", { name: "Allow this model for imports" })).toBeVisible();
  await expect(dialog.locator(PRIMARY)).toHaveCount(1);
  await expect(dialog.locator(PRIMARY)).toHaveAccessibleName("Allow and organize");
});
