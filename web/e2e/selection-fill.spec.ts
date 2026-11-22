import { expect, test, type Locator } from "@playwright/test";

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
    // A browser session has one project, so the project sidebar starts folded (#283).
    await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
    await expectSelected(page.locator('.sidebar-projects-row[aria-current="page"]'));

    const claims = rail.getByRole("link", { name: "Claims", exact: true });
    await claims.click();
    const allClaims = sidebar.getByRole("link", { name: /^All claims/ });
    const otherClaimView = sidebar.locator('.nav-item:not([aria-current="page"])').first();
    await expectSelected(claims);
    await expectSelected(allClaims);
    const defaultFill = await otherClaimView.evaluate((item) => getComputedStyle(item).backgroundColor);
    await otherClaimView.hover();
    await expect(otherClaimView).not.toHaveCSS("background-color", defaultFill);
    await expect(otherClaimView).not.toHaveCSS("background-color", selectedFill);
    await page.keyboard.press("Tab");
    await allClaims.focus();
    await expect(allClaims).toHaveCSS("outline-width", "2px");
    await expect(allClaims).toHaveCSS("outline-style", "solid");

    await rail.getByRole("link", { name: "Library", exact: true }).click();
    const firstRow = page.locator(".library-table tr[data-row]").first();
    await firstRow.click();
    await expectSelected(sidebar.locator('[aria-current="page"]'));
    await expect(firstRow).toHaveAttribute("aria-selected", "true");
    await expectSelected(firstRow);
    await expect(firstRow.locator("td").first()).toHaveCSS("box-shadow", "none");

    await rail.getByRole("link", { name: "Chats", exact: true }).click();
    const chat = page.locator(".chats-sidebar .chat-list-item").first();
    await chat.click();
    await expect(chat).toHaveAttribute("aria-current", "page");
    await expectSelected(chat);
  });
}
