import { expect, test, type Page } from "@playwright/test";
import {
  ADD_MENU,
  FIXTURE_CLAIM_TITLE,
  IMPORT_DIALOG,
  mapClaim,
  openAddEntry,
  THEOREM_DIALOG,
  THEOREM_ENTRY,
  IMPORT_ENTRY,
} from "./project-map";

const NOTES = "# Group actions\n\nLemma. Every orbit is a block of the partition.\n";

/** Drop one synthetic file on the project map, as Finder would. */
async function dropFile(page: Page, name: string, body: string | number[], type: string) {
  const transfer = await page.evaluateHandle(
    ({ name, body, type }) => {
      const data = new DataTransfer();
      const bytes = typeof body === "string" ? body : new Uint8Array(body);
      data.items.add(new File([bytes], name, { type }));
      return data;
    },
    { name, body, type },
  );
  const area = page.locator(".project-map-area");
  await area.dispatchEvent("dragover", { dataTransfer: transfer });
  await expect(page.getByText("Drop to import", { exact: true })).toBeVisible();
  await area.dispatchEvent("drop", { dataTransfer: transfer });
}

async function emptyProject(page: Page) {
  await page.route("**/api/claims", (route) => route.fulfill({ json: { claims: [], claim_id: null } }));
  await page.route("**/api/project-canvas/claims", (route) => route.fulfill({ json: { claims: [] } }));
  await page.route("**/api/atlas", (route) => route.fulfill({ json: { claims: [] } }));
  await page.goto("/#session=fixture");
}

test("the project map has one Add menu with the import and a new theorem", async ({ page }) => {
  await page.goto("/#session=fixture");
  const header = page.locator("#workbench .page-actions");
  await expect(header.getByRole("button", { name: "Research actions" })).toHaveCount(0);
  await expect(header.getByRole("button", { name: "Add theorem" })).toHaveCount(0);
  await header.getByRole("button", { name: ADD_MENU, exact: true }).click();
  const items = page.getByRole("menu", { name: ADD_MENU }).getByRole("menuitem");
  await expect(items).toHaveCount(2);
  await expect(items.first()).toContainText("or drop a file on the map");
  await page.keyboard.press("Escape");

  await openAddEntry(page, IMPORT_ENTRY);
  await expect(page.getByRole("dialog", { name: IMPORT_DIALOG, exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await openAddEntry(page, THEOREM_ENTRY);
  await expect(page.getByRole("dialog", { name: THEOREM_DIALOG, exact: true })).toBeVisible();
});

test("a claim's context menu holds its lemma and premise work, and says when it is unavailable", async ({ page }) => {
  await page.goto("/#session=fixture");
  await mapClaim(page, FIXTURE_CLAIM_TITLE).click({ button: "right" });
  const menu = page.getByRole("menu", { name: `${FIXTURE_CLAIM_TITLE} actions` });
  // The fixture claim has no confirmed Lean statement or premises; opening them is covered in App.test.tsx.
  await expect(menu.getByRole("menuitem", { name: /^Local lemmas/ })).toContainText(
    "Local lemmas need a confirmed Lean statement for this claim.",
  );
  await expect(menu.getByRole("menuitem", { name: /^Premises/ })).toContainText(
    "This claim has no premises to review.",
  );
});

test("an empty map offers a drop area that reads a dropped file into the import", async ({ page }) => {
  await emptyProject(page);
  const area = page.getByRole("region", { name: /This project is empty/ });
  await expect(area).toBeVisible();
  await expect(area.getByRole("button", { name: "Choose file…" })).toBeVisible();
  await area.getByRole("button", { name: "New theorem…" }).click();
  await expect(page.getByRole("dialog", { name: THEOREM_DIALOG, exact: true })).toBeVisible();
  await page.keyboard.press("Escape");

  await dropFile(page, "group-notes.md", NOTES, "text/markdown");
  const dialog = page.getByRole("dialog", { name: IMPORT_DIALOG, exact: true });
  await expect(dialog.getByLabel("Research notes or Lean text")).toHaveValue(NOTES);
  await expect(dialog.getByLabel("Title", { exact: true })).toHaveValue("group-notes");
  await expect(page.getByText("Drop to import", { exact: true })).toBeHidden();
});

test("Choose file reads the chosen file into the import", async ({ page }) => {
  await emptyProject(page);
  await page
    .locator(".map-drop-input")
    .setInputFiles({ name: "basis.tex", mimeType: "text/x-tex", buffer: Buffer.from("\\begin{lemma}A.\\end{lemma}") });
  const dialog = page.getByRole("dialog", { name: IMPORT_DIALOG, exact: true });
  await expect(dialog.getByLabel("Research notes or Lean text")).toHaveValue("\\begin{lemma}A.\\end{lemma}");
  await expect(dialog.getByLabel("Title", { exact: true })).toHaveValue("basis");
});

test("a file dropped on a map with claims opens the import, and unreadable files say why in place", async ({
  page,
}) => {
  await page.goto("/#session=fixture");
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toBeVisible();
  await expect(page.getByRole("region", { name: /This project is empty/ })).toHaveCount(0);
  await dropFile(page, "Basis.lean", "theorem t : True := trivial\n", "");
  const dialog = page.getByRole("dialog", { name: IMPORT_DIALOG, exact: true });
  await expect(dialog.getByLabel("Research notes or Lean text")).toHaveValue("theorem t : True := trivial\n");
  await page.keyboard.press("Escape");

  await dropFile(page, "paper.pdf", [37, 80, 68, 70, 0, 1, 2], "application/pdf");
  await expect(dialog.getByRole("alert")).toHaveText(
    "This file type cannot be read yet. Use a text, Markdown, TeX or Lean file.",
  );
  await expect(dialog.getByLabel("Research notes or Lean text")).toHaveValue("");
  await page.keyboard.press("Escape");

  await dropFile(page, "long.md", "x".repeat(19_000), "text/markdown");
  await expect(dialog.getByRole("alert")).toHaveText("Use up to 18 KB of research text per request.");
});
