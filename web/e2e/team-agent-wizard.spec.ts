import { expect, test, type Page } from "@playwright/test";
import type { TeamAgent } from "../src/api/teamAgents";

const connection = {
  id: "9a33cb02-fa5c-40f4-bc2e-591c368e754e",
  provider: "codex-cli",
  model: "gpt-5-codex",
  label: "Research Codex",
  status: "connected",
  fingerprint: "a".repeat(64),
};
const unavailable = {
  ...connection,
  id: "1fcb84d3-e55f-47c4-ae0d-503fbab21455",
  label: "Unavailable runtime",
  status: "not_installed",
  fingerprint: null,
};

async function openAgents(page: Page) {
  await page.goto("/#session=fixture");
  await page.locator("nav.rail").getByRole("link", { name: /^Team/ }).click();
  await page.locator(".section-nav").getByRole("link", { name: "Agents", exact: true }).click();
  return page.getByRole("region", { name: "Saved agents" });
}

test("the Team wizard preserves drafts, retries the same create, and reopens the saved agent", async ({ page }) => {
  const saved = new Map<string, TeamAgent>();
  const attempts: TeamAgent[] = [];
  const unexpectedWrites: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method()) && path !== "/api/team-agents/save")
      unexpectedWrites.push(path);
  });
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({ json: { connections: [connection, unavailable] } }),
  );
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [...saved.values()] } }));
  await page.route("**/api/team-agents/save", async (route) => {
    const draft = route.request().postDataJSON() as TeamAgent;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-rm-session"]).toBe("fixture");
    attempts.push(draft);
    const previous = saved.get(draft.id);
    if (draft.revision === 0 && previous) return route.fulfill({ json: { agent: previous } });
    const record = { ...draft, revision: draft.revision + 1 };
    saved.set(record.id, record);
    // The operation was committed before the first reply was lost.
    if (attempts.length === 1) return route.fulfill({ status: 503, json: { error: "unreadable_response" } });
    await route.fulfill({ json: { agent: record } });
  });

  const roster = await openAgents(page);
  const add = page.getByRole("button", { name: "Add agent", exact: true });
  const wizard = page.getByRole("dialog");
  const next = wizard.getByRole("button", { name: "Next", exact: true });
  const name = wizard.getByRole("textbox", { name: "Name", exact: true });
  const avatar = wizard.getByRole("combobox", { name: "Avatar", exact: true });
  const description = wizard.getByRole("textbox", { name: "Description", exact: true });
  const instructions = wizard.getByRole("textbox", { name: "Instructions", exact: true });
  const connect = async () => {
    await expect(wizard.getByRole("heading", { name: "Choose runtime", exact: true })).toBeVisible();
    await wizard.getByRole("radio", { name: /Research Codex/ }).check();
    await next.click();
    await expect(wizard.getByRole("heading", { name: "Configure agent", exact: true })).toBeVisible();
  };

  await add.click();
  await connect();
  await name.fill("Canceled profile");
  await wizard.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(wizard).toBeHidden();
  expect(attempts).toHaveLength(0);

  await add.click();
  await connect();
  await expect(name).toHaveValue("");
  await expect(wizard.getByRole("combobox", { name: "Avatar", exact: true })).toHaveCSS("min-height", "38px");
  const create = wizard.getByRole("button", { name: "Create agent", exact: true });
  await name.fill("   ");
  await expect(create).toBeDisabled();
  await expect(wizard.getByRole("alert")).toBeVisible();
  expect(attempts).toHaveLength(0);
  await name.fill("Proof Scout");
  await avatar.selectOption("book");
  await description.fill("Looks for missing premises");
  await instructions.fill("State the assumed scope before proposing a proof.");
  const firstStarter = wizard.getByRole("textbox", { name: "Starter 1", exact: true });
  await firstStarter.fill("What is still unproved?");
  await wizard.getByRole("textbox", { name: "Starter 2", exact: true }).fill("Suggest a checking order");
  await wizard.getByRole("button", { name: "Back", exact: true }).click();
  await expect(wizard.getByRole("radio", { name: /Research Codex/ })).toBeChecked();
  await next.click();
  await expect(name).toHaveValue("Proof Scout");
  await expect(avatar).toHaveValue("book");
  await expect(description).toHaveValue("Looks for missing premises");
  await expect(instructions).toHaveValue("State the assumed scope before proposing a proof.");
  await expect(firstStarter).toHaveValue("What is still unproved?");
  expect(attempts).toHaveLength(0);

  await create.click();
  await expect(wizard.getByRole("alert")).toBeVisible();
  await expect(name).toHaveValue("Proof Scout");
  await create.click();
  await expect(wizard).toBeHidden();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  expect(attempts[0]).toMatchObject({
    id: expect.stringMatching(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i),
    revision: 0,
    connection_id: connection.id,
    name: "Proof Scout",
    avatar: "book",
    description: "Looks for missing premises",
    instructions: "State the assumed scope before proposing a proof.",
    starters: ["What is still unproved?", "Suggest a checking order"],
  });
  expect(saved.size).toBe(1);
  await expect(roster.getByRole("row", { name: /Proof Scout/ })).toBeVisible();

  await page.reload();
  await roster
    .getByRole("row", { name: /Proof Scout/ })
    .getByRole("button", { name: /^Edit/ })
    .click();
  await expect(name).toHaveValue("Proof Scout");
  await expect(avatar).toHaveValue("book");
  await expect(firstStarter).toHaveValue("What is still unproved?");
  await name.fill("Proof Scout revised");
  await wizard.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(wizard).toBeHidden();
  expect(attempts[2]).toMatchObject({ id: attempts[0]!.id, revision: 1, name: "Proof Scout revised" });
  await page.reload();
  await expect(roster.getByRole("row", { name: /Proof Scout revised/ })).toBeVisible();
  expect(saved.size).toBe(1);
  expect(unexpectedWrites).toEqual([]);
});

test("an unavailable runtime cannot be used to create an agent", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 640 });
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({
      json: { connections: [unavailable, { ...connection, fingerprint: null }] },
    }),
  );
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [] } }));
  let writes = 0;
  await page.route("**/api/team-agents/save", (route) => {
    writes += 1;
    return route.fulfill({ status: 409, json: { error: "team_agents.connection_unavailable" } });
  });

  await openAgents(page);
  await page.getByRole("button", { name: "Add agent", exact: true }).click();
  const wizard = page.getByRole("dialog", { name: "Choose runtime" });
  await expect(wizard.getByText(/No available connections/)).toBeVisible();
  await expect(wizard.getByRole("radio", { name: /Unavailable runtime/ })).toBeDisabled();
  await expect(wizard.getByRole("radio", { name: /Research Codex/ })).toBeDisabled();
  await expect(wizard.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  const manage = wizard.getByRole("button", { name: "Manage model connections", exact: true });
  await manage.click();
  const models = page.getByRole("dialog", { name: "Model connections", exact: true });
  await expect(models).toBeVisible();
  await models.getByRole("button", { name: "Close", exact: true }).click();
  await expect(manage).toBeFocused();
  await wizard.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(wizard).toBeHidden();
  expect(writes).toBe(0);
});
