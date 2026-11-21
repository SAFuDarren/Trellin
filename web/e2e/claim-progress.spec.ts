import { expect, test, type Locator, type Page } from "@playwright/test";
import type { Claim } from "../src/api/client";
import type { ProjectClaim } from "../src/api/projectCanvas";
import type { InboxItem, Job } from "../src/api/sections";
import type { AtlasClaim, GraphSnapshot } from "../src/workbench/types";
import { mapClaim } from "./project-map";
import { mockWorkspaceEvents } from "./workspace-events";

const WORKSPACE = "11111111-2222-4333-8444-555555555555";
const WAITING_SINCE = "2026-11-21T00:00:00Z";
const PARTIAL = "1 of 2 obligations verified";
const ZERO = "No recorded obligations";
const ZERO_HINT = "Recorded proof obligations and their verification progress will appear here.";
const OLDER = "2 of 7 obligations verified";
const UPDATED = "2 of 2 obligations verified";
const LONG_TITLE =
  "A long theorem about the behaviour of composed maps between finite ordered sets under repeated restriction and extension";

function claim(index: number, title: string, status = "open"): Claim {
  return { id: `33333333-3333-4333-8333-33333333333${index}`, title, status, domain: "lean-core" };
}

function withoutTotals({ id, title, status, domain }: ProjectClaim): Claim {
  return { id, title, status, domain };
}

const partial = { verified: 1, total: 2 };
const review = { ...claim(0, LONG_TITLE), obligations: partial };
const decision = { ...claim(1, "Choose a premise before proving this claim"), obligations: partial };
const verification = { ...claim(2, "Check the candidate proof in Lean"), obligations: partial };
const recheck = {
  ...claim(3, "Revisit an older proof with a withdrawn premise", "needs_recheck"),
  obligations: { verified: 2, total: 7 },
};
const ready = { ...claim(4, "A frozen statement and confirmed basis"), obligations: { verified: 0, total: 0 } };
const missing = claim(5, "A claim from a build without exact obligation totals");
const claims: ProjectClaim[] = [review, decision, verification, recheck, ready, missing];

function reviewItem(target: Claim, decisionRequired = false): InboxItem {
  return {
    kind: decisionRequired ? "resolve_clarification" : "review_evidence",
    item_id: `55555555-5555-4555-8555-55555555555${target.id.at(-1)}`,
    claim_id: target.id,
    claim_title: target.title,
    waiting_since: WAITING_SINCE,
    question: decisionRequired ? "Which premise should be confirmed?" : "Review this verified proof.",
    detail: decisionRequired ? { question_count: 1 } : { kind: "lean" },
    priority: 1,
  };
}

