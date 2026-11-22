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

// #289: projects are switched from the tile at the top of the rail, as in Multica's workspace tile.
// Opening a project folder and moving the library are advanced tools in Settings.
const tile = (page: Page, name: string) =>
  page.locator("nav.rail").getByRole("button", { name: `Switch project, ${name}`, exact: true });

test("the rail tile opens the project switcher without folder actions", async ({ page }) => {
  const project = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Only Research Project", available: true };
  await stubLibrary(page, (request) => {
    if (request.action === "list") return { projects: [project], activeId: project.id };
    return true;
  });
  await page.goto("/#session=fixture");
  const switcher = tile(page, project.name);
  await expect(switcher).toHaveText("O");
  await expect(page.locator("#sidebar").getByRole("region", { name: "Projects" })).toHaveCount(0);
  await switcher.press("Enter");
  const dialog = page.getByRole("dialog", { name: `Switch project, ${project.name}` });
  await expect(dialog.getByRole("button", { name: project.name })).toHaveAttribute("aria-current", "page");
  await expect(dialog.getByRole("button", { name: "New project" })).toBeEnabled();
  await expect(dialog.getByRole("button", { name: "Open project folder" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(switcher).toBeFocused();
  // The map heading names the project but no longer repeats the switcher.
  await expect(page.getByRole("heading", { name: project.name, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: `Switch project, ${project.name}` })).toHaveCount(1);
});

test("the rail tile lists and opens registered desktop projects", async ({ page }) => {
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
  await page.goto("/#session=fixture");
  await tile(page, first.name).click();
  const projects = page.getByRole("dialog", { name: `Switch project, ${first.name}` });
  const firstRow = projects.getByRole("button", { name: first.name });
  const secondRow = projects.getByRole("button", { name: second.name });
  await expect(firstRow).toHaveAttribute("aria-current", "page");
  await expect(secondRow).toBeEnabled();
  await expect(projects.getByRole("button", { name: "Offline Research Unavailable" })).toBeDisabled();

  try {
    await secondRow.press("Enter");
    await expect(projects.getByRole("status")).toHaveText("Opening project…");
    await expect(secondRow).toBeDisabled();
    await expect(projects.getByRole("button", { name: "New project" })).toBeDisabled();
  } finally {
    finishOpening();
  }
  await expect(projects.getByRole("alert")).toContainText("Try again");
  await expect(firstRow).toHaveAttribute("aria-current", "page");
  await secondRow.press("Enter");
  await expect(projects.getByRole("status")).toHaveCount(0);
  await page.reload();
  await expect(tile(page, second.name)).toHaveText("S");
  await tile(page, second.name).click();
  const reopened = page.getByRole("dialog", { name: `Switch project, ${second.name}` });
  await expect(reopened.getByRole("button", { name: second.name })).toHaveAttribute("aria-current", "page");
  await reopened.getByRole("button", { name: "New project" }).click();
  await expect
    .poll(() => actions)
    .toEqual([{ action: "open", id: second.id }, { action: "open", id: second.id }, { action: "new" }]);
});

test("the map heading falls back to the name of the opened project directory", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, "trellinProjectName", { value: "My opened research" }));
  await page.goto("/#session=fixture");
  await expect(page.getByRole("heading", { name: "My opened research", exact: true })).toBeVisible();
});

test("an empty desktop library keeps New project and can recover from a list error", async ({ page }) => {
  let failing = true;
  await stubLibrary(page, (request) => {
    expect(request).toEqual({ action: "list" });
    if (failing) throw new Error("Fixture registry read failed");
    return { projects: [] };
  });
  await page.goto("/#session=fixture");
  await page
    .locator("nav.rail")
    .getByRole("button", { name: /^Switch project/ })
    .click();
  const projects = page.getByRole("dialog", { name: /^Switch project/ });
  await expect(projects.getByRole("alert")).toHaveText("Could not load registered projects.");
  failing = false;
  await projects.getByRole("button", { name: "Reload projects" }).click();
  await expect(projects.getByText("No registered projects yet.")).toBeVisible();
  await expect(projects.getByRole("button", { name: "New project" })).toBeEnabled();
});

test("Settings → Advanced opens a project folder and the library location", async ({ page }) => {
  const actions: DesktopProjectAction[] = [];
  await stubLibrary(page, (request) => {
    if (request.action === "list") return { projects: [] };
    actions.push(request);
    return true;
  });
  await page.goto("/#session=fixture");
  await page.goto("/#settings/open-folder");
  await page.getByRole("button", { name: "Open Folder…", exact: true }).click();
  await page.getByRole("button", { name: "Change Location…", exact: true }).click();
  await expect.poll(() => actions).toEqual([{ action: "folder" }, { action: "storage" }]);
});

test("a workbench without the native bridge shows the brand and the Home space", async ({ page }) => {
  await page.goto("/#session=fixture");
  await expect(page.locator("nav.rail").getByRole("link", { name: "Trellin", exact: true })).toBeVisible();
  await expect(page.locator("nav.rail").getByRole("button", { name: /^Switch project/ })).toHaveCount(0);
  await page.goto("/#settings");
  await expect(page.getByRole("button", { name: "Open Folder…" })).toHaveCount(0);
  await page.goto("/#workbench");
  const sidebar = page.locator("#sidebar");
  const expand = sidebar.getByRole("button", { name: "Expand sidebar", exact: true });
  if (await expand.isVisible()) await expand.click();
  await expect(sidebar.getByRole("link", { name: "Project map", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(sidebar.getByRole("link", { name: "Claims", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Find claim", exact: true }).click();
  const search = page.getByRole("combobox", { name: "Search claims on map" });
  await search.fill("单射");
  await expect(page.getByRole("option", { name: "单射映射的复合", exact: true })).toBeVisible();
});
