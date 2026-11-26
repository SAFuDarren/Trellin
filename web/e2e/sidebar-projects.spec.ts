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
  await expect(page.locator(".section-nav")).toHaveCount(0);
  await switcher.press("Enter");
  const dialog = page.getByRole("dialog", { name: `Switch project, ${project.name}` });
  await expect(dialog.getByRole("button", { name: project.name, exact: true })).toHaveAttribute("aria-current", "page");
  await expect(dialog.getByRole("button", { name: "New project" })).toBeEnabled();
  await expect(dialog.getByRole("button", { name: "Open project folder" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(switcher).toBeFocused();
  await expect(page.getByRole("main").getByRole("heading", { name: "Project map", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: project.name, exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: `Switch project, ${project.name}` })).toHaveCount(1);
});

for (const colorScheme of ["light", "dark"] as const) {
  test(`the project tile is the brand navy with a white letter in ${colorScheme}`, async ({ page }) => {
    // #349: the tile marks the project, not a selection, so it never takes the selection accent.
    await page.emulateMedia({ colorScheme });
    const project = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Only Research Project", available: true };
    await stubLibrary(page, (request) =>
      request.action === "list" ? { projects: [project], activeId: project.id } : true,
    );
    await page.goto("/#session=fixture");
    const switcher = tile(page, project.name);
    await expect(switcher).toHaveCSS("background-color", "rgb(22, 35, 63)");
    await expect(switcher).toHaveCSS("color", "rgb(255, 255, 255)");
  });
}

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
  const firstRow = projects.getByRole("button", { name: first.name, exact: true });
  const secondRow = projects.getByRole("button", { name: second.name, exact: true });
  await expect(firstRow).toHaveAttribute("aria-current", "page");
  await expect(secondRow).toBeEnabled();
  await expect(projects.getByRole("button", { name: "Offline Research Unavailable", exact: true })).toBeDisabled();

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
  await expect(reopened.getByRole("button", { name: second.name, exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await reopened.getByRole("button", { name: "New project" }).click();
  await expect
    .poll(() => actions)
    .toEqual([{ action: "open", id: second.id }, { action: "open", id: second.id }, { action: "new" }]);
});

test("the rail tile uses the opened directory name for a project outside the library", async ({ page }) => {
  await stubLibrary(page, () => ({ projects: [] }));
  await page.addInitScript(() => Object.defineProperty(window, "trellinProjectName", { value: "My opened research" }));
  await page.goto("/#session=fixture");
  await expect(tile(page, "My opened research")).toBeVisible();
  await expect(page.getByRole("main").getByRole("heading", { name: "Project map", exact: true })).toBeVisible();
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
  await expect(page.locator(".section-nav")).toHaveCount(0);
  await expect(page.getByRole("main").getByRole("heading", { name: "Project map", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Find claim", exact: true }).click();
  const search = page.getByRole("combobox", { name: "Search claims on map" });
  await search.fill("单射");
  await expect(page.getByRole("option", { name: "单射映射的复合", exact: true })).toBeVisible();
});

// #302: the project Trellin created is shown in the interface language, an empty one is left out by the app,
// and any project can be renamed or removed from the list without touching its folder.
test("projects are renamed and removed from the switcher, keeping the default and the open project", async ({
  page,
}) => {
  const created = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "My research", available: true, default: true };
  const own = { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Own Research", available: true };
  const old = { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", name: "Old Notes", available: true };
  let projects = [created, own, old];
  const actions: DesktopProjectAction[] = [];
  let failNext = false;
  await stubLibrary(page, (request) => {
    if (request.action === "list") return { projects, activeId: own.id };
    actions.push(request);
    if (failNext) {
      failNext = false;
      throw new Error("Fixture library busy");
    }
    if (request.action === "rename") {
      projects = projects.map((project) => (project.id === request.id ? { ...project, name: request.name } : project));
    }
    if (request.action === "remove") projects = projects.filter((project) => project.id !== request.id);
    return true;
  });
  await page.goto("/#session=fixture");
  await tile(page, own.name).click();
  // The dialog is named after the open project, which this test renames.
  const dialog = page.locator(".project-switcher-panel");
  await expect(dialog.getByRole("button", { name: "My research", exact: true })).toBeEnabled();

  // The default can be renamed but never removed: it is the fallback when nothing else opens.
  await dialog.getByRole("button", { name: "Manage My research", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Rename", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Remove from list", exact: true })).toHaveCount(0);

  // The open project cannot be removed until another one is open.
  await dialog.getByRole("button", { name: "Manage Own Research", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Remove from list", exact: true })).toBeDisabled();
  await expect(dialog.getByText("Switch to another project before removing this one.")).toBeVisible();

  await dialog.getByRole("button", { name: "Rename", exact: true }).click();
  const field = dialog.getByRole("textbox", { name: "Project name", exact: true });
  await expect(field).toBeFocused();
  await expect(field).toHaveValue("Own Research");
  await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  await field.fill("  Thesis  ");
  await field.press("Enter");
  await expect(dialog.getByRole("button", { name: "Thesis", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(
    page.locator("nav.rail").getByRole("button", { name: "Switch project, Thesis", exact: true }),
  ).toBeVisible();
  expect(actions).toEqual([{ action: "rename", id: own.id, name: "Thesis" }]);

  // Removing asks first, names what stays on disk, and reports a failure without changing the list.
  await dialog.getByRole("button", { name: "Manage Old Notes", exact: true }).click();
  await dialog.getByRole("button", { name: "Remove from list", exact: true }).click();
  await expect(
    dialog.getByText("Remove “Old Notes” from the list? Its folder and database stay on this Mac."),
  ).toBeVisible();
  failNext = true;
  await dialog.getByRole("button", { name: "Remove from list", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Could not change the project list. Try again.");
  await expect(dialog.getByRole("button", { name: "Old Notes", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Remove from list", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Old Notes", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  expect(actions.slice(1)).toEqual([
    { action: "remove", id: old.id },
    { action: "remove", id: old.id },
  ]);
});

test("the project Trellin created is shown in Chinese", async ({ page }) => {
  const created = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "My research", available: true, default: true };
  await page.addInitScript(() => localStorage.setItem("research-multiplier-language", "zh-CN"));
  await stubLibrary(page, (request) =>
    request.action === "list" ? { projects: [created], activeId: created.id } : true,
  );
  await page.goto("/#session=fixture");
  const switcher = page.locator("nav.rail").getByRole("button", { name: "切换项目，我的研究", exact: true });
  await expect(switcher).toHaveText("我");
  await switcher.click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "我的研究", exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
