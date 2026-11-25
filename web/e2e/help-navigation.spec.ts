import { expect, test, type Page } from "@playwright/test";

const destinations = {
  "#workbench": ["Project map", "项目图谱"],
  "#claims": ["Work", "工作"],
  "#library": ["Work", "工作"],
  "#agent-work/inbox": ["Work", "工作"],
  "#chat": ["Chats", "对话"],
  "#team": ["Team", "团队"],
  "#progress": ["Automation", "自动化"],
  "#settings": ["Settings", "设置"],
  "#team/agents": ["Agents", "执行者"],
  "#agent-work": ["Work", "工作"],
} as const;
type Destination = keyof typeof destinations;

const guides = [
  { path: "project-map", title: ["Build a project map", "建立项目图谱"], links: ["#workbench"] },
  { path: "agents", title: ["Coordinate Agents", "协调多个 Agent"], links: ["#team/agents", "#agent-work"] },
  { path: "proofs", title: ["Check and reuse a proof", "核验和复用证明"], links: ["#claims", "#library"] },
] as const;

async function expectDestination(page: Page, hash: Destination, locale: 0 | 1) {
  await expect(page).toHaveURL((url) => url.hash === hash);
  const name = destinations[hash][locale];
  if (hash === "#workbench") await expect(page.getByRole("application", { name, exact: true })).toBeVisible();
  else if (hash === "#team/agents")
    await expect(page.getByRole("main").getByRole("heading", { name, exact: true })).toBeVisible();
  else await expect(page.locator("#section-title")).toHaveText(name);
}

for (const [localeName, locale] of [
  ["English", 0],
  ["简体中文", 1],
] as const) {
  test(`Trellin Help links open existing pages and guides in ${localeName}`, async ({ page }) => {
    await page.setViewportSize(locale === 1 ? { width: 640, height: 460 } : { width: 1440, height: 900 });
    await page.goto("/#session=fixture");
    if (locale === 1) {
      await page.goto("/#settings");
      await page.getByRole("button", { name: localeName, exact: true }).click();
    }
    await page.goto("/#help");

    const inside = page.getByRole("main");
    const help = page.locator("nav.section-nav");
    const overview = ["Using Trellin", "使用 Trellin"][locale];
    await expect(page).toHaveTitle("Trellin");
    await expect(page.locator(".rail-brand")).toHaveAccessibleName("Trellin");
    await expect(inside.getByRole("heading", { name: overview, exact: true })).toBeVisible();
    await expect(inside.getByText(/Research Multiplier|\.md/)).toHaveCount(0);
    await expect(inside.locator('a[href="#review"]')).toHaveCount(0);

    const places = inside.getByRole("region", { name: ["Where to work", "工作入口"][locale], exact: true });
    // One link per space, in rail order (#289).
    // #326: Work opens on the claims board.
    const primaryDestinations = ["#workbench", "#claims", "#chat", "#team", "#progress"] as const;
    await expect(places.getByRole("link")).toHaveCount(primaryDestinations.length);
    for (const path of primaryDestinations) {
      await places.locator(`a[href="${path}"]`).click();
      await expectDestination(page, path, locale);
      await page.goto("/#help");
    }

    await expect(help.getByRole("link")).toHaveCount(guides.length + 1);
    for (const guide of guides) {
      const path = `#help/${guide.path}`;
      const link = help.locator(`a[href="${path}"]`);
      await link.click();
      await expect(page).toHaveURL((url) => url.hash === path);
      await expect(link).toHaveAttribute("aria-current", "page");
      await expect(inside.getByRole("heading", { name: guide.title[locale], exact: true })).toBeVisible();
      await expect(inside.getByRole("listitem")).toHaveCount(3);
      await expect(inside.getByRole("link")).toHaveCount(guide.links.length);
      for (const target of guide.links) {
        await inside.locator(`a[href="${target}"]`).click();
        await expectDestination(page, target, locale);
        await page.goBack();
        await expect(page).toHaveURL((url) => url.hash === path);
      }
      await help.getByRole("link", { name: overview, exact: true }).click();
      await expect(inside.getByRole("heading", { name: overview, exact: true })).toBeVisible();
    }

    const controls = inside.getByRole("region", { name: ["Quick controls", "常用操作"][locale], exact: true });
    const keys = await controls.locator("kbd").allTextContents();
    expect(keys).toHaveLength(2);
    const shortcuts = ["Keyboard shortcuts", "键盘快捷键"][locale];
    await inside.getByRole("button", { name: shortcuts, exact: true }).press("Enter");
    const sheet = page.getByRole("dialog", { name: shortcuts, exact: true });
    const list = sheet.getByRole("region", { name: ["Navigation", "导航"][locale], exact: true });
    for (const key of keys) await expect(list.getByText(key, { exact: true })).toBeVisible();
  });
}
