// SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";
import { openProof } from "./project-map";

const statuses = ["Verified", "Needs review", "Blocked / recheck", "To prove", "Planned", "Running", "Queued"];

for (const { width, height, inspectorOpen } of [
  { width: 1440, height: 900, inspectorOpen: true },
  { width: 1024, height: 768, inspectorOpen: false },
]) {
  test(`proof legend labels fit at ${width}×${height} with the inspector ${inspectorOpen ? "open" : "closed"}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/#session=fixture");
    await openProof(page);
    const inspector = page.locator("#review");
    if (inspectorOpen) await expect(inspector).toBeVisible();
    else await inspector.getByRole("button", { name: "Collapse review column" }).click();
    const counts = page.getByRole("status", { name: "Proof status counts" });
    await expect(counts).toBeVisible();
    for (const status of statuses) {
      const label = counts.getByText(status, { exact: true });
      await expect(label).toHaveCSS("clip-path", "none");
      await expect(label).toBeInViewport({ ratio: 0.99 });
    }
    await expect(page.getByRole("button", { name: "Proof status counts" })).toHaveCount(0);
    const stage = page.locator(".main-proof-graph");
    await expect(stage.getByRole("button", { name: "Fit to window", exact: true })).toBeInViewport();
  });
}

test("the compact legend opens named counts for the keyboard and expands when the labels fit", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await page.goto("/#session=fixture");
  await openProof(page);
  await page.locator("#review").getByRole("button", { name: "Collapse review column" }).click();
  const graph = page.locator(".main-proof-graph");
  const trigger = graph.getByRole("button", { name: "Proof status counts" });
  const status = graph.getByRole("status", { name: "Proof status counts" });
  await expect(trigger).toHaveText("Status");
  await expect(status).toHaveCount(0);

  await trigger.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Proof status counts" });
  await expect(dialog).toBeVisible();
  for (const name of [
    "Verified 1",
    "Awaiting your review 1",
    "Blocked / needs recheck 1",
    "To prove 1",
    "Planned 1",
    "Running 2",
    "Queued 1",
  ]) {
    await expect(dialog.getByRole("listitem", { name, exact: true })).toBeVisible();
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(graph).toBeVisible();

  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(trigger).toHaveCount(0);
  await expect(status.getByText("Needs review", { exact: true })).toBeInViewport();
});
