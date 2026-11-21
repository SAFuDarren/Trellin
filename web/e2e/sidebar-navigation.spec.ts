import { expect, test } from "@playwright/test";
import { controlledMenu, openMenu } from "./navigation";
import { openProof } from "./project-map";

test("the icon rail keeps names and badges and shows labels by pointer and keyboard", async ({ page }) => {
  await page.goto("/#session=fixture");
  const rail = page.locator("nav.rail");
  const home = rail.getByRole("link", { name: "Home", exact: true });
  const automation = rail.getByRole("link", { name: "Automation", exact: true });
  const team = rail.getByRole("link", { name: /^Team/ });
  const hint = page.getByRole("tooltip");

  await expect(home).toHaveAttribute("aria-current", "page");
  await expect(home).toHaveAttribute("href", "#workbench");
  await expect(automation).toHaveText("");
  await expect(team.locator(".rail-badge")).toHaveText("2");
  await expect(team).toHaveAccessibleName(/2 running/);
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
  const utilities = rail.getByRole("button", { name: "Settings and help", exact: true });
  await expect(utilities).toBeInViewport();
  const utilityMenu = await openMenu(utilities);
  await expect(utilityMenu.getByRole("link", { name: "Help", exact: true })).toBeInViewport({ ratio: 1 });
  await expect(utilityMenu.getByRole("link", { name: "Settings", exact: true })).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Escape");
  await expect(utilities).toBeFocused();
  await expect(workbenchSidebar.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await page.getByRole("button", { name: "Find claim", exact: true }).click();
  await expect(page.getByRole("searchbox", { name: "Search claims on map" })).toBeVisible();
  await expect(workbenchSidebar.getByRole("searchbox")).toHaveCount(0);
  await rail.getByRole("link", { name: "Library", exact: true }).click();
  const navigation = page.locator(".section-nav");
  const needsRecheck = navigation.getByRole("link", { name: /^Needs recheck/ });
  await needsRecheck.click();
  await expect(needsRecheck).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".section-main")).toBeInViewport();
});

test("dark navigation and conversation tabs use flat gray selection", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/#session=fixture");
  await openProof(page);

  const proofActivity = page.getByRole("button", { name: "Proof activity" });
  await proofActivity.focus();
  await proofActivity.press("Enter");
  await expect(proofActivity).toHaveCSS("outline-style", "solid");
  await expect(proofActivity).toHaveCSS("outline-width", "2px");
  await expect(proofActivity).toHaveAttribute("aria-pressed", "true");
  await expect(proofActivity).toHaveCSS("background-color", "rgb(52, 52, 52)");
  await expect(proofActivity).toHaveCSS("color", "rgb(241, 241, 241)");

  const rail = page.locator("nav.rail");
  const claims = rail.getByRole("link", { name: "Claims", exact: true });
  await claims.click();
  const allClaims = page.locator(".section-nav").getByRole("link", { name: /^All claims/ });
  for (const selected of [claims, allClaims]) {
    await expect(selected).toHaveAttribute("aria-current", "page");
    await expect(selected).toHaveCSS("background-color", "rgb(52, 52, 52)");
    await expect(selected).toHaveCSS("box-shadow", "none");
  }
});

