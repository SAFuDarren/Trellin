import { expect, test, type Page } from "@playwright/test";
async function openSettings(page: Page) {
  await page.goto("/#session=fixture");
  // #293: the gear opens Settings in one click.
  await page.locator("nav.rail").getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page.locator("#section-title")).toHaveText("Settings");
}

test("Settings changes things in place and never sends the researcher to another space", async ({ page }) => {
  await openSettings(page);
  const side = page.getByRole("navigation", { name: "Settings", exact: true });
  await expect(side.locator(".nav-group > span")).toHaveText(["Personal", "About"]);
  const entries = side.getByRole("link");
  const count = await entries.count();
  expect(count).toBeGreaterThan(3);
  const settings = page.locator("nav.rail").getByRole("link", { name: "Settings", exact: true });
  for (let index = 0; index < count; index++) {
    const entry = entries.nth(index);
    await expect(entry).toHaveAttribute("href", /^#settings\//);
    await entry.click();
    await expect(page).toHaveURL(/#settings\/[a-z-]+$/);
    await expect(settings).toHaveAttribute("aria-current", "page");
  }
  // Nothing in the page body links out either: model connections live in Team, rules in Automation.
  await expect(page.locator(".settings a[href]:not([href^='#settings'])")).toHaveCount(0);
});

test("settings search matches full-width text and clears an empty search", async ({ page }) => {
  await openSettings(page);
  const search = page.getByRole("searchbox", { name: "Search settings" });
  await search.fill("unknownsetting");
  await expect(page.locator(".settings").getByRole("status")).toHaveText("No matching settings.");
  await search.press("Escape");
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("");

  await search.fill("ＬＡＹＯＵＴ");
  const results = page.getByRole("list", { name: "Settings search results" });
  const layout = results.getByRole("link", { name: "Workbench layout Personal", exact: true });
  await expect(layout).toHaveAttribute("href", "#settings/layout");
  await search.press("Tab");
  await expect(layout).toBeFocused();
  await layout.press("Enter");
  await expect(page).toHaveURL(/#settings\/layout$/);
  await expect(page.getByRole("heading", { name: "Workbench layout", exact: true })).toBeFocused();
});

test("search focuses existing update and shortcut controls", async ({ page }) => {
  await openSettings(page);
  const search = page.getByRole("searchbox", { name: "Search settings" });
  const results = page.getByRole("list", { name: "Settings search results" });
  await search.fill("automatic update");
  await results.getByRole("link", { name: /Updates/ }).click();
  await expect(page).toHaveURL(/#settings\/updates$/);
  const updates = page.getByRole("region", { name: "Updates", exact: true });
  await expect(updates.getByRole("heading", { name: "Updates", exact: true })).toBeFocused();
  await expect(updates.getByRole("checkbox", { name: "Automatically check for updates" })).not.toBeChecked();
  await expect(updates.getByRole("button", { name: "Check for updates" })).toBeVisible();

  await search.fill("keyboard shortcuts");
  await results.getByRole("link", { name: /Keyboard shortcuts/ }).click();
  await expect(page).toHaveURL(/#settings\/shortcuts$/);
  await page.getByRole("button", { name: "View shortcuts", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeHidden();
});

test("settings search follows the selected interface language at compact width", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await openSettings(page);
  await page.getByRole("searchbox", { name: "Search settings" }).fill("简体中文");
  await page
    .getByRole("list", { name: "Settings search results" })
    .getByRole("link", { name: /Language/ })
    .click();
  await expect(page.getByRole("heading", { name: "Language", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "简体中文", exact: true }).click();
  const search = page.getByRole("searchbox", { name: "搜索设置" });
  await search.fill("布局");
  const layout = page.getByRole("list", { name: "设置搜索结果" }).getByRole("link", { name: /工作台布局/ });
  await expect(layout).toHaveAttribute("href", "#settings/layout");
  await layout.click();
  await expect(page.getByRole("heading", { name: "工作台布局", exact: true })).toBeFocused();
});
