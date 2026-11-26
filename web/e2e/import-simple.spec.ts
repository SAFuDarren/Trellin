import { expect, test, type Page } from "@playwright/test";
import { IMPORT_DIALOG, IMPORT_ENTRY, openAddEntry } from "./project-map";

// #355: importing is choose a file, then Organize; the details wait under Advanced options.

const CONNECTION = {
  id: "fixture-remote",
  label: "Fixture remote",
  model: "fixture",
  provider: "openai",
  fingerprint: "x",
  status: "connected",
};

async function models(page: Page, local: string[], connections: (typeof CONNECTION)[] = []) {
  await page.route("**/api/project-intake/models", (route) => route.fulfill({ json: { models: local } }));
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { connections } }));
}

async function openImport(page: Page) {
  await page.goto("/#session=fixture");
  await openAddEntry(page, IMPORT_ENTRY);
  return page.getByRole("dialog", { name: IMPORT_DIALOG, exact: true });
}

test("a chosen file is organized with one click, with its text, name and the project's last model", async ({
  page,
}) => {
  await models(page, ["first-local", "second-local"]);
  const sent: Record<string, unknown>[] = [];
  await page.route("**/api/project-intake", (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: { drafts: [] } });
    sent.push(route.request().postDataJSON());
    return route.fulfill({ json: { id: "draft", sha256: "x", title: "group-notes", items: [], confirmed: false } });
  });
  let dialog = await openImport(page);
  await expect(dialog.locator("input[type=file]:visible")).toHaveCount(0);
  await expect(dialog.getByLabel("Title")).toHaveCount(0);
  await expect(dialog.getByText("Read by first-local")).toBeVisible();
  await dialog
    .locator(".import-file-input")
    .setInputFiles({ name: "group-notes.md", mimeType: "text/markdown", buffer: Buffer.from("Lemma. A.\n") });
  await expect(dialog.locator(".import-source-name")).toHaveText("group-notes.md");
  await expect(dialog.locator(".import-source-size")).toHaveText("10 B");
  await dialog.getByRole("button", { name: "Organize", exact: true }).click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ title: "group-notes", notes: "Lemma. A.", model: "first-local" });

  // Advanced options: another model, remembered with the panel for the next import.
  await page.keyboard.press("Escape");
  dialog = await openImport(page);
  await dialog.getByRole("button", { name: "Advanced options" }).click();
  await dialog.getByRole("button", { name: "Model", exact: true }).click();
  await dialog.getByRole("option", { name: "second-local" }).click();
  await dialog.getByRole("button", { name: "Paste text instead" }).click();
  await dialog.getByLabel("Research notes or Lean text").fill("Definition. B.");
  await dialog.getByLabel("Title", { exact: true }).fill("Pasted notes");
  await dialog.getByRole("button", { name: "Organize", exact: true }).click();
  await expect.poll(() => sent.length).toBe(2);
  expect(sent[1]).toMatchObject({ title: "Pasted notes", notes: "Definition. B.", model: "second-local" });
  await page.keyboard.press("Escape");
  dialog = await openImport(page);
  await expect(dialog.getByRole("button", { name: "Advanced options" })).toHaveAttribute("aria-expanded", "true");
  await expect(dialog.getByText("Read by second-local")).toBeVisible();
});

test("without a usable model the import says so in one line and links to model connections", async ({ page }) => {
  await models(page, []);
  const dialog = await openImport(page);
  await expect(dialog.getByText("No model can read it yet.")).toBeVisible();
  await expect(dialog.getByRole("link", { name: "Connect a model" })).toHaveAttribute("href", /#team\/runtimes/);
  await expect(dialog.getByRole("button", { name: "Model", exact: true })).toHaveCount(0);
});

test("a cloud model is allowed once per project, then imports go straight out and can be withdrawn", async ({
  page,
}) => {
  await models(page, [], [CONNECTION]);
  let granted = false;
  const sent: Record<string, unknown>[] = [];
  await page.route("**/api/project-intake/grants", (route) =>
    route.fulfill({
      json: {
        grants: granted
          ? [
              {
                connection_id: CONNECTION.id,
                provider: "openai",
                model: "fixture",
                researcher_id: "r",
                granted_at: "t",
              },
            ]
          : [],
      },
    }),
  );
  await page.route("**/api/project-intake/grant", (route) => {
    granted = true;
    return route.fulfill({ json: { connection_id: CONNECTION.id } });
  });
  await page.route("**/api/project-intake/revoke", (route) => {
    granted = false;
    return route.fulfill({ json: { connection_id: CONNECTION.id, revoked: true } });
  });
  await page.route("**/api/project-intake/public-draft", (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill({
      json: { id: `draft-${sent.length}`, sha256: "x", title: "Sent", items: [], confirmed: false },
    });
  });

  const organize = async (notes: string) => {
    const dialog = await openImport(page);
    await dialog.getByRole("button", { name: "Paste text instead" }).click();
    await dialog.getByLabel("Research notes or Lean text").fill(notes);
    await dialog.getByRole("button", { name: "Organize", exact: true }).click();
    return dialog;
  };

  let dialog = await organize("First excerpt.");
  const ask = dialog.getByRole("group", { name: "Allow this model for imports" });
  await expect(ask).toContainText("Fixture remote");
  expect(sent).toHaveLength(0);
  await ask.getByRole("button", { name: "Allow and organize" }).click();
  await expect.poll(() => sent.length).toBe(1);
  await page.keyboard.press("Escape");

  dialog = await organize("Second excerpt.");
  await expect.poll(() => sent.length).toBe(2);
  await expect(dialog.getByRole("group", { name: "Allow this model for imports" })).toHaveCount(0);
  expect(sent.map((body) => body.notes)).toEqual(["First excerpt.", "Second excerpt."]);
  expect(sent.every((body) => body.connection_id === CONNECTION.id && !("token" in body))).toBe(true);
  await page.keyboard.press("Escape");

  await page.goto("/#team/runtimes");
  const row = page.getByRole("row", { name: /Fixture remote/ });
  await expect(row).toContainText("Allowed for this project's imports");
  await row.getByRole("button", { name: "Withdraw Fixture remote from this project's imports" }).click();
  await expect(row).not.toContainText("Allowed for this project's imports");
  dialog = await organize("Third excerpt.");
  await expect(dialog.getByRole("group", { name: "Allow this model for imports" })).toBeVisible();
  expect(sent).toHaveLength(2);
});