test("the view switcher and Home restore the selected claim and proof node", async ({ page, request }) => {
  await request.post("/__reset", { data: {} });
  await page.setViewportSize({ width: 860, height: 720 });
  await page.goto("/#session=fixture");

  const home = page.locator("nav.rail").getByRole("link", { name: "Home", exact: true });
  const switchView = page.getByRole("button", { name: "Switch view", exact: true });
  const title = page.getByRole("heading", { level: 1 });
  await openProof(page, "单射映射的复合");
  await expect(title).toHaveText("Other claim");
  const chosen = page.getByRole("button", { name: /^Proof obligation 2\b/ });
  await chosen.click();
  await expect(chosen).toHaveAttribute("aria-pressed", "true");

  await switchView.focus();
  await page.keyboard.press("Space");
  await expect(switchView).toHaveAttribute("aria-expanded", "true");
  let menu = await controlledMenu(switchView);
  await expect(menu).toBeInViewport({ ratio: 1 });
  const workbench = menu.getByRole("link", { name: "Workbench", exact: true });
  await expect(workbench).toHaveAttribute("aria-current", "page");
  await page.keyboard.press("Tab");
  await expect(workbench).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(switchView).toBeFocused();

  menu = await openMenu(switchView);
  await title.click();
  await expect(menu).toBeHidden();
  await expect(switchView).toHaveAttribute("aria-expanded", "false");
  await expect(switchView).toBeFocused();

  for (const returnVia of ["switcher", "home"]) {
    menu = await openMenu(switchView);
    await menu.getByRole("link", { name: /^Review/ }).click();
    await expect(page).toHaveURL(/#review$/);
    await expect(home).not.toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Review");
    await expect(page.locator("#node-detail")).toHaveCount(0);
    if (returnVia === "switcher") {
      const reviewMenu = await openMenu(switchView);
      await expect(reviewMenu.getByRole("link", { name: /^Review/ })).toHaveAttribute("aria-current", "page");
      await reviewMenu.getByRole("link", { name: "Workbench", exact: true }).click();
    } else {
      await home.click();
    }
    await expect(page).toHaveURL(/#workbench$/);
    await expect(title).toHaveText("Other claim");
    await expect(chosen).toHaveAttribute("aria-pressed", "true");
    await expect(home).toHaveAttribute("aria-current", "page");
  }
});

test("utilities and direct review links work in a narrow Chinese window", async ({ page, request }) => {
  await request.post("/__reset", { data: {} });
  await page.setViewportSize({ width: 860, height: 720 });
  await page.goto("/#session=fixture");
  const rail = page.locator("nav.rail");
  const utilities = rail.getByRole("button", { name: "Settings and help", exact: true });

  await utilities.focus();
  await page.keyboard.press("Enter");
  await expect(utilities).toHaveAttribute("aria-expanded", "true");
  let menu = await controlledMenu(utilities);
  const settings = menu.getByRole("link", { name: "Settings", exact: true });
  await page.keyboard.press("Tab");
  await expect(settings).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#settings$/);
  await page.getByRole("button", { name: "简体中文", exact: true }).click();

  const chineseUtilities = rail.getByRole("button", { name: "设置与帮助", exact: true });
  menu = await openMenu(chineseUtilities);
  await expect(menu.getByRole("link", { name: "设置", exact: true })).toHaveAttribute("aria-current", "page");
  const help = menu.getByRole("link", { name: "帮助", exact: true });
  await expect(help).toBeInViewport({ ratio: 1 });
  await help.click();
  await expect(page).toHaveURL(/#help$/);
  await expect(page.locator("#section-title")).toHaveText("帮助");
  menu = await openMenu(chineseUtilities);
  await expect(menu.getByRole("link", { name: "帮助", exact: true })).toHaveAttribute("aria-current", "page");
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(chineseUtilities).toBeFocused();

  await page.goto("/#review/77777777-7777-4777-8777-777777777777");
  await page.reload();
  await expect(page.locator("#section-title")).toHaveText("审阅");
  await expect(page.locator(".page-head h2")).toHaveText("reverseZeroAdd");
  const chineseSwitcher = page.getByRole("button", { name: "切换视图", exact: true });
  menu = await openMenu(chineseSwitcher);
  const review = menu.getByRole("link", { name: /^审阅/ });
  await expect(review).toHaveAttribute("aria-current", "page");
  await expect(review.getByText("2", { exact: true })).toBeVisible();

  await rail.getByRole("link", { name: "主页", exact: true }).click();
  await expect(page).toHaveURL(/#workbench$/);
  await expect(page.getByRole("application", { name: "项目图谱", exact: true })).toBeVisible();
});