async function fixture(page: Page, displayedClaims = claims, live = false, extraReviews: InboxItem[] = []) {
  let verificationComplete = false;
  const proofTaskReads: string[] = [];
  function atlasClaim(target: Claim): AtlasClaim {
    const goal = { id: `goal:${target.id}`, kind: "goal", status: target.status ?? "open", title: target.title ?? "" };
    const nodes = [goal];
    const edges: [string, string][] = [];
    if (target.id !== ready.id) {
      const lemma = { id: `local-lemma:${target.id}`, kind: "local_lemma", status: "verified", title: "Checked lemma" };
      const obligation = {
        id: `obligation:${target.id}:1`,
        kind: "obligation",
        status: "verified",
        title: "Shared obligation",
      };
      nodes.push(lemma, obligation, {
        id: `item:${target.id}`,
        kind: "hypothesis",
        status: "approved",
        title: "Confirmed assumption",
      });
      nodes.push({ id: `library:${target.id}`, kind: "library", status: "available", title: "Library premise" });
      // The graph also shows the verified lemma and evidence discharging the first position.
      const openObligation = {
        ...obligation,
        id: `obligation:${target.id}:2`,
        status: "open",
        title: "Outstanding obligation",
      };
      const otherLemma = { ...lemma, id: `local-lemma:${target.id}:2`, title: "Alternate checked lemma" };
      nodes.push(openObligation, otherLemma);
      nodes.push({ id: `lean:${target.id}`, kind: "evidence", status: "verified", title: "Recorded evidence" });
      edges.push(
        [lemma.id, goal.id],
        [obligation.id, lemma.id],
        [otherLemma.id, goal.id],
        [openObligation.id, otherLemma.id],
      );
    }
    return {
      ...withoutTotals(target),
      nodes,
      edges,
      awaiting_review: Number(target.id === review.id || (target.id === verification.id && verificationComplete)),
      blocked: Number(target.id === decision.id),
    };
  }

  await page.route("**/api/claims", (route) =>
    route.fulfill({ json: { claims: displayedClaims.map(withoutTotals), claim_id: null } }),
  );
  await page.route("**/api/project-canvas/claims", (route) =>
    route.fulfill({
      json: {
        claims: displayedClaims.map((target) =>
          target.id === verification.id && verificationComplete
            ? { ...target, obligations: { verified: 2, total: 2 } }
            : target,
        ),
        claim_id: null,
      },
    }),
  );
  await page.route("**/api/project-canvas", (route) => route.fulfill({ json: { positions: {}, links: [] } }));
  // The project index contains older claims beyond the recent Atlas window.
  await page.route("**/api/atlas", (route) =>
    route.fulfill({ json: { claims: displayedClaims.filter((target) => target.id !== recheck.id).map(atlasClaim) } }),
  );
  await page.route("**/api/graph?*", (route) => {
    const claimId = new URL(route.request().url()).searchParams.get("claim");
    const target = displayedClaims.find((item) => item.id === claimId)!;
    const graph = atlasClaim(target);
    const snapshot: GraphSnapshot = {
      claim: withoutTotals(target),
      focus_id: graph.nodes[0]?.id,
      nodes: [
        ...graph.nodes,
        ...extraReviews
          .filter((item) => item.claim_id === target.id)
          .map((item) => ({
            id: `lean:${item.item_id}`,
            kind: "evidence",
            title: String(item.detail?.title ?? ""),
            citation: String(item.detail?.title ?? ""),
            status: "awaiting_review",
            reference: { evidence_id: item.item_id },
          })),
      ],
      edges: graph.edges.map(([source, target]) => ({ source, target, kind: "actual" })),
    };
    return route.fulfill({ json: snapshot });
  });
  await page.route("**/api/inbox", (route) =>
    route.fulfill({
      json: {
        items: [
          ...displayedClaims.flatMap((target) =>
            target.id === review.id || (target.id === verification.id && verificationComplete)
              ? [reviewItem(target)]
              : target.id === decision.id
                ? [reviewItem(target, true)]
                : [],
          ),
          ...extraReviews,
        ],
        ...(live
          ? {
              workspace_events: {
                workspace_id: WORKSPACE,
                cursor: verificationComplete ? "2" : "1",
                coverage: "workspace-writes-v1",
              },
            }
          : {}),
      },
    }),
  );
  await page.route("**/api/team", async (route) => {
    const response = await route.fetch();
    const team = await response.json();
    const checking: Job = {
      id: "44444444-4444-4444-8444-444444444444",
      claim_id: verification.id,
      claim_title: verification.title,
      kind: "verify",
      state: "running",
      provider: null,
      model: null,
      model_job: false,
      target: null,
      node_id: null,
      enqueued_at: WAITING_SINCE,
      submitted_at: WAITING_SINCE,
      running_since: WAITING_SINCE,
      execution_ms: null,
      failure_code: null,
    };
    const jobs =
      !verificationComplete && displayedClaims.some((target) => target.id === verification.id) ? [checking] : [];
    await route.fulfill({ response, json: { ...team, jobs } });
  });
  await page.route("**/api/progress", (route) => route.fulfill({ json: { policies: [] } }));
  await page.route("**/api/proof-task?*", (route) => {
    const claimId = new URL(route.request().url()).searchParams.get("claim");
    const target = displayedClaims.find((item) => item.id === claimId)!;
    proofTaskReads.push(target.id);
    return route.fulfill({
      json: {
        claim_id: target.id,
        title: target.title,
        state: target.id === ready.id ? "ready" : "blocked",
        can_start: target.id === ready.id,
        can_stop: false,
        blocker: null,
        local_model_allowed: false,
        scope: { token: null, proposition: null, basis: [], omitted_basis: 0 },
        decisions: [],
        policy: null,
        receipt: null,
        adopted: false,
      },
    });
  });
  return { proofTaskReads, finishVerification: () => (verificationComplete = true) };
}

async function expectProgress(surface: Locator, label: string, tone: string, count: string | null = PARTIAL) {
  const state = surface.locator(".claim-progress-state");
  await expect(state).toHaveText(label);
  await expect(state).toHaveAttribute("data-tone", tone);
  const renderedCount = surface.locator(".claim-progress-count");
  const empty = surface.locator(".claim-progress-empty");
  if (count === ZERO) {
    await expect(empty).toBeVisible();
    await expect(empty).toHaveText(ZERO);
    await expect(empty).toHaveAttribute("title", ZERO_HINT);
  } else {
    await expect(empty).toHaveCount(0);
  }
  if (count === null || count === ZERO) await expect(renderedCount).toHaveCount(0);
  else await expect(renderedCount).toHaveText(count);
}

