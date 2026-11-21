import { expect, test, type Page } from "@playwright/test";
import type { DesktopProjectAction } from "../src/workbench/desktop";

type Request = DesktopProjectAction | { action: "list" };

async function stubLibrary(page: Page, reply: (request: Request) => unknown) {
  await page.exposeFunction("fixtureProjectRequest", reply);
  await page.addInitScript(() => {
    const native = window as Window & { fixtureProjectRequest?: (request: Request) => Promise<unknown> };
    Object.defineProperty(window, "webkit", {
      value: {
        messageHandlers: {
          trellinProjects: { postMessage: (request: Request) => native.fixtureProjectRequest!(request) },
        },
      },
    });
  });
}

test("the sidebar lists and opens registered desktop projects", async ({ page }) => {
  const first = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "First Research", available: true };
  const second = { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Second Research", available: true };
  const missing = { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", name: "Offline Research", available: false };
  const actions: DesktopProjectAction[] = [];
  let activeId = first.id.toUpperCase();
  let finishOpening = () => {};
  const waitForOpening = new Promise<void>((resolve) => {
    finishOpening = resolve;
  });
  await stubLibrary(page, async (request) => {
    if (request.action === "list") return { projects: [first, second, missing], activeId };
    actions.push(request);
    if (request.action === "open") {
      if (actions.length === 1) {
        await waitForOpening;
        throw new Error("Fixture project temporarily unavailable");
      }
      activeId = request.id;
    }
    return true;
  });
  await page.setViewportSize({ width: 640, height: 720 });
  await page.goto("/#session=fixture");

  const sidebar = page.locator("#sidebar");
  const projects = sidebar.getByRole("region", { name: "Projects" });
  const firstRow = projects.getByRole("button", { name: first.name });
  const secondRow = projects.getByRole("button", { name: second.name });
  const openProjects = sidebar.getByRole("button", { name: "Projects", exact: true });
  await expect(projects).toHaveCount(0);
  await openProjects.press("Enter");
  const heading = projects.getByRole("heading", { name: "Projects" });
  await expect(heading).toBeFocused();
  await expect(heading).toHaveCSS("font-size", "15px");
  await expect(heading).toHaveCSS("font-weight", "600");
  await expect(firstRow).toHaveAttribute("aria-current", "page");
  await expect(secondRow).toBeEnabled();
  await expect(secondRow).toHaveCSS("font-size", "14px");
  await expect(secondRow).toHaveCSS("font-weight", "400");
  await expect(projects.getByText("Unavailable", { exact: true })).toHaveCSS("font-size", "12px");
  await expect(projects.getByRole("button", { name: "Offline Research Unavailable" })).toBeDisabled();
  expect(await sidebar.evaluate((element) => element.scrollWidth)).toBe(
    await sidebar.evaluate((element) => element.clientWidth),
  );

  await expect(sidebar.getByRole("searchbox")).toHaveCount(0);
  await expect(sidebar.getByRole("button", { name: "Global map" })).toHaveCount(0);
  await expect(sidebar.getByRole("button", { name: /^单射映射的复合/ })).toHaveCount(0);

  try {
    await secondRow.press("Enter");
    await expect(projects.getByRole("status")).toHaveText("Opening project…");
    await expect(secondRow).toBeDisabled();
    await expect(projects.getByRole("button", { name: "New project" })).toBeDisabled();
    await expect(secondRow).toBeFocused();
  } finally {
    finishOpening();
  }
  await expect(projects.getByRole("alert")).toContainText("Try again");
  await expect(firstRow).toHaveAttribute("aria-current", "page");
  await secondRow.press("Enter");
  await expect(projects.getByRole("status")).toHaveCount(0);
  await expect(firstRow).toHaveAttribute("aria-current", "page");
  await page.reload();
  await expect(secondRow).toHaveAttribute("aria-current", "page");
  await expect(firstRow).toBeEnabled();

  await projects.getByRole("button", { name: "New project" }).click();
  await projects.getByRole("button", { name: "Open project folder" }).click();
  await expect
    .poll(() => actions)
    .toEqual([
      { action: "open", id: second.id },
      { action: "open", id: second.id },
      { action: "new" },
      { action: "folder" },
    ]);
  await sidebar.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(projects).toHaveCount(0);
  await openProjects.press("Enter");
  await expect(heading).toBeFocused();
});

test("an empty desktop library retains the project actions and can recover from a list error", async ({ page }) => {
  let reads = 0;
  await stubLibrary(page, (request) => {
    expect(request).toEqual({ action: "list" });
    if (++reads === 1) throw new Error("Fixture registry read failed");
    return { projects: [] };
  });
  await page.goto("/#session=fixture");
  const projects = page.locator("#sidebar").getByRole("region", { name: "Projects" });
  await expect(projects.getByRole("alert")).toHaveText("Could not load registered projects.");
  await projects.getByRole("button", { name: "Reload projects" }).click();
  await expect(projects.getByText("No registered projects yet.")).toBeVisible();
  await expect(projects.getByRole("button", { name: "New project" })).toBeEnabled();
  await expect(projects.getByRole("button", { name: "Open project folder" })).toBeEnabled();
});

test("a workbench without the native bridge can find claims on the project map", async ({ page }) => {
  await page.goto("/#session=fixture");
  const sidebar = page.locator("#sidebar");
  await expect(sidebar.getByRole("searchbox")).toHaveCount(0);
  await expect(sidebar.getByRole("region", { name: "Projects" }).getByText("Current project")).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "New project" })).toHaveCount(0);
  await page.getByRole("button", { name: "Find claim", exact: true }).click();
  const search = page.getByRole("combobox", { name: "Search claims on map" });
  await search.fill("单射");
  await expect(page.getByRole("option", { name: "单射映射的复合", exact: true })).toBeVisible();
});
