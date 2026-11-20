import { expect, test } from "@playwright/test";

test("the icon rail keeps names and badges and shows labels by pointer and keyboard", async ({ page }) => {
  await page.goto("/#session=fixture");
  const rail = page.locator("nav.rail");
  const workbench = rail.getByRole("link", { name: "Workbench", exact: true });
  const automation = rail.getByRole("link", { name: "Automation", exact: true });
  const team = rail.getByRole("link", { name: /^Team/ });
  const review = rail.getByRole("link", { name: /^Review/ });
  const hint = page.getByRole("tooltip");

  await expect(workbench).toHaveAttribute("aria-current", "page");
  await expect(automation).toHaveText("");
  await expect(review.locator(".rail-badge")).toBeVisible();
  const count = await review.locator(".rail-badge").textContent();
  await expect(review).toHaveAccessibleName(new RegExp(count!));
  expect((await rail.boundingBox())!.width).toBeLessThan(64);
  await expect(hint).toHaveCount(0);
  await automation.hover();
  await expect(hint).toHaveCount(0);
  await expect(hint).toHaveText("Automation");
  expect((await hint.boundingBox())!.x).toBeGreaterThan((await rail.boundingBox())!.width);
  await team.hover();
  await expect(hint).toHaveText("Team", { timeout: 250 });
  await page.keyboard.press("Escape");
  await expect(hint).toHaveCount(0);

  await page.mouse.move(200, 0);
  await automation.focus();
  await expect(hint).toHaveText("Automation", { timeout: 250 });
  await page.keyboard.press("Escape");
  await expect(hint).toHaveCount(0);
  await expect(automation).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(automation).toHaveAttribute("aria-current", "page");
});

test("compact navigation keeps utilities, claim search, and Library filters reachable", async ({ page }) => {
  await page.setViewportSize({ width: 540, height: 460 });
  await page.goto("/#session=fixture");
  const rail = page.locator("nav.rail");
  const workbenchSidebar = page.locator("#sidebar");
  const settings = rail.getByRole("link", { name: "Settings", exact: true });
  await expect(settings).toBeInViewport();
  await expect(workbenchSidebar.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await workbenchSidebar.getByRole("button", { name: "Search claims" }).click();
  await expect(workbenchSidebar.getByRole("searchbox", { name: "Search claims" })).toBeFocused();
  await expect(workbenchSidebar.getByRole("button", { name: "Collapse sidebar" })).toBeVisible();
  await rail.getByRole("link", { name: "Library", exact: true }).click();
  const navigation = page.locator(".section-nav");
  const needsRecheck = navigation.getByRole("link", { name: /^Needs recheck/ });
  await needsRecheck.click();
  await expect(needsRecheck).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".section-main")).toBeInViewport();
});
