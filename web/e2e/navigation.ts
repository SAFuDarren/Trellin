import { expect, type Locator, type Page } from "@playwright/test";

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

// #289: the rail shows spaces; each space's panel holds its pages. Tests reach a page the way a researcher
// does: the space in the rail, then the entry in its panel.
const SPACE_OF = {
  "Project map": "Home",
  Claims: "Work",
  Inbox: "Work",
  Review: "Work",
  "Agent tasks": "Work",
  Library: "Work",
} as const;

export type Destination = keyof typeof SPACE_OF;

export function railSpace(page: Page, space: string) {
  // A badge adds its count to the name ("Work · 2 waiting for you").
  return page.locator("nav.rail").getByRole("link", { name: new RegExp(`^${space}( ·|$)`) });
}

export async function openDestination(page: Page, destination: Destination) {
  const space = railSpace(page, SPACE_OF[destination]);
  await space.click();
  await expect(space).toHaveAttribute("aria-current", "page");
  if (destination === "Project map") return;
  const panel = page.locator("nav.section-nav");
  // An entry with a count is named with it ("Review 2").
  const entry = panel.getByRole("link", { name: new RegExp(`^${destination}( \\d+)?$`) });
  await entry.click();
  await expect(entry).toHaveAttribute("aria-current", "page");
}
