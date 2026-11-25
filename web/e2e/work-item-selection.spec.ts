import { expect, test, type Page } from "@playwright/test";
import type { AgentWorkSummary } from "../src/api/agentWork";
import { openDestination } from "./navigation";

const claimIds = [
  "11111111-1111-4111-8111-111111111111",
  "aaaaaaaa-0000-4000-8000-000000000001",
  "aaaaaaaa-0000-4000-8000-000000000002",
];
const taskIds = [
  "11000000-0000-4000-8000-000000000040",
  "11000000-0000-4000-8000-000000000041",
  "11000000-0000-4000-8000-000000000042",
];
const recordedAt = "2026-08-04T12:00:00Z";

async function openSection(page: Page, section: string) {
  await page.goto("/#session=fixture");
  await openDestination(page, section === "Agent work" ? "Agent tasks" : (section as "Claims"));
}

async function setLayout(page: Page, layout: "List" | "Table" | "Board" | "Status board") {
  await page.getByRole("button", { name: "Display", exact: true }).click();
  await page.getByRole("menuitemradio", { name: layout, exact: true }).click();
}

async function captureClipboard(page: Page, denyFirstWrite = false) {
  await page.addInitScript((denyFirstWrite) => {
    let value = "";
    let blocked = denyFirstWrite;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (text: string) => {
          if (blocked) {
            blocked = false;
            throw new Error("Denied");
          }
          value = text;
        },
        readText: async () => value,
      },
    });
  }, denyFirstWrite);
  return () => page.evaluate(() => navigator.clipboard.readText());
}

test("Claims selects a range in visible order, copies IDs and resets when the layout or filter changes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 640, height: 460 });
  const clipboard = await captureClipboard(page);
  await openSection(page, "Claims");
  await setLayout(page, "List");
  const view = page.getByRole("region", { name: "Select items in All claims" });
  const rows = view.getByRole("checkbox");
  const actions = view.getByRole("group", { name: "Selection actions" });
  await expect(rows).toHaveCount(5);
  await rows.nth(2).check();
  await rows.first().click({ modifiers: ["Shift"] });
  await expect(actions.getByText("3 selected", { exact: true })).toBeVisible();
  await expect(rows.nth(1)).toBeChecked();
  await expect(rows.nth(3)).not.toBeChecked();
  await actions.getByRole("button", { name: "Copy record IDs" }).click();
  await expect.poll(clipboard).toBe(claimIds.join("\n"));
  await expect(actions.getByText("Record IDs copied")).toBeVisible();
  await rows.first().press("ControlOrMeta+a");
  await expect(actions.getByText("5 selected", { exact: true })).toBeVisible();
  await rows.first().press("Escape");
  await expect(actions).toHaveCount(0);
  await expect(rows.first()).toBeFocused();
  await rows.first().check();
  await setLayout(page, "Table");
  await expect(view.getByRole("table", { name: "All claims" })).toBeVisible();
  await expect(actions).toHaveCount(0);
  await rows.first().check();
  await expect(actions.getByText("1 selected", { exact: true })).toBeVisible();
  await page.locator(".section-nav").getByRole("link", { name: /^flow/ }).click();
  const filtered = page.getByRole("region", { name: "Select items in flow" });
  const filteredRows = filtered.getByRole("checkbox");
  await expect(filteredRows).toHaveCount(1);
  await expect(filteredRows.first()).not.toBeChecked();
  await filteredRows.first().press("Control+a");
  await expect(filtered.getByText("1 selected", { exact: true })).toBeVisible();
  await filtered.getByRole("button", { name: "Copy record IDs" }).click();
  await expect.poll(clipboard).toBe("aaaaaaaa-0000-4000-8000-000000000003");
  await setLayout(page, "Status board");
  await expect(page.locator(".board article")).toHaveCount(1);
  await expect(page.getByRole("checkbox")).toHaveCount(0);
});

test("Agent work table selection allows clipboard retry and leaves task records untouched", async ({ page }) => {
  const clipboard = await captureClipboard(page, true);
  const tasks: AgentWorkSummary[] = taskIds.map((id, index) => ({
    id,
    title: `Recorded task ${index + 1}`,
    status: index === 1 ? "delivered" : "queued",
    agent_name: "Proof Agent",
    mode: "review",
    created_at: recordedAt,
    updated_at: recordedAt,
    delivery_sha256: null,
    predecessor_id: null,
    error: null,
  }));
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && new URL(request.url()).pathname.startsWith("/api/agent-work"))
      writes.push(request.method());
  });
  await page.route("**/api/agent-work", (route) => route.fulfill({ json: { tasks, available: true } }));
  await openSection(page, "Agent work");
  await setLayout(page, "Table");
  const view = page.getByRole("region", { name: "Select items in All tasks" });
  const table = view.getByRole("table", { name: "All tasks" });
  const rows = table.getByRole("checkbox");
  await expect(rows).toHaveCount(3);
  await rows.first().check();
  await rows.last().click({ modifiers: ["Shift"] });
  await expect(view.getByText("3 selected", { exact: true })).toBeVisible();
  const copy = view.getByRole("button", { name: "Copy record IDs" });
  await copy.click();
  await expect(view.getByText("Clipboard unavailable. Try copying again.")).toBeVisible();
  await expect.poll(clipboard).toBe("");
  await copy.click();
  await expect.poll(clipboard).toBe(taskIds.join("\n"));
  await view.getByRole("button", { name: "Clear selection" }).click();
  await expect(view).toBeFocused();
  await expect(copy).toHaveCount(0);
  await view.press("Meta+a");
  await expect(view.getByText("3 selected", { exact: true })).toBeVisible();
  await copy.press("Escape");
  await expect(view).toBeFocused();
  await expect(copy).toHaveCount(0);
  await table.getByRole("checkbox", { name: "Select Recorded task 2" }).press("Space");
  await expect(view.getByText("1 selected", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/#agent-work$/);
  expect(writes).toEqual([]);
});