test("exact obligation totals and waiting states agree on the map, inspector and Claims page", async ({ page }) => {
  const { proofTaskReads } = await fixture(page);
  await page.goto("/#session=fixture");
  const atlasToggle = page.getByRole("button", { name: "Global map", exact: true });
  await atlasToggle.click();
  await expect(page.getByRole("img", { name: /^Recent claims in the workspace/ })).toBeVisible();
  await atlasToggle.click();
  const states = [
    [review, "Waiting for your review", "review", PARTIAL],
    [decision, "Needs a decision", "bad", PARTIAL],
    [verification, "Verification running", "live", PARTIAL],
    [recheck, "Needs recheck", "bad", OLDER],
    [missing, "In progress", "open", null],
  ] as const;
  for (const [target, label, tone, count] of states) {
    await expectProgress(mapClaim(page, target.title!), label, tone, count);
  }
  // The map initially selects its first claim. It needs no per-card task requests.
  expect(proofTaskReads.filter((id) => id !== review.id)).toEqual([]);

  await mapClaim(page, review.title!).click();
  const detail = page.locator("#node-detail");
  await expect(detail.getByRole("heading", { name: review.title!, exact: true })).toBeVisible();
  await expect(detail.locator(".claim-progress-count")).toHaveText(PARTIAL);
  await expect(detail.getByRole("link", { name: "1 result to review", exact: true })).toBeVisible();
  const detailHeader = detail.locator(".detail-header > *");
  await expect(detailHeader.nth(0)).toHaveText(review.title!);
  await expect(detailHeader.nth(1)).toContainText("1 result to review");
  await expect(detailHeader.nth(2).getByRole("button", { name: "Proof task", exact: true })).toBeVisible();
  await expect(detail.locator(".claim-progress-state")).toHaveCount(0);

  await mapClaim(page, recheck.title!).press("Space");
  await expectProgress(detail, "Needs recheck", "bad", OLDER);
  await mapClaim(page, missing.title!).press("Space");
  await expectProgress(detail, "In progress", "open", null);

  const readyCard = mapClaim(page, ready.title!);
  await readyCard.press("Space");
  await expectProgress(readyCard, "Ready to start", "open", ZERO);
  await expect(readyCard).toHaveAccessibleDescription(/No recorded obligations/);
  await expectProgress(detail, "Ready to start", "open", ZERO);

  await page.locator("nav.rail").getByRole("link", { name: "Claims", exact: true }).click();
  for (const [target, label, tone, count] of states.slice(1, 3)) {
    const card = page.locator(".board-card").filter({ hasText: target.title! });
    await expectProgress(card, label, tone, count);
  }
  const recheckColumn = page.getByRole("region", { name: "Needs recheck", exact: true });
  await expect(recheckColumn.getByText("Needs recheck", { exact: true })).toHaveCount(1);
  const recheckCard = recheckColumn.locator(".board-card").filter({ hasText: recheck.title! });
  await expect(recheckCard.locator(".claim-progress-state")).toHaveCount(0);
  await expect(recheckCard.locator(".claim-progress-count")).toHaveText(OLDER);
  const uncounted = page.locator(".board-card").filter({ hasText: missing.title! });
  await expect(uncounted.locator(".claim-progress")).toHaveCount(0);
  const reviewCard = page.locator(".board-card").filter({ hasText: review.title! });
  await expect(reviewCard.locator(".claim-progress-state")).toHaveCount(0);
  await expect(reviewCard.locator(".claim-progress-count")).toHaveText(PARTIAL);
  await expect(reviewCard.getByRole("link", { name: "1 result to review", exact: true })).toHaveAttribute(
    "href",
    `#review/${reviewItem(review).item_id}`,
  );
  await expectProgress(page.locator(".board-card").filter({ hasText: ready.title! }), "Ready to start", "open", ZERO);
});

