import { expect, test, type Page } from "@playwright/test";
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

test("menus and dialogs enter with motion, and reduced motion removes it", async ({ page }) => {
  await page.goto("/#session=fixture");
  await page.getByRole("button", { name: "Add theorem" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const enter = await dialog.evaluate((element) => {
    const style = getComputedStyle(element);
    return { name: style.animationName, duration: style.animationDuration, shadow: style.boxShadow };
  });
  expect(enter.name).toBe("rm-dialog-in");
  expect(enter.duration).toBe("0.18s");
  expect(enter.shadow).not.toBe("none");
  await page.keyboard.press("Escape");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Add theorem" }).click();
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate((element) => getComputedStyle(element).animationDuration)).toBe("0s");
  expect((await transitionOf(page, ".button")).duration).toContain("0s");
});
