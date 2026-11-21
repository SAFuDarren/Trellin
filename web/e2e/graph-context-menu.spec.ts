import { expect, test, type Locator, type Page } from "@playwright/test";
import type { GraphSnapshot } from "../src/workbench/types";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";

const CLAIM_ID = "11111111-1111-4111-8111-111111111111";
const CLIPPED_CLAIM_TITLE = "连续自然数之积的奇偶性";
const GOAL_REFERENCE = "ZADD-01";
const NODE_REFERENCE = "ZADD-02";
const GOAL_LEAN = "∀ n : Nat, n = 0 + n";
const NODE_LEAN = "∀ n : Nat, n + 0 = n";
const OBLIGATION = "Proof obligation 2";

const actionsFor = (page: Page, title: string) => page.getByRole("menu", { name: `${title} actions`, exact: true });
const action = (menu: Locator, label: string) => menu.getByRole("menuitem", { name: label, exact: true });
const camera = (plot: Locator) => plot.evaluate((element) => getComputedStyle(element).transform);

async function withRecordedReferences(page: Page) {
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const snapshot = (await response.json()) as GraphSnapshot;
    if (snapshot.claim.id === CLAIM_ID) {
      snapshot.claim.code = "ZADD";
      snapshot.nodes.find((node) => node.kind === "goal")!.code = GOAL_REFERENCE;
      snapshot.nodes.find((node) => node.title === OBLIGATION)!.code = NODE_REFERENCE;
    }
    await route.fulfill({ response, json: snapshot });
  });
}

async function captureClipboard(page: Page) {
  // Keep the assertion local to the fixture browser instead of touching the Mac pasteboard.
  await page.addInitScript(() => {
    let value = "";
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (text: string) => {
          value = text;
        },
        readText: async () => value,
      },
    });
  });
  return () => page.evaluate(() => navigator.clipboard.readText());
}

async function panCardToRightEdge(page: Page, stage: Locator, card: Locator) {
  await expect(card).toBeVisible();
  const stageBounds = (await stage.boundingBox())!;
  const cardBounds = (await card.boundingBox())!;
  const rightEdge = stageBounds.x + stageBounds.width;
  const left = rightEdge - cardBounds.width / 2;
  await page.mouse.move(stageBounds.x + stageBounds.width / 2, stageBounds.y + 32);
  await page.mouse.wheel(Math.round(cardBounds.x - left), 0);
  await expect.poll(async () => Math.abs(((await card.boundingBox())?.x ?? Number.NaN) - left)).toBeLessThan(2);
  return { x: rightEdge - Math.min(16, cardBounds.width / 4), y: cardBounds.y + cardBounds.height / 2 };
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("right-click and keyboard open the claim menu without moving the camera or completing a pending link", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1150, height: 850 });
  await page.goto("/#session=fixture");
  const source = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const target = mapClaim(page, CLIPPED_CLAIM_TITLE);
  const stage = page.getByRole("application", { name: "Project map", exact: true });
  const plot = stage.locator(".project-canvas-plot");

  await source.click();
  await expect(page.locator("#review")).toBeVisible();
  await page.getByRole("button", { name: "Link selected claim", exact: true }).click();
  await expect(source).toHaveAttribute("data-link-source", "true");
  const point = await panCardToRightEdge(page, stage, target);
  const originalCamera = await camera(plot);
  await page.mouse.click(point.x, point.y, { button: "right" });

  const menu = actionsFor(page, CLIPPED_CLAIM_TITLE);
  await expect(menu).toBeVisible();
  await expect(target).toHaveAttribute("aria-pressed", "true");
  await expect(source).toHaveAttribute("data-link-source", "true");
  await expect(action(menu, "Cancel link")).toBeVisible();
  expect(await camera(plot)).toBe(originalCamera);
  await expect(stage.locator("[data-research-link]")).toHaveCount(0);
  expect((await (await request.get("/api/project-canvas")).json()).links).toEqual([]);

  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(target).toBeFocused();
  await expect(stage).toHaveAttribute("data-linking", "true");

  await target.press("ContextMenu");
  await expect(menu).toBeVisible();
  expect(await camera(plot)).toBe(originalCamera);
  await action(menu, "Cancel link").press("Enter");
  await expect(menu).toBeHidden();
  await expect(stage).not.toHaveAttribute("data-linking");
  await expect(page.getByRole("button", { name: "Link selected claim", exact: true })).toBeEnabled();
});

