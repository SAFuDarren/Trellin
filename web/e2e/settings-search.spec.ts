import { expect, test, type Page } from "@playwright/test";
import { openMenu } from "./navigation";

async function openSettings(page: Page) {
  await page.goto("/#session=fixture");
  const menu = await openMenu(page.getByRole("button", { name: "Settings and help", exact: true }));
  await menu.getByRole("menuitem", { name: "Settings", exact: true }).click();
  await expect(page.locator("#section-title")).toHaveText("Settings");
}

test("grouped settings find real destinations and clear an empty search", async ({ page }) => {
  await openSettings(page);
  const side = page.getByRole("navigation", { name: "Settings", exact: true });
  await expect(side.locator(".nav-group > span")).toHaveText([
    "Personal",
    "Research and workflow",
    "Connections",
    "Updates and help",
  ]);
  await expect(side.getByRole("link", { name: "Automation", exact: true })).toHaveAttribute("href", "#progress");

  const search = page.getByRole("searchbox", { name: "Search settings" });
  await search.fill("unknownsetting");
  await expect(page.locator(".settings").getByRole("status")).toHaveText("No matching settings.");
  await search.press("Escape");
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("");

  await search.fill("Ｌｅａｎ");
  const results = page.getByRole("list", { name: "Settings search results" });
  const environment = results.getByRole("link", { name: "Environment Connections", exact: true });
  await expect(environment).toHaveAttribute("href", "#team/runtimes");
  await search.press("Tab");
  await expect(environment).toBeFocused();
  await environment.press("Enter");
  await expect(page).toHaveURL(/#team\/runtimes$/);
  await expect(page.getByRole("heading", { name: "Runtimes", exact: true })).toBeVisible();
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
  await search.fill("模型连接");
  const models = page.getByRole("list", { name: "设置搜索结果" }).getByRole("link", { name: /模型连接/ });
  await expect(models).toHaveAttribute("href", "#team/models");
  await models.click();
  await expect(page).toHaveURL(/#team\/models$/);
  await expect(page.getByRole("heading", { name: "模型连接", exact: true })).toBeVisible();
});