test("a long title leaves status and proof progress legible at the minimum desktop size", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 460 });
  await fixture(page, [review]);
  await page.goto("/#session=fixture");
  const card = mapClaim(page, LONG_TITLE);
  const title = card.locator(".project-canvas-title");
  const state = card.locator(".claim-progress-state");
  const count = card.locator(".claim-progress-count");
  await expect(card).toBeInViewport({ ratio: 0.99 });
  await expect(title).toHaveText(LONG_TITLE);
  await expectProgress(card, "Waiting for your review", "review");
  await expect(state).toBeInViewport({ ratio: 0.99 });
  await expect(count).toBeInViewport({ ratio: 0.99 });
  const [cardBox, titleBox, stateBox, countBox] = await Promise.all(
    [card, title, state, count].map((element) => element.boundingBox()),
  );
  expect(titleBox!.y + titleBox!.height).toBeLessThanOrEqual(stateBox!.y + 1);
  expect(stateBox!.y + stateBox!.height).toBeLessThanOrEqual(countBox!.y + 1);
  expect(countBox!.y + countBox!.height).toBeLessThanOrEqual(cardBox!.y + cardBox!.height + 1);
  for (const text of [state, count]) {
    expect(await text.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  }
});

test("a committed workspace event updates proof progress on the open canvas and inspector", async ({ page }) => {
  const { finishVerification } = await fixture(page, [verification], true);
  const emitEvent = await mockWorkspaceEvents(page, WORKSPACE);
  await page.goto("/#session=fixture");
  const card = mapClaim(page, verification.title!);
  await expectProgress(card, "Verification running", "live");
  await card.click();
  const detail = page.locator("#node-detail");
  await expectProgress(detail, "Verification running", "live");

  const event = {
    id: "2",
    type: "workspace.changed",
    workspace_id: WORKSPACE,
    schema_version: 1,
    occurred_at: WAITING_SINCE,
    actor: { kind: "system", id: null },
    subject: { kind: "workspace", id: WORKSPACE },
    data: { area: "proof", claim_id: verification.id },
  };
  let reloads = 0;
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) reloads++;
  });
  finishVerification();
  await emitEvent(event);
  await expectProgress(card, "Waiting for your review", "review", UPDATED);
  await expect(detail.locator(".claim-progress-count")).toHaveText(UPDATED);
  await expect(detail.getByRole("link", { name: "1 result to review", exact: true })).toBeVisible();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  expect(reloads).toBe(0);
});

test("a proved claim keeps its status and opens either recorded result by identity", async ({ page }) => {
  const proved: ProjectClaim = {
    ...claim(6, "A result already proved", "proved"),
    obligations: { verified: 0, total: 0 },
  };
  const first = {
    ...reviewItem(proved),
    detail: { kind: "lean", title: "Alternative verified route" },
  };
  const second = {
    ...reviewItem(proved),
    item_id: "55555555-5555-4555-8555-555555555559",
    detail: { kind: "lean", title: "Independent verified evidence" },
  };
  await fixture(page, [proved, ready, review], false, [first, second]);
  await page.goto("/#session=fixture");

  const map = mapClaim(page, proved.title!);
  await expectProgress(map, "Proved", "ok", null);
  await map.click();
  const detail = page.locator("#node-detail");
  await expectProgress(detail, "Proved", "ok", null);
  await expect(detail.getByRole("button", { name: "2 results to review", exact: true })).toBeVisible();

  await page.locator("nav.rail").getByRole("link", { name: "Claims", exact: true }).click();
  const provedColumn = page.getByRole("region", { name: "Proved", exact: true });
  const provedCard = provedColumn.locator(".board-card").filter({ hasText: proved.title! });
  await expect(provedCard.locator(".claim-progress-empty")).toHaveCount(0);
  await expect(provedCard.locator(".progress")).toHaveCount(0);
  await provedCard.getByRole("button", { name: "2 results to review", exact: true }).press("Enter");
  const menu = page.getByRole("menu", { name: "2 results to review", exact: true });
  await expect(menu.getByRole("menuitem", { name: first.detail.title, exact: true })).toHaveAttribute(
    "href",
    `#review/${first.item_id}`,
  );
  const exact = menu.getByRole("menuitem", { name: second.detail.title, exact: true });
  await expect(exact).toHaveAttribute("href", `#review/${second.item_id}`);
  await exact.click();
  await expect(page).toHaveURL(new RegExp(`#review/${second.item_id}$`));
  await expect(page.locator(".page-head h2")).toHaveText(second.detail.title);
  await expect(page.locator(".page-head h2")).not.toHaveText(first.detail.title);

  await page.evaluate(() => {
    window.location.hash = "#review/55555555-5555-4555-8555-555555555558";
  });
  await expect(page.getByText("This item is no longer waiting for review.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);
});
