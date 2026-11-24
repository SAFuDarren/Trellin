import { expect, test, type Page } from "@playwright/test";

// #328: a project database with a local identity signs decisions as its researcher, so Review and
// the confirmation forms never ask for a name. Databases without an identity still ask (see
// contextual-review.spec.ts).
const EVIDENCE = "77777777-7777-4777-8777-777777777777";
const WORKSPACE = "11111111-2222-4333-8444-555555555555";
const SIGNER = "Fixture researcher";

async function withIdentity(page: Page, workspace: string | null) {
  await page.route("**/api/inbox", async (route) => {
    const response = await route.fetch();
    const inbox = await response.json();
    await route.fulfill({
      response,
      json: { ...inbox, review_identity: { workspace_id: workspace, display_name: SIGNER } },
    });
  });
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("Review records a decision for the bound researcher without asking for a name", async ({ page }) => {
  let decision: unknown = null;
  await withIdentity(page, WORKSPACE);
  await page.route(`**/api/v1/workspaces/${WORKSPACE}/evidence/${EVIDENCE}/reviews`, async (route) => {
    decision = route.request().postDataJSON();
    await route.fulfill({ status: 201, json: { id: "decision-one", user_id: "fixture-user" } });
  });
  await page.goto("/#session=fixture");
  await page.goto(`/#review/${EVIDENCE}`);
  await expect(page.getByText(`Recorded by ${SIGNER}`, { exact: true })).toBeVisible();
  await expect(page.getByLabel("Your name")).toHaveCount(0);
  await page.getByRole("radio", { name: "Approve", exact: true }).check();
  await page.getByLabel("Reason", { exact: true }).fill("The result proves the selected Lean statement.");
  await page.getByRole("button", { name: "Record decision", exact: true }).click();
  await expect(page.getByText("Approved and recorded.", { exact: true })).toBeVisible();
  expect(decision).toEqual({ decision: "approve", reason: "The result proves the selected Lean statement." });
});

test("an identity without a bound workspace signs the older review route with the researcher's name", async ({
  page,
}) => {
  await withIdentity(page, null);
  await page.goto("/#session=fixture");
  await page.goto(`/#review/${EVIDENCE}`);
  await expect(page.getByText(`Recorded by ${SIGNER}`, { exact: true })).toBeVisible();
  await expect(page.getByLabel("Your name")).toHaveCount(0);
  await page.getByRole("radio", { name: "Return", exact: true }).check();
  await page.getByLabel("Reason", { exact: true }).fill("Recheck the scope.");
  const sent = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/review"));
  await page.getByRole("button", { name: "Record decision", exact: true }).click();
  expect((await sent).postDataJSON()).toEqual({
    evidence_id: EVIDENCE,
    action: "return",
    actor: SIGNER,
    reason: "Recheck the scope.",
  });
});

test("Add theorem confirms a goal as the bound researcher", async ({ page }) => {
  let sent: Record<string, unknown> | null = null;
  await withIdentity(page, WORKSPACE);
  await page.route("**/api/theorems", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { drafts: [] } });
      return;
    }
    sent = route.request().postDataJSON();
    await route.fulfill({ status: 400, json: { error: { code: "theorem.invalid_input", message: "stop" } } });
  });
  await page.goto("/#session=fixture");
  await page.getByRole("button", { name: "Add theorem" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(`Recorded by ${SIGNER}`, { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Your name")).toHaveCount(0);
  await dialog.getByLabel("Title", { exact: true }).fill("Reflexivity");
  await dialog.getByLabel("Mathematical statement in your own words").fill("Every natural number equals itself.");
  await dialog.getByLabel("Lean proposition", { exact: true }).fill("∀ n : Nat, n = n");
  await dialog.getByLabel("Reason").fill("Checked the proposition");
  await dialog.getByLabel("The Lean proposition expresses my intended theorem, including its premises.").check();
  await dialog.getByRole("button", { name: "Confirm statement and add goal" }).click();
  await expect.poll(() => sent).not.toBeNull();
  expect(sent).toMatchObject({ kind: "goal", actor: SIGNER, reason: "Checked the proposition" });
});
