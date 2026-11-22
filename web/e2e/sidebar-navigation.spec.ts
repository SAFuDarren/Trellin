import { expect, test } from "@playwright/test";
import { controlledMenu, openDestination, openMenu, railSpace } from "./navigation";
import { mapClaim, openProof } from "./project-map";

test("keyboard shortcuts open from the canvas or Help and return focus", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  const card = mapClaim(page, "自然数加零的对称性");
  await card.focus();
  await page.keyboard.press("?");
  const sheet = page.getByRole("dialog", { name: "Keyboard shortcuts", exact: true });
  await expect(sheet).toBeVisible();
  const close = sheet.getByRole("button", { name: "Close keyboard shortcuts", exact: true });
  await expect(close).toBeFocused();
  const mac = await page.evaluate(() => navigator.platform.startsWith("Mac"));
  await expect(sheet.getByRole("region", { name: "Navigation", exact: true })).toContainText(mac ? "⌘K" : "Ctrl+K");
  await expect(sheet.getByRole("region", { name: "Navigation", exact: true })).toContainText(
    mac ? "⇧F10" : "Shift+F10",
  );
  await expect(sheet.getByRole("region", { name: "Canvas", exact: true })).toContainText("Cancel card move");
  await page.keyboard.press("Tab");
  await expect(sheet.getByRole("region", { name: "Shortcut list", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(card).toBeFocused();

  await openProof(page);
  const proof = page.locator(".main-proof-graph");
  const proofReturn = page.locator(".proof-return");
  await expect(proofReturn).toBeFocused();
  await page.keyboard.press("?");
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(proof).toBeVisible();
  await expect(proofReturn).toBeFocused();

  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Command palette", exact: true });
  const search = palette.getByRole("combobox");
  await search.press("?");
  await expect(search).toHaveValue("?");
  await expect(sheet).toBeHidden();
  await search.press("Escape");

  await page.goto("/#library");
  const librarySearch = page.getByRole("searchbox", { name: /^Search by name or statement/ });
  await librarySearch.press("?");
  await expect(librarySearch).toHaveValue("?");
  await expect(sheet).toBeHidden();

  await page.goto("/#help");
  const launcher = page.getByRole("button", { name: "Keyboard shortcuts", exact: true });
  await launcher.press("Enter");
  await expect(sheet).toBeVisible();
  await close.press("Enter");
  await expect(sheet).toBeHidden();
  await expect(launcher).toBeFocused();
});

test("the icon rail keeps names and badges and shows labels by pointer and keyboard", async ({ page }) => {
  await page.goto("/#session=fixture");
  const rail = page.locator("nav.rail");
  const home = rail.getByRole("link", { name: "Home", exact: true });
  const automation = rail.getByRole("link", { name: "Automation", exact: true });
  const team = rail.getByRole("link", { name: /^Team/ });
  const hint = page.getByRole("tooltip");

  await expect(home).toHaveAttribute("aria-current", "page");
  await expect(home).toHaveAttribute("href", "#workbench");
  // #289: five spaces, as in Multica. Pages live in each space's panel, not in the rail.
  await expect(rail.locator(".rail-group .rail-link")).toHaveCount(5);
  for (const space of ["Home", "Work", "Chats", "Team", "Automation"])
    await expect(railSpace(page, space)).toHaveCount(1);
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
  const utilities = rail.getByRole("button", { name: "Settings and help", exact: true });
  await expect(utilities).toBeInViewport();
  const utilityMenu = await openMenu(utilities);
  await expect(utilityMenu.getByRole("menuitem", { name: "Help", exact: true })).toBeInViewport({ ratio: 1 });
  await expect(utilityMenu.getByRole("menuitem", { name: "Settings", exact: true })).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Escape");
  await expect(utilities).toBeFocused();
  await expect(page.locator(".section-nav")).toHaveCount(0);
  await page.getByRole("button", { name: "Find claim", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Search claims on map" })).toBeVisible();
  await openDestination(page, "Library");
  const navigation = page.locator(".section-nav");
  const needsRecheck = navigation.getByRole("link", { name: /^Needs recheck/ });
  await needsRecheck.click();
  await expect(needsRecheck).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".section-main")).toBeInViewport();
});

test("proof activity opens with the keyboard and dark navigation uses Navy selection", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/#session=fixture");
  await openProof(page);

  const proofActivity = page.locator("#review summary").filter({ hasText: /^Proof activity$/ });
  await proofActivity.focus();
  await proofActivity.press("Enter");
  await expect(proofActivity).toHaveCSS("outline-style", "solid");
  await expect(proofActivity).toHaveCSS("outline-width", "2px");
  await expect(page.locator(".inspector-activity")).toHaveAttribute("open", "");

  await openDestination(page, "Claims");
  const work = railSpace(page, "Work");
  const claims = page.locator("nav.section-nav").getByRole("link", { name: "Claims", exact: true });
  const selectedTint = await page.evaluate(() => {
    const probe = document.createElement("span");
    probe.hidden = true;
    probe.style.backgroundColor = "var(--state-selected)";
    document.body.append(probe);
    const color = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return color;
  });
  for (const selected of [work, claims]) {
    await expect(selected).toHaveAttribute("aria-current", "page");
    await expect(selected).toHaveCSS("background-color", selectedTint);
    await expect(selected).toHaveCSS("box-shadow", "none");
  }
});

test("Work → Review and Home restore the selected claim and proof node", async ({ page, request }) => {
  await request.post("/__reset", { data: {} });
  await page.setViewportSize({ width: 860, height: 720 });
  await page.goto("/#session=fixture");

  const home = railSpace(page, "Home");
  await expect(page.getByRole("button", { name: "Switch view" })).toHaveCount(0);
  await openProof(page, "单射映射的复合");
  const proof = page.locator(".main-proof-graph");
  await expect(proof).toBeVisible();
  const chosen = page.getByRole("button", { name: /^Proof obligation 2\b/ });
  await chosen.click();
  await expect(chosen).toHaveAttribute("aria-pressed", "true");

  await openDestination(page, "Review");
  await expect(page).toHaveURL(/#review$/);
  await expect(home).not.toHaveAttribute("aria-current", "page");
  await expect(railSpace(page, "Work")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#section-title")).toHaveText("Work");
  await expect(page.locator("#node-detail")).toHaveCount(0);
  await home.click();
  await expect(page).toHaveURL(/#workbench$/);
  await expect(proof).toBeVisible();
  await expect(chosen).toHaveAttribute("aria-pressed", "true");
  await expect(home).toHaveAttribute("aria-current", "page");
  await page.locator(".proof-return").click();
  await expect(mapClaim(page, "单射映射的复合")).toHaveAttribute("aria-pressed", "true");
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
  const settings = menu.getByRole("menuitem", { name: "Settings", exact: true });
  await expect(settings).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#settings$/);
  await page.getByRole("button", { name: "简体中文", exact: true }).click();

  const chineseUtilities = rail.getByRole("button", { name: "设置与帮助", exact: true });
  menu = await openMenu(chineseUtilities);
  await expect(menu.getByRole("menuitem", { name: "设置", exact: true })).toHaveAttribute("aria-current", "page");
  const help = menu.getByRole("menuitem", { name: "帮助", exact: true });
  await expect(help).toBeInViewport({ ratio: 1 });
  await help.click();
  await expect(page).toHaveURL(/#help$/);
  await expect(page.locator("#section-title")).toHaveText("帮助");
  menu = await openMenu(chineseUtilities);
  await expect(menu.getByRole("menuitem", { name: "帮助", exact: true })).toHaveAttribute("aria-current", "page");
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(chineseUtilities).toBeFocused();

  await page.goto("/#review/77777777-7777-4777-8777-777777777777");
  await page.reload();
  await expect(page.locator("#section-title")).toHaveText("工作");
  await expect(page.locator(".page-head h2")).toHaveText("reverseZeroAdd");
  const review = page.locator("nav.section-nav").getByRole("link", { name: /^审阅/ });
  await expect(review).toHaveAttribute("aria-current", "page");
  await expect(review.getByText("2", { exact: true })).toBeVisible();

  await railSpace(page, "主页").click();
  await expect(page).toHaveURL(/#workbench$/);
  await expect(page.getByRole("application", { name: "项目图谱", exact: true })).toBeVisible();
});

for (const [colorScheme, language, menuLabel, title] of [
  ["light", "en", "Settings and help", "Settings"],
  ["dark", "zh-CN", "设置与帮助", "设置"],
] as const) {
  test(`the rail shows a Settings gear and keeps the utility menu reachable in ${colorScheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await page.addInitScript((locale) => localStorage.setItem("research-multiplier-language", locale), language);
    await page.goto("/#session=fixture");
    const utilities = page.locator("nav.rail").getByRole("button", { name: menuLabel, exact: true });
    await expect(utilities).toHaveAttribute("title", title);
    await expect(utilities).toHaveCSS("width", "36px");
    await expect(utilities).toHaveCSS("height", "36px");
    const path = utilities.locator("svg path");
    await expect(path).toHaveAttribute("d", /^M10 3h4.*ZM15 12a3/);
    await expect(utilities.locator("svg")).toHaveCSS("stroke-width", "1.6px");
    const menu = await openMenu(utilities);
    await expect(menu.getByRole("menuitem", { name: title, exact: true })).toBeVisible();
  });
}
