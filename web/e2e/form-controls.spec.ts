import { expect, test, type Locator, type Page } from "@playwright/test";
import { openAddEntry, THEOREM_ENTRY } from "./project-map";

// Regression for #152: a fixed select height combined with vertical padding left
// less room than one line of text, so the selected option was cut off.

type SelectMetrics = { label: string; content: number; line: number; height: number; chevron: boolean };

async function selectMetrics(scope: Locator): Promise<SelectMetrics[]> {
  const selects = scope.locator("select:visible");
  await expect(selects.first()).toBeVisible();
  return selects.evaluateAll((elements) =>
    elements.map((element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      const px = (value: string) => Number.parseFloat(value) || 0;
      return {
        label: (element as HTMLSelectElement).selectedOptions[0]?.text ?? "",
        content:
          box.height -
          px(style.paddingTop) -
          px(style.paddingBottom) -
          px(style.borderTopWidth) -
          px(style.borderBottomWidth),
        // A normal line box is roughly 1.2 × the font size.
        line: px(style.fontSize) * 1.2,
        height: box.height,
        chevron: style.backgroundImage.includes("linear-gradient"),
      };
    }),
  );
}

async function expectLegible(scope: Locator) {
  const metrics = await selectMetrics(scope);
  for (const metric of metrics) {
    expect(metric.content, `"${metric.label}" has room for one line`).toBeGreaterThanOrEqual(metric.line);
    expect(metric.chevron, `"${metric.label}" uses the shared chevron`).toBe(true);
  }
  return metrics;
}

function collectErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function heightOf(locator: Locator) {
  return locator.evaluate((element) => element.getBoundingClientRect().height);
}

async function openTeam(page: Page) {
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [] } }));
  await page.goto("/#session=fixture");
  await page.locator("nav.rail").getByRole("link", { name: /^Team/ }).click();
}

test("New theorem selects show their whole option and match the text fields", async ({ page }) => {
  const errors = collectErrors(page);
  await page.route("**/api/theorems", (route) => route.fulfill({ json: { drafts: [] } }));
  await page.goto("/#session=fixture");
  await openAddEntry(page, THEOREM_ENTRY);
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Destination · Current project graph")).toBeVisible();

  const [kind] = await expectLegible(dialog);
  if (!kind) throw new Error("No select in New theorem");
  expect(kind.label).toBe("Theorem to prove");
  // The dialog scales in (#163), so compare once both fields have settled.
  const title = dialog.getByLabel("Title", { exact: true });
  const select = dialog.locator("select").first();
  await expect.poll(async () => Math.abs((await heightOf(select)) - (await heightOf(title)))).toBeLessThan(0.01);
  expect(errors).toEqual([]);
});

test("Team filters share the search field height", async ({ page }) => {
  const errors = collectErrors(page);
  await openTeam(page);
  const main = page.locator(".section-main");
  await page.locator(".section-nav").getByRole("link", { name: /^Jobs/ }).click();
  const tools = main.locator(".team-tools");
  const [filter] = await expectLegible(tools);
  if (!filter) throw new Error("No Team filter");
  expect(filter.height).toBe(await heightOf(tools.locator(".search-field")));
  expect(errors).toEqual([]);
});
