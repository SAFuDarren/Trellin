import { expect, test, type Locator } from "@playwright/test";
import { openDestination, railSpace } from "./navigation";

for (const theme of ["light", "dark"] as const) {
  test(`selected navigation and Library rows use fill, hover and focus in ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/#session=fixture");
    const rail = page.locator("nav.rail");
    const sidebar = page.locator(".section-nav");
    const selectedFill = await page.evaluate(() => {
      const probe = document.createElement("span");
      probe.hidden = true;
      probe.style.backgroundColor = "var(--state-selected)";
      document.body.append(probe);
      const fill = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return fill;
    });
    const expectSelected = async (item: Locator) => {
      await expect(item).toHaveCSS("background-color", selectedFill);
      await expect(item).toHaveCSS("box-shadow", "none");
    };
    await openDestination(page, "Claims");
    const work = railSpace(page, "Work");
    const allClaims = sidebar.getByRole("link", { name: "Claims", exact: true });
    const otherClaimView = sidebar.locator('.nav-item:not([aria-current="page"])').first();
    await expectSelected(work);
    await expectSelected(allClaims);
    const defaultFill = await otherClaimView.evaluate((item) => getComputedStyle(item).backgroundColor);
    await otherClaimView.hover();
    await expect(otherClaimView).not.toHaveCSS("background-color", defaultFill);
    await expect(otherClaimView).not.toHaveCSS("background-color", selectedFill);
    await page.keyboard.press("Tab");
    await allClaims.focus();
    await expect(allClaims).toHaveCSS("outline-width", "2px");
    await expect(allClaims).toHaveCSS("outline-style", "solid");

    await openDestination(page, "Library");
    const firstRow = page.locator(".library-table tr[data-row]").first();
    await firstRow.click();
    await expectSelected(sidebar.getByRole("link", { name: "Library", exact: true }));
    await expect(firstRow).toHaveAttribute("aria-selected", "true");
    await expectSelected(firstRow);
    await expect(firstRow.locator("td").first()).toHaveCSS("box-shadow", "none");

    await rail.getByRole("link", { name: "Chats", exact: true }).click();
    const chat = sidebar.locator(".chat-list-item").first();
    await chat.click();
    await expect(chat).toHaveAttribute("aria-current", "page");
    await expectSelected(chat);
  });
}
