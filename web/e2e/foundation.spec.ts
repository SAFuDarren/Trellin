import { expect, test, type Locator, type Page } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

// #163: interactive elements share one transition, radii come from one scale,
// floating layers enter with motion, and reduced motion removes it.

// Token values plus glyph shapes (status squares, legend keys, round dots).
const ALLOWED_RADII = new Set(["0px", "1.5px", "2px", "4px", "6px", "8px", "12px", "999px", "50%"]);

async function radiiOutsideScale(page: Page) {
  return page.evaluate(
    (allowed) => {
      const found: string[] = [];
      for (const element of document.querySelectorAll<HTMLElement>("body *")) {
        if (!element.getClientRects().length) continue;
        const style = getComputedStyle(element);
        for (const corner of [style.borderTopLeftRadius, style.borderBottomRightRadius]) {
          const value = corner.split(" ")[0] ?? corner;
          if (!allowed.includes(value)) found.push(`${element.tagName.toLowerCase()}.${element.className} ${value}`);
        }
      }
      return [...new Set(found)];
    },
    [...ALLOWED_RADII],
  );
}

async function transitionOf(page: Page, selector: string) {
  return page
    .locator(selector)
    .first()
    .evaluate((element) => {
      const style = getComputedStyle(element);
      return { property: style.transitionProperty, duration: style.transitionDuration };
    });
}

async function expectToken(locator: Locator, property: "background-color" | "box-shadow", token: string) {
  const expected = await locator.evaluate(
    (element, { property, token }) => {
      // Resolve the token with the browser's CSS serialization, including color-mix().
      const probe = document.createElement("div");
      probe.hidden = true;
      probe.style.setProperty(property, `var(${token})`);
      element.ownerDocument.body.append(probe);
      const style = getComputedStyle(probe);
      const value = style.getPropertyValue(property);
      const defined = style.getPropertyValue(token);
      probe.remove();
      return { value, defined };
    },
    { property, token },
  );
  expect(expected.defined, token).not.toBe("");
  await expect(locator).toHaveCSS(property, expected.value);
}

async function expectEnter(locator: Locator, animation: string, duration: string) {
  await expect(locator).toBeVisible();
  await expect(locator).toHaveCSS("animation-name", animation);
  await expect(locator).toHaveCSS("animation-duration", duration);
}

test("workbench surfaces use the radius scale and shared state transition", async ({ page }) => {
  await page.goto("/#session=fixture");
  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  await expect(page.locator("#review")).toBeVisible();
  expect(await radiiOutsideScale(page)).toEqual([]);

  for (const selector of [".project-canvas-node", ".button", ".rail-link"]) {
    const transition = await transitionOf(page, selector);
    expect(transition.property, selector).toContain("background-color");
    expect(transition.property, selector).not.toContain("transform");
    expect(transition.duration, selector).toContain("0.15s");
  }

  await page.locator("nav.rail").getByRole("link", { name: /^Team/ }).click();
  await expect(page.locator(".section-main")).toBeVisible();
  expect(await radiiOutsideScale(page)).toEqual([]);
});

test("map cards distinguish hover, press and selection", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/#session=fixture");
  const card = mapClaim(page, "单射映射的复合");
  await expect(card).toHaveAttribute("aria-pressed", "false");
  await card.hover();
  await expectToken(card, "background-color", "--state-hover");
  await page.mouse.down();
  try {
    await expectToken(card, "background-color", "--state-pressed");
  } finally {
    await page.mouse.up();
  }

  await expect(card).toHaveAttribute("aria-pressed", "true");
  await card.hover();
  await expectToken(card, "background-color", "--state-selected");

  const home = page.locator("nav.rail").getByRole("link", { name: "Home", exact: true });
  await expect(home).toHaveAttribute("aria-current", "page");
  await home.hover();
  await expectToken(home, "background-color", "--state-selected");
});

for (const reducedMotion of ["no-preference", "reduce"] as const) {
  test(`workbench overlays use the motion and elevation tokens (${reducedMotion})`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion });
    await page.goto("/#session=fixture");
    const panelDuration = reducedMotion === "reduce" ? "0s" : "0.18s";
    const floatDuration = reducedMotion === "reduce" ? "0s" : "0.12s";
    const card = mapClaim(page, FIXTURE_CLAIM_TITLE);

    await card.click();
    await expectEnter(page.locator("#review"), "rm-panel-in", panelDuration);

    const globalMap = page.getByRole("button", { name: "Global map", exact: true });
    await globalMap.click();
    const overview = page.locator(".project-canvas-stage .atlas");
    await expectEnter(overview, "rm-fade-in", floatDuration);
    await expectToken(overview, "box-shadow", "--elevation-float");
    await globalMap.click();

    const search = page.getByRole("button", { name: "Find claim", exact: true });
    await search.click();
    await expectToken(search, "background-color", "--state-selected");
    const searchResults = page.locator(".project-canvas-popover").filter({ has: page.getByRole("combobox") });
    await expectEnter(searchResults, "rm-float-in", floatDuration);
    await expectToken(searchResults, "box-shadow", "--elevation-float");
    await expect(searchResults.getByRole("combobox")).toHaveCSS("border-radius", "6px");
    await expect(searchResults.getByRole("option").first()).toHaveCSS("border-radius", "6px");
    await page.keyboard.press("Escape");

    const researchActions = page.getByRole("button", { name: "Research actions", exact: true });
    await researchActions.click();
    const actions = page.locator(".workbench-actions-items");
    await expectEnter(actions, "rm-float-in", floatDuration);
    await expectToken(actions, "box-shadow", "--elevation-float");
    await page.keyboard.press("Escape");
    await expect(researchActions).toBeFocused();

    await page.getByRole("button", { name: "Add theorem", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expectEnter(dialog, "rm-dialog-in", panelDuration);
    await expectToken(dialog, "box-shadow", "--elevation-dialog");
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "Agent chat", exact: true }).click();
    const dock = page.getByRole("region", { name: "Agent chat", exact: true });
    await expectEnter(dock, "rm-panel-in", panelDuration);
    await expectToken(dock, "box-shadow", "--elevation-dialog");
    if (reducedMotion === "reduce") {
      await expect(card).toHaveCSS("transition-duration", "0s, 0s, 0s, 0s, 0s");
    }
    await page.locator("nav.rail").getByRole("link", { name: "Claims", exact: true }).click();
    await expectToken(dock, "box-shadow", "--elevation-dialog");
  });
}
