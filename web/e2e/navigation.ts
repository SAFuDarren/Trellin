import { expect, type Locator } from "@playwright/test";

export async function controlledMenu(trigger: Locator) {
  await expect(trigger).toHaveAttribute("aria-controls", /.+/);
  const id = await trigger.getAttribute("aria-controls");
  return trigger.page().locator(`[id=${JSON.stringify(id)}]`);
}

export async function openMenu(trigger: Locator) {
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  const menu = await controlledMenu(trigger);
  await expect(menu).toBeVisible();
  return menu;
}
