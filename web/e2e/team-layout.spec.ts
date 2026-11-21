import { expect, test, type Page } from "@playwright/test";
import type { Team } from "../src/api/sections";

const HOST = "fixture-host";
const MODEL = "gpt-5-codex";
const TITLE = "自然数加零的对称性";

async function openTeam(page: Page, name = /^Team/) {
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [] } }));
  await page.goto("/#session=fixture");
  await page.locator("nav.rail").getByRole("link", { name }).click();
}

test("Team groups real data views and keeps both model connection entries", async ({ page }) => {
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({
      json: {
        connections: [
          {
            id: "fixture-connection",
            model: MODEL,
            provider: "codex-cli",
            label: "Research Codex",
            status: "connected",
          },
        ],
      },
    }),
  );
  await openTeam(page);
  const sidebar = page.locator(".section-nav");
  const main = page.locator(".section-main");
  await expect(sidebar.locator(".nav-group").nth(0).getByRole("link")).toHaveText(["Agents", "Models", /Jobs/]);
  await expect(sidebar.locator(".nav-group").nth(1).getByRole("link")).toHaveText(["Runtimes", "Usage"]);
  await expect(main.getByRole("rowheader", { name: new RegExp(HOST) })).toBeVisible();
  const connections = main.getByRole("region", { name: "Model connections" });
  const row = connections.getByRole("row", { name: /Research Codex/ });
  await expect(row.getByRole("button", { name: "Test connection" })).toBeEnabled();
  await expect(connections.getByRole("button", { name: "Add connection" })).toBeEnabled();
  await main.getByRole("button", { name: "Run check" }).click();
  await expect(main.getByText("All 2 checks passed.")).toBeVisible();

  await sidebar.getByRole("link", { name: "Models", exact: true }).click();
  await expect(row).toBeVisible();
  await expect(row.getByText("Connection tested", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: "Remove connection", exact: true }).click();
  await expect(row.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await row.getByRole("button", { name: "Cancel", exact: true }).click();

  await sidebar.getByRole("link", { name: "Agents", exact: true }).click();
  const agent = main.getByRole("row", { name: /GPT-5 Codex/ });
  await expect(agent).toBeVisible();
  await expect(agent.getByRole("cell", { name: "5", exact: true })).toBeVisible();
  await expect(main.getByText("Running", { exact: true })).toHaveCount(0);
  const search = main.getByRole("searchbox", { name: "Search agents or models" });
  await search.fill(MODEL);
  await expect(agent).toBeVisible();
  await expect(main.getByRole("rowheader", { name: "Lean verifier" })).toHaveCount(0);
  await main.getByRole("combobox", { name: "Filter by provider" }).selectOption("lean");
  await expect(main.getByRole("status")).toHaveText("No records match these filters.");
  await search.fill("");
  await expect(main.getByRole("rowheader", { name: "Lean verifier" })).toBeVisible();

  await sidebar.getByRole("link", { name: /^Jobs/ }).click();
  const jobs = main.getByRole("table");
  await expect(jobs.getByText("Running", { exact: true })).toHaveCount(2);
  await main.getByRole("combobox", { name: "Filter by status" }).selectOption("queued");
  await expect(jobs.getByText("Running", { exact: true })).toHaveCount(0);
  await expect(jobs.getByText("Queued", { exact: true })).toBeVisible();
  await main.getByRole("button", { name: TITLE }).press("Enter");
  await expect(page.getByRole("application", { name: "Project map", exact: true })).toBeVisible();
  await expect(page.locator("#node-detail")).toContainText(TITLE);
});

test("Team limits long text to its table in a compact Chinese window", async ({ page }) => {
  const longModel = "ResearchModel".repeat(13);
  await page.setViewportSize({ width: 640, height: 540 });
  await page.addInitScript(() => localStorage.setItem("research-multiplier-language", "zh-CN"));
  await page.route("**/api/team", async (route) => {
    const response = await route.fetch();
    const team = (await response.json()) as Team;
    team.agents = [
      {
        provider: "claude-code",
        model: longModel,
        runs: 1,
        last_at: null,
        average_ms: null,
        failures: 0,
        cost_usd: null,
        input_tokens: null,
        output_tokens: null,
      },
    ];
    await route.fulfill({ response, json: team });
  });
  await openTeam(page, /^团队/);
  const sidebar = page.locator(".section-nav");
  const main = page.locator(".section-main");
  await sidebar.getByRole("link", { name: "执行者", exact: true }).click();
  await expect(main.getByRole("searchbox", { name: "搜索执行者或模型" })).toBeVisible();
  const long = main.getByRole("cell", { name: longModel, exact: true });
  await expect(long).toBeVisible();
  await expect(long.evaluate((element) => element.scrollWidth <= element.clientWidth)).resolves.toBe(true);
  const [viewportWidth, scrollWidth] = await main.evaluate((element) => [element.clientWidth, element.scrollWidth]);
  expect(scrollWidth).toBe(viewportWidth);
  await expect(long.locator("..").getByText("未记录", { exact: true })).toHaveCount(2);

  await sidebar.getByRole("link", { name: "用量", exact: true }).click();
  await expect(main.getByRole("definition").filter({ hasText: /^未记录/ })).toHaveCount(2);
});

test("Team reports loading, empty and failed data without placeholder rows", async ({ page }) => {
  let release: () => void = () => {};
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = false;
  await page.route("**/api/team", async (route) => {
    await wait;
    if (fail) return route.fulfill({ status: 503, json: { error: "Fixture unavailable" } });
    const response = await route.fetch();
    const team = (await response.json()) as Team;
    team.agents = [];
    team.jobs = [];
    team.verifier = { runs: 0, failures: 0, last_at: null, average_ms: null };
    await route.fulfill({ response, json: team });
  });
  await openTeam(page);
  await page.locator(".section-nav").getByRole("link", { name: "Agents", exact: true }).click();
  const main = page.locator(".section-main");
  await expect(main.getByRole("status")).toContainText("Loading");
  release();
  await expect(
    main.getByRole("status").filter({ hasText: "No agent runs have been recorded in this project." }),
  ).toBeVisible();
  await expect(main.getByRole("table")).toHaveCount(0);
  fail = true;
  await page.reload();
  await expect(main.getByRole("alert")).toHaveText("Could not load this project's Team data.");
});
