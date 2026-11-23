import { expect, test, type Page } from "@playwright/test";
import type { TeamAgent } from "../src/api/teamAgents";

// #323: Chats keeps the saved Agents as its entries, and role templates create one in a single review.
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
  label: "Offline runtime",
  status: "not_installed",
  fingerprint: null,
};

async function library(page: Page, initial: TeamAgent[] = []) {
  const saved = new Map(initial.map((agent) => [agent.id, agent]));
  const writes: TeamAgent[] = [];
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({ json: { connections: [unavailable, connection] } }),
  );
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [...saved.values()] } }));
  await page.route("**/api/team-agents/save", (route) => {
    const draft = route.request().postDataJSON() as TeamAgent;
    writes.push(draft);
    const record = { ...draft, revision: draft.revision + 1 };
    saved.set(record.id, record);
    return route.fulfill({ json: { agent: record } });
  });
  await page.route("**/api/conversations", (route) => route.fulfill({ json: { threads: [] } }));
  return writes;
}

const panel = (page: Page) => page.locator(".section-nav");

for (const colorScheme of ["light", "dark"] as const) {
  test(`an empty team starts from a role template and lands in the new Agent's chat in ${colorScheme}`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme });
    const writes = await library(page);
    await page.goto("/#session=fixture");
    await page
      .locator("nav.rail")
      .getByRole("link", { name: /^Chats/ })
      .click();

    const templates = panel(page).getByRole("region", { name: "Add your first teammate" });
    await expect(templates).toBeVisible();
    await expect(templates.getByRole("button")).toHaveCount(4);
    await expect(panel(page).getByText("No conversations yet.")).toHaveCount(0);
    await templates.getByRole("button", { name: /^Lean prover/ }).click();

    // Every field is filled and the first working connection is chosen, so only Create remains.
    const wizard = page.getByRole("dialog", { name: "Configure agent" });
    await expect(wizard.getByRole("textbox", { name: "Name", exact: true })).toHaveValue("Lean prover");
    await expect(wizard.getByRole("textbox", { name: "Description", exact: true })).not.toHaveValue("");
    await expect(wizard.getByRole("textbox", { name: "Instructions", exact: true })).not.toHaveValue("");
    await expect(wizard.getByRole("textbox", { name: "Starter 1", exact: true })).not.toHaveValue("");
    await expect(wizard.getByText(/Codex/).first()).toBeVisible();
    await wizard.getByRole("button", { name: "Create agent", exact: true }).click();
    await expect(wizard).toBeHidden();

    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({
      revision: 0,
      connection_id: connection.id,
      name: "Lean prover",
      avatar: "spark",
    });
    await expect(page).toHaveURL(new RegExp(`#chat/employee%2F${writes[0]!.id}$`));
    const person = panel(page).getByRole("link", { name: /^Lean prover/ });
    await expect(person).toHaveAttribute("aria-current", "page");
    await expect(person).toContainText("Works on Lean proofs");
    await expect(page.getByRole("textbox", { name: "Message Lean prover" })).toBeFocused();
  });
}

test("saved Agents stay listed and one click opens a chat addressed to them", async ({ page }) => {
  const agents: TeamAgent[] = [
    {
      id: "11111111-aaaa-4aaa-8aaa-111111111111",
      revision: 1,
      connection_id: connection.id,
      name: "Ada",
      description: "Checks Lean statements",
      avatar: "compass",
      instructions: "",
      starters: [],
    },
    {
      id: "22222222-bbbb-4bbb-8bbb-222222222222",
      revision: 1,
      connection_id: connection.id,
      name: "Basil",
      description: "Finds related results",
      avatar: "book",
      instructions: "",
      starters: [],
    },
  ];
  await library(page, agents);
  await page.goto("/#session=fixture");
  await page
    .locator("nav.rail")
    .getByRole("link", { name: /^Chats/ })
    .click();

  await expect(panel(page).getByRole("region", { name: "Add your first teammate" })).toHaveCount(0);
  const people = panel(page).getByRole("list", { name: "Employees" }).getByRole("link");
  await expect(people).toHaveCount(2);
  await expect(people.nth(0)).toContainText("Checks Lean statements");
  await people.nth(1).click();
  await expect(page).toHaveURL(new RegExp(`#chat/employee%2F${agents[1]!.id}$`));
  await expect(people.nth(1)).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("textbox", { name: "Message Basil" })).toBeFocused();
});

test("Team's Add agent starts with the roles and falls back to the connection step without a working one", async ({
  page,
}) => {
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections: [unavailable] } }));
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [] } }));
  await page.goto("/#session=fixture");
  await page.locator("nav.rail").getByRole("link", { name: /^Team/ }).click();
  await page.locator(".section-nav").getByRole("link", { name: "Agents", exact: true }).click();
  await page.getByRole("button", { name: "Add agent", exact: true }).click();
  const wizard = page.getByRole("dialog");
  await expect(wizard.getByRole("heading", { name: "Choose a role", exact: true })).toBeVisible();
  await wizard.getByRole("button", { name: /^Independent reviewer/ }).click();
  await expect(wizard.getByRole("heading", { name: "Choose runtime", exact: true })).toBeVisible();
  await expect(wizard.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  await wizard.getByRole("button", { name: "Back", exact: true }).click();
  await expect(wizard.getByRole("heading", { name: "Choose a role", exact: true })).toBeVisible();
});
