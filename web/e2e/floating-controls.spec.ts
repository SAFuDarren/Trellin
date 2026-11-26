import { expect, test } from "@playwright/test";

test("the shared menu supports keyboard navigation, typeahead and focus return", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  // #293 removed the Settings and help menu; #353's Add menu uses the same shared menu.
  const trigger = page.getByRole("button", { name: "Add to project", exact: true });
  const menu = page.getByRole("menu", { name: "Add to project" });
  const items = menu.getByRole("menuitem");

  await trigger.press("ArrowDown");
  await expect(menu).toBeInViewport({ ratio: 1 });
  await expect(items.first()).toBeFocused();
  await page.keyboard.press("End");
  await expect(items.last()).toBeFocused();
  const firstName = (await items.first().innerText()).trim();
  await page.keyboard.press(firstName[0]!.toLowerCase());
  await expect(items.first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(trigger).toBeFocused();

  await trigger.press("ArrowUp");
  await expect(items.last()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("picker search stays in place and Escape returns focus without choosing", async ({ page }) => {
  await page.goto("/#session=fixture");
  const find = page.getByRole("button", { name: "Find claim", exact: true });
  await find.press("Enter");
  const search = page.getByRole("combobox", { name: "Search claims on map" });
  await expect(search).toBeFocused();
  await search.fill("a claim that does not exist");
  await expect(page.getByRole("option")).toHaveCount(0);
  await expect(page.getByText("No matching claims.")).toBeVisible();
  await search.fill("单射");
  await expect(page.getByRole("option", { name: "单射映射的复合" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(search).toBeHidden();
  await expect(find).toBeFocused();
  await expect(page.getByRole("button", { name: "单射映射的复合", exact: true })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

test("a picker inside a native dialog keeps focus and Escape scoped to the picker", async ({ page }) => {
  await page.setViewportSize({ width: 760, height: 540 });
  await page.route("**/api/project-intake/models", (route) => route.fulfill({ json: { models: ["fixture-model"] } }));
  await page.goto("/#session=fixture");
  await page.getByRole("button", { name: "Add to project" }).click();
  await page.getByRole("menuitem", { name: /^Paper, notes or code…/ }).click();
  const information = page.getByRole("dialog", { name: "Import paper, notes or code" });
  const trigger = information.getByRole("button", { name: "Model", exact: true });
  await expect(trigger).toHaveAccessibleDescription("fixture-model");
  await trigger.press("Enter");
  const search = information.getByRole("combobox", { name: "Search models" });
  await expect(search).toBeFocused();
  await expect(information.getByRole("option", { name: "fixture-model" })).toBeInViewport({ ratio: 1 });

  await search.dispatchEvent("keydown", { key: "Escape", isComposing: true });
  await expect(search).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(search).toBeHidden();
  await expect(information).toBeVisible();
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(information).toBeHidden();
});
