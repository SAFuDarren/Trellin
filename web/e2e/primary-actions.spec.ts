import { expect, test } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

const PRIMARY = "button.button.primary:visible";

test("the header, inspector and current dialog each have one filled main action", async ({ page }) => {
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
  const header = page.locator(".project-toolbar-actions");
  const add = header.getByRole("button", { name: "Add theorem", exact: true });
  await expect(header.locator(PRIMARY)).toHaveCount(1);
  await expect(header.locator(PRIMARY)).toHaveAccessibleName("Add theorem");
  for (const colorScheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme });
    await expect(add).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  }
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
  const theorem = page.getByRole("dialog", { name: "Add theorem", exact: true });
  await expect(theorem.locator(PRIMARY)).toHaveCount(1);
  await expect(theorem.locator(PRIMARY)).toHaveAttribute("type", "submit");
  await page.keyboard.press("Escape");
  await expect(theorem).toBeHidden();

  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  const inspector = page.locator("#review");
  await expect(inspector.locator(PRIMARY)).toHaveCount(1);
  await expect(inspector.locator(PRIMARY)).toHaveAccessibleName("Proof task");
  await inspector.locator(PRIMARY).click();
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

test("reviewing a public excerpt transfers the intake dialog's one primary action", async ({ page }) => {
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
  await page.route("**/api/project-intake/public-preview", (route) =>
    route.fulfill({
      json: {
        token: "fixture-preview",
        provider: "openai",
        model: "fixture",
        outgoing: { domain: "lean-core", objective: "Fixture objective", notes: "Fixture notes" },
        limits: { max_calls: 1, timeout_seconds: 30, max_output_tokens: 400, output_token_limit_enforced: true },
      },
    }),
  );
  await page.goto("/#session=fixture");
  await page.getByRole("button", { name: "Research actions", exact: true }).click();
  await page.getByRole("menuitem", { name: "Organize with AI", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Organize with AI", exact: true });
  await dialog.getByRole("button", { name: "Model", exact: true }).click();
  await dialog.getByRole("option", { name: /Fixture remote/ }).click();
  await dialog.getByLabel("Research notes or Lean text").fill("Fixture notes");
  await expect(dialog.locator(PRIMARY)).toHaveCount(1);
  await expect(dialog.locator(PRIMARY)).toHaveAccessibleName("Analyze selected notes");
  await dialog.locator(PRIMARY).click();
  await expect(dialog.getByRole("heading", { name: "Review the public material to share" })).toBeVisible();
  await expect(dialog.locator(PRIMARY)).toHaveCount(1);
  await expect(dialog.locator(PRIMARY)).toHaveAccessibleName("Send this public excerpt");
});