test("the claim menu opens existing proof tools, copies exact text and shows recorded Library sources", async ({
  page,
}) => {
  await withRecordedReferences(page);
  const clipboard = await captureClipboard(page);
  await page.goto("/#session=fixture");
  const claim = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const menu = actionsFor(page, FIXTURE_CLAIM_TITLE);
  const review = page.locator("#review");

  await claim.click({ button: "right" });
  await expect(menu).toBeVisible();
  await expect(review).toBeHidden();
  await action(menu, "Copy reference").click();
  await expect.poll(clipboard).toBe(GOAL_REFERENCE);
  await expect(page.locator(".workbench-action-feedback")).toHaveText(/Copied to clipboard/);

  await claim.press("Shift+F10");
  const copyLean = action(menu, "Copy Lean statement");
  await expect(copyLean.locator("svg.workbench-icon")).toBeVisible();
  await expect(copyLean).toHaveAttribute("aria-keyshortcuts", "Alt+L");
  const mac = await page.evaluate(() => navigator.platform.startsWith("Mac"));
  await expect(copyLean.locator("kbd")).toHaveText(mac ? "⌥L" : "Alt+L");
  await action(menu, "Open proof").press("Alt+l");
  await expect.poll(clipboard).toBe(GOAL_LEAN);

  await claim.press("Shift+F10");
  await action(menu, "Open proof").click();
  await expect(page.locator(".main-proof-graph .graph-node").first()).toBeVisible();
  await expect(review.locator(".graph-node")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("application", { name: "Project map", exact: true })).toBeVisible();

  await claim.press("Shift+F10");
  const proofAction = action(menu, "Proof task");
  await expect(proofAction).toHaveAttribute("aria-keyshortcuts", "Alt+T");
  const proofIcon = await proofAction.locator("svg path").getAttribute("d");
  await action(menu, "Open proof").press("Alt+t");
  const proofTask = page.getByRole("dialog", { name: "Proof task", exact: true });
  await expect(proofTask).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(proofTask).toBeHidden();
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Command palette", exact: true });
  const paletteProof = palette.getByRole("option", { name: "Proof task", exact: true });
  await expect(paletteProof).toHaveAttribute("aria-keyshortcuts", "Alt+T");
  await expect(paletteProof.locator("svg path")).toHaveAttribute("d", proofIcon!);
  await page.keyboard.press("Escape");
  await expect(palette).toBeHidden();

  await claim.press("Shift+F10");
  await action(menu, "Ask an Agent").click();
  const dock = page.getByRole("region", { name: "Agent chat", exact: true });
  await expect(dock.getByText(`Context · ${FIXTURE_CLAIM_TITLE}`, { exact: true })).toBeVisible();

  await claim.press("Shift+F10");
  await action(menu, "Show in Library").click();
  await expect(page).toHaveURL(new RegExp(`#library/claim%3A${CLAIM_ID}$`));
  await expect(page.getByRole("link", { name: /^Sources used by this claim/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("row", { name: /Nat\.add_zero/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /Nat\.mul_comm/ })).toHaveCount(0);
});

test("the proof node menu keeps camera and focus, copies exact node text, and opens a note without sending it", async ({
  page,
}) => {
  await withRecordedReferences(page);
  const clipboard = await captureClipboard(page);
  const guidancePosts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/guidance") {
      guidancePosts.push(request.postData() ?? "");
    }
  });
  await page.goto("/#session=fixture");
  await openProof(page);
  const stage = page.locator(".main-proof-graph .stage");
  const plot = stage.locator(".plot");
  const node = stage.getByRole("button", { name: /^Proof obligation 2,/ });
  const menu = actionsFor(page, OBLIGATION);
  await expect(node).toBeVisible();
  const point = await panCardToRightEdge(page, stage, node);
  const originalCamera = await camera(plot);
  await page.mouse.click(point.x, point.y, { button: "right" });
  await expect(menu).toBeVisible();
  await expect(node).toHaveAttribute("aria-pressed", "true");
  expect(await camera(plot)).toBe(originalCamera);
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(node).toBeFocused();

  await node.press("Shift+F10");
  await action(menu, "Open details").click();
  await expect(page.locator("#node-detail")).toBeFocused();

  await node.press("Shift+F10");
  await action(menu, "Copy reference").click();
  await expect.poll(clipboard).toBe(NODE_REFERENCE);
  await node.press("Shift+F10");
  await action(menu, "Copy Lean statement").click();
  await expect.poll(clipboard).toBe(NODE_LEAN);

  await node.press("Shift+F10");
  await action(menu, "Write proof note").click();
  const note = page.locator("#guidance-text");
  await expect(note).toBeFocused();
  await expect(page.getByRole("region", { name: "Proof activity", exact: true }).getByRole("heading")).toHaveText(
    /Proof obligation 2/,
  );
  await expect(note).toHaveValue("");
  expect(guidancePosts).toEqual([]);
});

test("a missing saved reference explains why copying is unavailable and does not activate", async ({ page }) => {
  await withRecordedReferences(page);
  const clipboard = await captureClipboard(page);
  await page.goto("/#session=fixture");
  await openProof(page);
  const node = page.locator(".main-proof-graph").getByRole("button", { name: /^Proof obligation 1,/ });
  await node.press("Shift+F10");
  const menu = actionsFor(page, "Proof obligation 1");
  const copy = action(menu, "Copy reference");
  await expect(copy).toBeDisabled();
  await expect(copy).toHaveAccessibleDescription(/saved reference/i);
  await expect(copy).not.toHaveAttribute("aria-keyshortcuts", "Alt+C");
  await menu.press("Alt+c");
  await copy.press("Enter");
  await expect(menu).toBeVisible();
  expect(await clipboard()).toBe("");
});
