"use strict";

const byId = (id) => document.getElementById(id);
const ui = {
  workbench: byId("workbench"),
  notice: byId("notice"),
  content: byId("content"),
  claimSearch: byId("claim-search"),
  claimList: byId("claim-list"),
  workspaceState: byId("workspace-state"),
  title: byId("claim-title"),
  meta: byId("claim-meta"),
  refresh: byId("refresh"),
  tabs: byId("view-tabs"),
  impact: byId("impact-controls"),
  premise: byId("premise-select"),
  impactState: byId("impact-state"),
  stage: byId("stage"),
  plot: byId("plot"),
  edges: byId("edges"),
  nodes: byId("nodes"),
  legend: byId("legend"),
  atlas: byId("atlas"),
  atlasMap: byId("atlas-map"),
  atlasCount: byId("atlas-count"),
  atlasKeys: byId("atlas-keys"),
  zoomIn: byId("zoom-in"),
  zoomOut: byId("zoom-out"),
  zoomReset: byId("zoom-reset"),
  zoomFit: byId("zoom-fit"),
  queueTitle: byId("queue-title"),
  queueCount: byId("queue-count"),
  queue: byId("review-queue"),
  detail: byId("node-detail"),
  activity: byId("activity-panel"),
  activityTitle: byId("activity-title"),
  activityState: byId("activity-state"),
  activityFeed: byId("activity-feed"),
  chatTarget: byId("chat-target"),
  chatScope: byId("chat-scope"),
  toggleChat: byId("toggle-chat"),
  chatBody: byId("chat-body"),
  guidanceForm: byId("guidance-form"),
  guidanceText: byId("guidance-text"),
  guidanceAudience: byId("guidance-audience"),
  guidanceScope: byId("guidance-scope"),
  guidanceStatus: byId("guidance-status"),
  guidanceSend: byId("guidance-send"),
  toggleSidebar: byId("toggle-sidebar"),
  menuReview: byId("menu-review"),
  menuReviewCount: byId("menu-review-count"),
  menuLibrary: byId("menu-library"),
  menuLibraryCount: byId("menu-library-count"),
  menuAtlas: byId("menu-atlas"),
  menuMore: byId("menu-more"),
  moreMenu: byId("more-menu"),
  resetLayout: byId("reset-layout"),
  openReview: byId("open-review"),
  closeReview: byId("close-review"),
  atlasHandle: byId("atlas-handle"),
  atlasResize: byId("atlas-resize")
};

const activity = {snapshot: null, error: "", request: null, timer: null, feedKey: null, allNodes: false};
const guidanceDrafts = new Map();
const activityKinds = {
  worker: "证明推进", model: "模型调用", lean: "Lean 核验", task: "任务",
  pass: "推进结果", agent_feedback: "模型反馈", verifier_feedback: "核验反馈", guidance: "你的意见"
};
const activityStates = {
  queued: "排队中", running: "运行中", leased: "已领取", waiting_retry: "等待重试", waiting_dependency: "等待依赖",
  lease_expired: "租约过期", succeeded: "已完成", finished: "已完成", completed: "已完成", failed: "失败",
  ready: "待处理", recorded: "已记录", cancelled: "已取消", stopped: "已停止", open: "未解决", blocked: "遇到卡点",
  lemma_verified: "引理已核验", route_checked: "归约已核验", awaiting_evidence_review: "待人审",
  budget_exhausted: "预算用完", proof_search_blocked: "路线受阻"
};
const guidanceAudiences = {local: "本地模型", codex: "Codex（外部服务）"};
const providerNames = {"codex-cli": "Codex", codex: "Codex", "claude-code": "Claude Code"};
const clock = new Intl.DateTimeFormat("zh-CN", {hour: "2-digit", minute: "2-digit"});
const dayClock = new Intl.DateTimeFormat("zh-CN", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"});

const state = {
  graph: null,
  claims: [],
  claimId: null,
  view: "proof",
  selected: null,
  assumption: null,
  impact: null,
  loadingImpact: false,
  impactError: "",
  graphRequest: null,
  impactRequest: null,
  atlas: null,
  camera: {x: 0, y: 0, k: 1},
  cameraMoved: false,
  panel: "review",
  reviewFilter: false
};

const labels = {
  kind: {
    goal: "原命题", hypothesis: "假设", library: "定理库", local_lemma: "中间引理",
    reduction: "归约", obligation: "证明义务", evidence: "Lean 核验"
  },
  status: {
    open: "进行中", active: "有效", confirmed: "已确认", frozen: "已冻结",
    approved: "已批准", verified: "已核验", proved: "已证明",
    needs_recheck: "待重验", pending: "待处理", blocked: "卡点", failed: "失败",
    rejected: "已拒绝", withdrawn: "已撤回", draft: "草稿", ready: "可核验",
    available: "可复用", returned: "已退回", refuted: "已反驳", stale: "已过期",
    running: "核验中", planned: "已计划", unsolved: "待证明", proved_goal: "已证明",
    awaiting_review: "待你审阅", awaiting_statement_review: "待陈述审阅",
    awaiting_obligations: "待证明义务", waiting_for_theorems: "待依赖定理", waiting_for_reduction: "等待归约重验",
    lemma_verified: "引理已核验", route_checked: "归约已核验"
  },
  edge: {actual: "实际引用", planned: "计划依赖", scope: "登记前提"}
};
const tones = {
  ok: ["verified", "proved", "proved_goal", "approved", "route_checked", "lemma_verified"],
  review: ["awaiting_review", "awaiting_statement_review"],
  bad: ["needs_recheck", "blocked", "failed", "rejected", "returned", "stale"],
  open: ["open", "unsolved", "pending", "ready"],
  planned: ["planned", "awaiting_obligations", "waiting_for_theorems", "waiting_for_reduction"]
};
const toneLabels = {ok: "已核验", review: "待你审阅", bad: "卡点 / 待重验", open: "待证明", planned: "已计划"};
const referenceLabels = {
  claim_id: "课题 ID", statement_id: "Lean 陈述 ID", statement_version: "陈述版本",
  statement_sha256: "陈述摘要", basis_version_id: "依据版本 ID", version: "依据版本",
  lemma_id: "引理 ID", check_id: "Lean 核验 ID", evidence_id: "证据 ID",
  verification_attempt_id: "核验尝试 ID", implication_id: "归约 ID", position: "义务序号",
  source_claim_id: "来源课题 ID", module: "Lean 模块", sha256: "SHA-256"
};
const card = {width: 160, pitch: 196, row: 132, pad: 48};
const categories = ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5"];

/* ---------- Basics ---------- */

function sessionKey() {
  const key = "research-multiplier-session";
  const token = new URLSearchParams(location.hash.slice(1)).get("session");
  if (token) {
    history.replaceState(null, "", location.pathname + location.search);
    try { sessionStorage.setItem(key, token); } catch { /* The tab can still use the launch token. */ }
    return token;
  }
  try { return sessionStorage.getItem(key); } catch { return null; }
}
const session = sessionKey();

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.data ? "POST" : "GET", cache: "no-store", mode: "same-origin", credentials: "omit", redirect: "error",
    headers: {"X-RM-Session": session, ...(options.data ? {"Content-Type": "application/json"} : {})},
    body: options.data ? JSON.stringify(options.data) : undefined, signal: options.signal
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("本机服务返回了无法读取的响应。");
  }
  if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : "本机服务未能读取项目。");
  return data;
}

function item(tag, text = "", name = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (name) node.className = name;
  return node;
}

function svgItem(tag, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function icon(kind) {
  const svg = svgItem("svg", {viewBox: "0 0 12 12", "aria-hidden": "true", class: kind});
  if (kind === "spin") {
    svg.append(svgItem("circle", {cx: 6, cy: 6, r: 4.5, class: "track"}),
      svgItem("path", {d: "M6 1.5a4.5 4.5 0 0 1 4.5 4.5", class: "arc"}));
  } else if (kind === "clock") {
    svg.append(svgItem("circle", {cx: 6, cy: 6, r: 4.5}), svgItem("path", {d: "M6 3.5V6l1.7 1.2"}));
  } else {
    svg.append(svgItem("path", {d: "m4.2 2.5 3 3.5-3 3.5"}));
  }
  return svg;
}

function mark(tone, shape) {
  const dot = item("span", "", "mark");
  dot.setAttribute("aria-hidden", "true");
  if (tone) dot.dataset.tone = tone;
  if (shape) dot.dataset.shape = shape;
  return dot;
}

function status(value) {
  return labels.status[value] ?? value ?? "";
}

function nodeStatus(node) {
  if (node.kind === "evidence" && node.status === "pending") return labels.status.awaiting_review;
  if (node.kind === "hypothesis" && node.status === "approved") return labels.status.confirmed;
  return status(node.status);
}

function summarize(value, limit) {
  const summary = String(value).trim().replace(/\s+/g, " ");
  return summary.length > limit ? summary.slice(0, limit).trimEnd() + " …" : summary;
}

function timeText(value, withDay = false) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : (withDay ? dayClock : clock).format(date);
}

const uuidTitle = /^(?:[^:]+:)?[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i;

function nodeTitle(node) {
  let title = typeof node.title === "string" ? node.title : "";
  title = title.replace(/^Proof obligation (\d+)$/, "证明义务 $1")
    .replace(/^Checked reduction (\d+)$/, "归约 $1")
    .replace(/^Lean lemma (\d+)$/, "中间引理 $1");
  if (node.kind === "evidence" && typeof node.citation === "string" && node.citation) {
    return node.citation.split(".").pop();
  }
  if (node.kind === "evidence") return "Lean 核验";
  if (node.kind === "library" && node.citation) return node.citation;
  if (title && !uuidTitle.test(title) && title.length <= 60) return title;
  const formula = node.proposition || (node.kind === "hypothesis" ? node.formalization : null);
  if (typeof formula === "string" && formula.length <= 44 && !formula.includes("\n")) return formula;
  return labels.kind[node.kind] ?? "登记记录";
}

function nodeFormula(node) {
  for (const value of [node.formula, node.proposition, node.formalization]) {
    if (typeof value === "string" && value.trim() && !value.includes("\n") && value.length <= 120) return value.trim();
  }
  return "";
}

function nodeSub(node) {
  if (node.kind === "evidence") {
    const id = node.reference?.evidence_id;
    return {text: "Lean 核验" + (id ? " · " + String(id).slice(0, 8) : ""), math: false};
  }
  const formula = nodeFormula(node);
  if (formula && formula !== nodeTitle(node)) return {text: summarize(formula, 44), math: true};
  return {text: labels.kind[node.kind] ?? "", math: false};
}

function nodeTone(node, blocked) {
  if (blocked) return "bad";
  if (node.kind === "evidence" && ["pending", "awaiting_review"].includes(node.status)) return "review";
  // An approved premise or library entry is an input, not a checked result.
  if (["hypothesis", "library"].includes(node.kind) && ["approved", "confirmed", "active", "available"].includes(node.status)) return "";
  for (const [tone, values] of Object.entries(tones)) {
    if (values.includes(node.status)) return node.kind === "goal" && tone === "planned" ? "open" : tone;
  }
  if (node.kind === "goal") return "open";
  return "";
}

function setNotice(message, error = false) {
  ui.notice.hidden = !message;
  ui.notice.dataset.error = String(error);
  ui.notice.textContent = message;
}

/* ---------- Panel sizes ---------- */

const layoutKey = "research-multiplier-workbench-layout";
const defaultSizes = {side: 240, right: 340, chat: 320, chatOpen: true, atlasOpen: true,
  sidebarOpen: null, reviewOpen: null, atlas: null};
const sizes = structuredClone(defaultSizes);
const sizeLimits = {side: [180, 420], right: [280, 640], chat: [140, 640]};

function loadSizes() {
  try {
    const saved = JSON.parse(localStorage.getItem(layoutKey) || "{}");
    for (const key of ["side", "right", "chat"]) if (typeof saved[key] === "number") sizes[key] = saved[key];
    for (const key of ["chatOpen", "atlasOpen", "sidebarOpen", "reviewOpen"]) {
      if (typeof saved[key] === "boolean") sizes[key] = saved[key];
    }
    const atlas = saved.atlas;
    if (atlas && ["x", "y", "w"].every((key) => typeof atlas[key] === "number")) sizes.atlas = atlas;
    else sizes.atlas = null;
  } catch { /* Defaults stay in place. */ }
}

// Without a saved choice, side panels fold away on narrow windows so the columns stay side by side.
function sidebarOpen() {
  return sizes.sidebarOpen ?? window.innerWidth >= 900;
}

function reviewOpen() {
  return sizes.reviewOpen ?? window.innerWidth >= 760;
}

function saveSizes() {
  try { localStorage.setItem(layoutKey, JSON.stringify(sizes)); } catch { /* Sizes still apply for this visit. */ }
}

function clampSize(key, value) {
  const [low, high] = sizeLimits[key];
  // The conversation never takes more than about half the window, so the graph keeps room.
  const ceiling = key === "chat" ? Math.min(high, Math.round(ui.workbench.clientHeight * 0.45)) : high;
  return Math.round(Math.max(low, Math.min(Math.max(low, ceiling), value)));
}

function applySizes() {
  for (const key of ["side", "right", "chat"]) sizes[key] = clampSize(key, sizes[key]);
  const side = sidebarOpen();
  const review = reviewOpen();
  ui.workbench.dataset.sidebar = side ? "open" : "collapsed";
  ui.workbench.dataset.review = review ? "open" : "collapsed";
  ui.toggleSidebar.setAttribute("aria-expanded", String(side));
  ui.toggleSidebar.setAttribute("aria-label", side ? "收起侧栏" : "展开侧栏");
  ui.openReview.hidden = review || !state.graph;
  ui.menuAtlas.setAttribute("aria-pressed", String(sizes.atlasOpen));
  placeAtlas();
  ui.workbench.style.setProperty("--side-w", sizes.side + "px");
  ui.workbench.style.setProperty("--right-w", sizes.right + "px");
  ui.workbench.style.setProperty("--chat-h", sizes.chat + "px");
  ui.activity.dataset.collapsed = String(!sizes.chatOpen);
  ui.chatBody.hidden = !sizes.chatOpen;
  ui.toggleChat.setAttribute("aria-expanded", String(sizes.chatOpen));
  ui.toggleChat.setAttribute("aria-label", sizes.chatOpen ? "收起对话" : "展开对话");
  for (const splitter of document.querySelectorAll(".splitter")) {
    const key = splitter.dataset.split;
    splitter.hidden = key === "chat" && !sizes.chatOpen;
    splitter.setAttribute("aria-valuenow", String(sizes[key]));
    splitter.setAttribute("aria-valuemin", String(sizeLimits[key][0]));
    splitter.setAttribute("aria-valuemax", String(sizeLimits[key][1]));
  }
}

function resizeBy(key, delta) {
  sizes[key] = clampSize(key, sizes[key] + delta);
  applySizes();
}

function startResize(event) {
  const splitter = event.currentTarget;
  if (event.button !== 0) return;
  const key = splitter.dataset.split;
  const start = {x: event.clientX, y: event.clientY, value: sizes[key]};
  splitter.setPointerCapture(event.pointerId);
  splitter.dataset.dragging = "true";
  const move = (moveEvent) => {
    const delta = key === "side" ? moveEvent.clientX - start.x
      : key === "right" ? start.x - moveEvent.clientX : start.y - moveEvent.clientY;
    sizes[key] = clampSize(key, start.value + delta);
    applySizes();
  };
  const stop = () => {
    splitter.removeEventListener("pointermove", move);
    splitter.removeEventListener("pointerup", stop);
    splitter.removeEventListener("pointercancel", stop);
    delete splitter.dataset.dragging;
    saveSizes();
  };
  splitter.addEventListener("pointermove", move);
  splitter.addEventListener("pointerup", stop);
  splitter.addEventListener("pointercancel", stop);
  event.preventDefault();
}

function resizeKey(event) {
  const key = event.currentTarget.dataset.split;
  const grow = {side: ["ArrowRight", "ArrowLeft"], right: ["ArrowLeft", "ArrowRight"], chat: ["ArrowUp", "ArrowDown"]}[key];
  const step = event.shiftKey ? 48 : 16;
  if (event.key === grow[0]) resizeBy(key, step);
  else if (event.key === grow[1]) resizeBy(key, -step);
  else return;
  event.preventDefault();
  saveSizes();
}

/* ---------- Claims ---------- */

function renderClaims() {
  const query = ui.claimSearch.value.trim().toLowerCase();
  const counts = new Map((state.atlas?.claims ?? []).map((claim) => [claim.id, claim]));
  const shown = state.claims.filter((claim) => (!query || (claim.title || "").toLowerCase().includes(query)
    || (claim.domain || "").toLowerCase().includes(query))
    && (!state.reviewFilter || counts.get(claim.id)?.awaiting_review > 0));
  const groups = new Map();
  for (const claim of shown) {
    const domain = claim.domain || "未分类";
    if (!groups.has(domain)) groups.set(domain, []);
    groups.get(domain).push(claim);
  }
  const sections = [...groups].sort(([a], [b]) => a.localeCompare(b)).map(([domain, claims]) => {
    const group = item("div", "", "claim-group");
    group.append(item("span", domain));
    for (const claim of claims) {
      const button = item("button", "", "claim");
      button.type = "button";
      button.dataset.claim = claim.id;
      if (claim.id === state.claimId) button.setAttribute("aria-current", "page");
      const record = counts.get(claim.id);
      const badge = record?.awaiting_review ? `${record.awaiting_review} 待审阅`
        : record?.blocked ? `${record.blocked} 卡点` : status(claim.status);
      const label = item("span", badge, "claim-status");
      label.dataset.attention = String(Boolean(record?.awaiting_review || record?.blocked));
      button.append(item("span", claim.title || "未命名课题", "claim-title"), label);
      group.append(button);
    }
    return group;
  });
  ui.claimList.replaceChildren(...(sections.length ? sections
    : [item("p", state.reviewFilter ? "没有等你审阅的课题。" : state.claims.length ? "没有匹配的课题。" : "这个项目还没有课题。", "claim-empty")]));
  const awaiting = [...counts.values()].reduce((sum, claim) => sum + (claim.awaiting_review ?? 0), 0);
  const library = libraryEntries().length;
  ui.menuReviewCount.textContent = awaiting ? String(awaiting) : "";
  ui.menuReview.setAttribute("aria-pressed", String(state.reviewFilter));
  ui.menuLibraryCount.textContent = library ? String(library) : "";
  ui.menuLibrary.setAttribute("aria-pressed", String(state.panel === "library"));
}

function libraryEntries() {
  const entries = new Map();
  for (const claim of state.atlas?.claims ?? []) {
    for (const node of claim.nodes) {
      if (node.kind !== "library" || !node.title) continue;
      if (!entries.has(node.title)) entries.set(node.title, {title: node.title, uses: []});
      entries.get(node.title).uses.push({claim: claim.id, claimTitle: claim.title, node: node.id});
    }
  }
  return [...entries.values()].sort((a, b) => a.title.localeCompare(b.title));
}

async function loadInitial() {
  if (!session) {
    setNotice("请从本机命令启动定理图谱，再用启动时提供的链接打开。", true);
    ui.workspaceState.textContent = "未连接";
    return;
  }
  ui.refresh.disabled = true;
  try {
    const data = await api("/api/claims");
    state.claims = data.claims;
    ui.workspaceState.textContent = `已连接 · ${data.claims.length} 个课题`;
    const previous = state.claimId;
    const chosen = previous && data.claims.some((entry) => entry.id === previous) ? previous : data.claim_id ?? data.claims[0]?.id;
    renderClaims();
    loadAtlas();
    if (chosen) {
      await loadGraph(chosen);
    } else {
      resetActivity();
      state.graph = null;
      state.claimId = null;
      ui.title.textContent = "定理图谱";
      ui.meta.hidden = true;
      ui.tabs.hidden = true;
      ui.content.hidden = true;
      renderQueue();
      ui.detail.replaceChildren();
      setNotice("这个项目还没有课题。登记课题和形式化后，刷新即可查看。");
    }
  } catch (error) {
    ui.workspaceState.textContent = "读取失败";
    setNotice(error.message, true);
  } finally {
    ui.refresh.disabled = false;
  }
}

function exitImpactRequest() {
  state.impactRequest?.abort();
  state.impactRequest = null;
  state.loadingImpact = false;
  state.impact = null;
  state.impactError = "";
}

async function loadGraph(id) {
  state.graphRequest?.abort();
  exitImpactRequest();
  const preserve = state.claimId === id;
  resetActivity();
  const request = new AbortController();
  state.graphRequest = request;
  state.claimId = id;
  renderClaims();
  setNotice("正在读取课题与核验记录…");
  ui.content.hidden = true;
  ui.tabs.hidden = true;
  ui.meta.hidden = true;
  try {
    const snapshot = await api("/api/graph?claim=" + encodeURIComponent(id), {signal: request.signal});
    if (state.graphRequest !== request) return;
    if (buildGraph(snapshot, preserve)) {
      setNotice("");
      applySizes();
      renderAtlas();
      pollActivity();
    }
  } catch (error) {
    if (state.graphRequest === request) {
      state.graph = null;
      ui.title.textContent = state.claims.find((claim) => claim.id === id)?.title || "定理图谱";
      renderQueue();
      ui.detail.replaceChildren();
      setNotice(error.message, true);
    }
  }
}

/* ---------- Graph ---------- */

function graphNode(node, blockers) {
  const button = item("button", "", "graph-node");
  button.type = "button";
  button.dataset.node = node.id;
  button.dataset.kind = node.kind;
  button.setAttribute("aria-controls", ui.detail.id);
  const head = item("span", "", "node-head");
  const dot = mark(nodeTone(node, blockers.has(node.id)), node.kind === "hypothesis" ? "square" : "");
  const title = item("span", nodeTitle(node), "node-title");
  head.append(dot, title);
  const sub = nodeSub(node);
  const subline = item("span", sub.text, "node-sub");
  subline.dataset.math = String(sub.math);
  const caption = item("span", "", "node-caption");
  const work = item("span", "", "work");
  work.hidden = true;
  button.title = nodeTitle(node);
  button.append(head, subline, caption, work);
  return {node, button, dot, caption, work, workText: ""};
}

function graphEdge(edge) {
  const path = svgItem("path", {class: "graph-edge"});
  path.dataset.kind = edge.kind === "scope" ? "context" : edge.kind;
  return {...edge, path};
}

function indexedEdges(edges) {
  const incoming = new Map();
  const outgoing = new Map();
  for (const edge of edges) {
    if (!incoming.has(edge.target)) incoming.set(edge.target, []);
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    incoming.get(edge.target).push(edge);
    outgoing.get(edge.source).push(edge);
  }
  return {incoming, outgoing};
}

function rankLevels(nodes, edges, outgoing) {
  const indegree = new Map(nodes.map((node) => [node.id, 0]));
  const depth = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of edges) indegree.set(edge.target, indegree.get(edge.target) + 1);
  const queue = nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id);
  for (const id of queue) {
    for (const edge of outgoing.get(id) ?? []) {
      indegree.set(edge.target, indegree.get(edge.target) - 1);
      depth.set(edge.target, Math.max(depth.get(edge.target), depth.get(id) + 1));
      if (indegree.get(edge.target) === 0) queue.push(edge.target);
    }
  }
  const levels = [];
  for (const node of nodes) (levels[depth.get(node.id)] ??= []).push(node.id);
  return levels.filter(Boolean);
}

function arrangeLevels(levels, incoming, outgoing) {
  const rows = new Map(levels.flatMap((group) => group.map((id, row) => [id, row / Math.max(1, group.length - 1)])));
  // Use earlier neighbours to order a level and later neighbours to tidy the preceding one.
  for (const [groups, index, endpoint] of [[levels, incoming, "source"], [[...levels].reverse(), outgoing, "target"]]) {
    for (const group of groups) {
      const averages = new Map(group.map((id) => {
        const neighbors = index.get(id) ?? [];
        const mean = neighbors.length ? neighbors.reduce((sum, edge) => sum + rows.get(edge[endpoint]), 0) / neighbors.length : rows.get(id);
        return [id, mean];
      }));
      group.sort((a, b) => averages.get(a) - averages.get(b));
      group.forEach((id, row) => rows.set(id, row / Math.max(1, group.length - 1)));
    }
  }
}

function buildGraph(snapshot, preserve) {
  const ordered = [...snapshot.nodes].sort((a, b) => nodeTitle(a).localeCompare(nodeTitle(b)) || a.id.localeCompare(b.id));
  const nodes = new Map(ordered.map((node) => [node.id, node]));
  const evidenceAliases = new Map(ordered.filter((node) => node.reference?.evidence_id)
    .map((node) => ["evidence:" + node.reference.evidence_id, node.id]));
  const blockers = new Map();
  for (const blocker of snapshot.blockers ?? []) {
    if (!blockers.has(blocker.node_id)) blockers.set(blocker.node_id, []);
    blockers.get(blocker.node_id).push(blocker);
  }
  const buttons = new Map(ordered.map((node) => [node.id, graphNode(node, blockers)]));
  const edges = snapshot.edges
    .filter((edge) => nodes.has(edge.source) && nodes.has(edge.target) && edge.source !== edge.target)
    .map(graphEdge);
  const {incoming, outgoing} = indexedEdges(edges);
  const levels = rankLevels(ordered, edges, outgoing);
  arrangeLevels(levels, incoming, outgoing);
  const hypotheses = ordered.filter((node) => node.kind === "hypothesis" && node.reference?.basis_version_id);
  state.graph = {nodes, buttons, edges, incoming, outgoing, levels, blockers, hypotheses, evidenceAliases,
    statementId: snapshot.claim.statement_id, claim: snapshot.claim};
  const selected = preserve && nodes.has(state.selected) ? state.selected : snapshot.focus_id;
  state.selected = nodes.has(selected) ? selected : (ordered.find((node) => node.kind === "goal") ?? ordered[0])?.id;
  state.assumption = hypotheses.some((node) => node.id === state.assumption) && preserve ? state.assumption : hypotheses[0]?.id;
  ui.title.textContent = snapshot.claim.title || "未命名课题";
  ui.meta.textContent = [status(snapshot.claim.status), snapshot.claim.domain].filter(Boolean).join(" · ");
  ui.meta.hidden = !ui.meta.textContent;
  ui.edges.replaceChildren(...edges.map((edge) => edge.path));
  ui.nodes.replaceChildren(...[...buttons.values()].map(({button}) => button));
  ui.premise.replaceChildren();
  for (const premise of hypotheses) {
    const option = item("option", nodeTitle(premise));
    option.value = premise.id;
    ui.premise.append(option);
  }
  ui.tabs.hidden = !ordered.length;
  ui.content.hidden = !ordered.length;
  if (!ordered.length) {
    setNotice("这个课题还没有已登记的图谱节点。");
    renderQueue();
    return false;
  }
  layoutGraph();
  if (!preserve) state.cameraMoved = false;
  if (!state.cameraMoved) fitView();
  setView(preserve ? state.view : "proof", false);
  return true;
}

function layoutGraph() {
  const graph = state.graph;
  if (!graph?.levels.length || ui.content.hidden) return;
  const ranks = graph.levels.length;
  const breadth = graph.levels.reduce((largest, level) => Math.max(largest, level.length), 1);
  const width = 2 * card.pad + (breadth - 1) * card.pitch + card.width;
  const points = new Map();
  // Dependencies sit below what uses them, so the claim reads from the top.
  graph.levels.forEach((group, rank) => {
    const y = card.pad + (ranks - 1 - rank) * card.row;
    const start = card.pad + (breadth - group.length) * card.pitch / 2;
    group.forEach((id, index) => points.set(id, {x: start + index * card.pitch, y}));
  });
  for (const [id, point] of points) {
    const {button} = graph.buttons.get(id);
    button.style.setProperty("--x", point.x + "px");
    button.style.setProperty("--y", point.y + "px");
  }
  let height = 0;
  for (const [id, point] of points) {
    point.h = graph.buttons.get(id).button.offsetHeight || 72;
    height = Math.max(height, point.y + point.h);
  }
  height += card.pad;
  ui.plot.style.width = width + "px";
  ui.plot.style.height = height + "px";
  ui.edges.setAttribute("viewBox", `0 0 ${width} ${height}`);
  for (const edge of graph.edges) {
    const a = points.get(edge.source);
    const b = points.get(edge.target);
    const ax = a.x + card.width / 2;
    const bx = b.x + card.width / 2;
    const top = a.y;
    const bottom = b.y + b.h;
    const middle = (top + bottom) / 2;
    edge.path.setAttribute("d", `M ${ax} ${top} C ${ax} ${middle}, ${bx} ${middle}, ${bx} ${bottom}`);
  }
  graph.size = {width, height};
}

/* ---------- Camera ---------- */

function applyCamera() {
  const {x, y, k} = state.camera;
  ui.plot.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
  ui.zoomReset.textContent = Math.round(k * 100) + "%";
}

function fitView() {
  const size = state.graph?.size;
  const width = ui.stage.clientWidth;
  const height = ui.stage.clientHeight;
  if (!size || !width || !height) return;
  // Leave the overview map uncovered when there is room beside it; otherwise the graph may pass under it.
  const box = atlasBox();
  const mapRight = ui.atlas.hidden || box.y > height / 2 ? 0 : box.x + box.w + 12;
  const inset = mapRight && mapRight < width * 0.4 ? mapRight : 0;
  const top = 0;
  const k = Math.max(0.3, Math.min(1.25, (width - inset - 32) / size.width, (height - top - 56) / size.height));
  state.camera = {k, x: inset + (width - inset - size.width * k) / 2,
    y: top + Math.max(8, (height - top - size.height * k) / 2)};
  applyCamera();
}

function zoomAt(factor, cx, cy) {
  const {x, y, k} = state.camera;
  const next = Math.max(0.3, Math.min(2.5, k * factor));
  if (cx === undefined) {
    cx = ui.stage.clientWidth / 2;
    cy = ui.stage.clientHeight / 2;
  }
  state.camera = {k: next, x: cx - (cx - x) * next / k, y: cy - (cy - y) * next / k};
  state.cameraMoved = true;
  applyCamera();
}

function panBy(dx, dy) {
  state.camera = {...state.camera, x: state.camera.x + dx, y: state.camera.y + dy};
  state.cameraMoved = true;
  applyCamera();
}

function onWheel(event) {
  event.preventDefault();
  if (event.ctrlKey || event.metaKey) {
    const bounds = ui.stage.getBoundingClientRect();
    zoomAt(Math.exp(-event.deltaY * 0.01), event.clientX - bounds.left, event.clientY - bounds.top);
  } else {
    panBy(-event.deltaX, -event.deltaY);
  }
}

function startPan(event) {
  if (event.button !== 0 || event.target.closest("button, a, select, input, textarea, figure, .zoom")) return;
  const start = {x: event.clientX, y: event.clientY, camera: {...state.camera}};
  ui.stage.setPointerCapture(event.pointerId);
  ui.stage.dataset.panning = "true";
  const move = (moveEvent) => {
    state.camera = {...start.camera, x: start.camera.x + moveEvent.clientX - start.x, y: start.camera.y + moveEvent.clientY - start.y};
    state.cameraMoved = true;
    applyCamera();
  };
  const stop = () => {
    ui.stage.removeEventListener("pointermove", move);
    ui.stage.removeEventListener("pointerup", stop);
    ui.stage.removeEventListener("pointercancel", stop);
    delete ui.stage.dataset.panning;
  };
  ui.stage.addEventListener("pointermove", move);
  ui.stage.addEventListener("pointerup", stop);
  ui.stage.addEventListener("pointercancel", stop);
}

function stageKey(event) {
  if (event.target !== ui.stage) return;
  const pan = {ArrowLeft: [48, 0], ArrowRight: [-48, 0], ArrowUp: [0, 48], ArrowDown: [0, -48]}[event.key];
  if (pan) panBy(...pan);
  else if (event.key === "+" || event.key === "=") zoomAt(1.2);
  else if (event.key === "-") zoomAt(1 / 1.2);
  else if (event.key === "0") { state.cameraMoved = false; fitView(); }
  else return;
  event.preventDefault();
}

function revealNode(id) {
  const button = state.graph?.buttons.get(id)?.button;
  if (!button) return;
  const {x, y, k} = state.camera;
  const left = x + button.offsetLeft * k;
  const top = y + button.offsetTop * k;
  const right = left + button.offsetWidth * k;
  const bottom = top + button.offsetHeight * k;
  const width = ui.stage.clientWidth;
  const height = ui.stage.clientHeight;
  if (left >= 0 && top >= 0 && right <= width && bottom <= height) return;
  panBy(width / 2 - (left + right) / 2, height / 2 - (top + bottom) / 2);
}

/* ---------- Highlights, legend and work ---------- */

function traceEdges(first, index, endpoint) {
  const reached = new Set([first]);
  const traversed = new Set();
  for (const id of reached) {
    for (const edge of index.get(id) ?? []) {
      traversed.add(edge);
      reached.add(edge[endpoint]);
    }
  }
  return traversed;
}

function impactItems() {
  const results = new Map();
  for (const group of [state.impact?.affected, state.impact?.scope]) {
    for (const entry of group ?? []) {
      const id = state.graph.evidenceAliases.get(entry.id) ?? entry.id;
      results.set(id, results.get(id) ?? entry);
    }
  }
  return results;
}

function impactLine(effect) {
  if (effect.relation) return effect.status === "needs_recheck" ? "已待重验" : "待重验";
  return effect.kind === "obligation" ? "等待归约重验" : "范围需确认";
}

function updateHighlights() {
  const graph = state.graph;
  if (!graph) return;
  const impact = state.view === "impact";
  const affected = impactItems();
  const highlightedNodes = new Set([state.selected]);
  const highlightedEdges = new Set();
  if (impact) {
    highlightedNodes.add(state.assumption);
    for (const id of affected.keys()) highlightedNodes.add(id);
    for (const edge of graph.edges) {
      if (highlightedNodes.has(edge.source) && highlightedNodes.has(edge.target)) highlightedEdges.add(edge);
    }
  } else {
    for (const edge of traceEdges(state.selected, graph.incoming, "source")) highlightedEdges.add(edge);
    for (const edge of traceEdges(state.selected, graph.outgoing, "target")) highlightedEdges.add(edge);
    for (const edge of graph.outgoing.get(state.selected) ?? []) {
      if (edge.kind === "planned" && graph.nodes.get(edge.target).kind === "reduction") {
        for (const upstream of traceEdges(edge.target, graph.incoming, "source")) highlightedEdges.add(upstream);
      }
    }
    for (const edge of graph.edges) {
      const a = graph.nodes.get(edge.source).domain;
      const b = graph.nodes.get(edge.target).domain;
      if (state.view === "references" && edge.kind === "actual" && a && b && a !== b) highlightedEdges.add(edge);
    }
    for (const edge of highlightedEdges) {
      highlightedNodes.add(edge.source);
      highlightedNodes.add(edge.target);
    }
  }
  for (const [id, entry] of graph.buttons) {
    const {node, button, caption} = entry;
    const blocked = graph.blockers.has(id);
    const effect = affected.get(id);
    button.setAttribute("aria-pressed", String(id === state.selected));
    button.dataset.emphasis = !impact || highlightedNodes.has(id) ? "strong" : "dim";
    button.dataset.affected = String(impact && (Boolean(effect) || id === state.assumption));
    let line = blocked ? "卡点" : nodeStatus(node);
    if (impact && id === state.assumption) line = "待重验预览";
    else if (impact && effect) line = impactLine(effect);
    caption.textContent = line;
    describeGraphNode(entry);
  }
  for (const edge of graph.edges) edge.path.dataset.emphasis = highlightedEdges.has(edge) ? "strong" : "dim";
  renderLegend();
}

function describeGraphNode({node, button, caption, workText}) {
  const description = [nodeTitle(node), labels.kind[node.kind] ?? node.kind, caption.textContent, workText];
  button.setAttribute("aria-label", description.filter(Boolean).join("，"));
}

function liveWork() {
  const rows = activity.error ? [] : activity.snapshot?.activity ?? [];
  const byNode = new Map();
  let running = 0;
  let queued = 0;
  for (const record of rows) {
    if (!["running", "queued", "waiting_retry"].includes(record.state)) continue;
    const node = activityNode(record);
    if (!node) continue;
    if (!byNode.has(node)) byNode.set(node, {running: new Map(), queued: new Map()});
    const entry = byNode.get(node);
    const bucket = record.state === "running" ? entry.running : entry.queued;
    const name = record.kind === "model" ? modelName(record) : activityKinds[record.kind] ?? "任务";
    bucket.set(name, (bucket.get(name) ?? 0) + 1);
    if (record.state === "running") running += 1;
    else queued += 1;
  }
  return {byNode, running, queued};
}

function workLabel(counts) {
  return [...counts].map(([name, count]) => count > 1 ? `${name} ×${count}` : name).join("、");
}

function markWork() {
  const {byNode} = liveWork();
  let changed = false;
  for (const [id, entry] of state.graph?.buttons ?? []) {
    const work = byNode.get(id);
    const running = work?.running.size ? workLabel(work.running) : "";
    const queued = !running && work?.queued.size ? workLabel(work.queued) + " 排队中" : "";
    const text = running || queued;
    if (entry.workText === text) continue;
    changed = true;
    entry.workText = text;
    entry.work.hidden = !text;
    entry.work.dataset.state = running ? "running" : "queued";
    entry.work.replaceChildren(...(text ? [icon(running ? "spin" : "clock"), document.createTextNode(text)] : []));
    entry.button.dataset.working = String(Boolean(running));
    describeGraphNode(entry);
  }
  // A work badge changes the card height, so the edge endpoints move with it.
  if (changed) layoutGraph();
  renderLegend();
}

function renderLegend() {
  const graph = state.graph;
  if (!graph) return;
  const kinds = new Set(graph.edges.map((edge) => edge.path.dataset.kind));
  const parts = [];
  for (const [kind, label] of [["actual", "实际引用"], ["planned", "计划依赖"], ["context", "登记前提"]]) {
    if (!kinds.has(kind)) continue;
    const key = item("span");
    const line = item("i");
    line.dataset.kind = kind;
    key.append(line, document.createTextNode(label));
    parts.push(key);
  }
  if (parts.length) parts.push(item("span", "", "rule"));
  const counts = new Map();
  const affected = state.view === "impact" ? impactItems() : new Map();
  for (const [id, {node}] of graph.buttons) {
    const tone = state.view === "impact" && (affected.has(id) || id === state.assumption) ? "bad" : nodeTone(node, graph.blockers.has(id));
    if (tone) counts.set(tone, (counts.get(tone) ?? 0) + 1);
  }
  for (const tone of ["ok", "review", "bad", "open", "planned"]) {
    if (!counts.get(tone)) continue;
    const key = item("span");
    const label = tone === "bad" && state.view === "impact" ? "待重验 / 卡点" : toneLabels[tone];
    key.append(mark(tone), document.createTextNode(label), item("b", String(counts.get(tone))));
    parts.push(key);
  }
  const {running, queued} = liveWork();
  if (running || queued) parts.push(item("span", "", "rule"));
  if (running) {
    const key = item("span");
    key.append(icon("spin"), document.createTextNode("运行中"), item("b", String(running)));
    parts.push(key);
  }
  if (queued) {
    const key = item("span");
    key.append(icon("clock"), document.createTextNode("排队中"), item("b", String(queued)));
    parts.push(key);
  }
  ui.legend.replaceChildren(...parts);
}

/* ---------- Atlas ---------- */

async function loadAtlas() {
  try {
    state.atlas = await api("/api/atlas");
  } catch {
    state.atlas = null;
  }
  renderAtlas();
  renderClaims();
  if (state.panel === "library") renderQueue();
}

function atlasTree(claim) {
  const ids = new Set(claim.nodes.map((node) => node.id));
  const sources = new Map();
  const hasOutgoing = new Set();
  for (const [source, target] of claim.edges) {
    if (!ids.has(source) || !ids.has(target)) continue;
    hasOutgoing.add(source);
    if (!sources.has(target)) sources.set(target, []);
    sources.get(target).push(source);
  }
  const root = claim.nodes.find((node) => node.kind === "goal")?.id ?? claim.nodes.find((node) => !hasOutgoing.has(node.id))?.id;
  const children = new Map();
  const seen = new Set(root ? [root] : []);
  const queue = root ? [root] : [];
  for (const id of queue) {
    const next = (sources.get(id) ?? []).filter((child) => !seen.has(child));
    next.forEach((child) => seen.add(child));
    children.set(id, next);
    queue.push(...next);
  }
  // Anything not reachable from the claim's goal still belongs to it.
  const loose = claim.nodes.map((node) => node.id).filter((id) => !seen.has(id));
  if (root) children.set(root, [...(children.get(root) ?? []), ...loose]);
  loose.forEach((id) => children.set(id, []));
  const leaves = new Map();
  const count = (id) => {
    const kids = children.get(id) ?? [];
    const total = kids.length ? kids.reduce((sum, kid) => sum + count(kid), 0) : 1;
    leaves.set(id, total);
    return total;
  };
  let depth = 0;
  const measure = (id, level) => {
    depth = Math.max(depth, level);
    for (const kid of children.get(id) ?? []) measure(kid, level + 1);
  };
  if (root) {
    count(root);
    measure(root, 0);
  }
  return {root, children, leaves, depth, size: claim.nodes.length};
}

function renderAtlas() {
  const claims = state.atlas?.claims ?? [];
  ui.atlas.hidden = claims.length < 1 || !state.graph || !sizes.atlasOpen;
  placeAtlas();
  if (ui.atlas.hidden) return;
  const trees = claims.map((claim, index) => ({claim, index, tree: atlasTree(claim)})).filter(({tree}) => tree.root);
  const total = trees.reduce((sum, entry) => sum + Math.max(entry.tree.leaves.get(entry.tree.root), 1), 0);
  const gap = trees.length > 1 ? 6 : 0;
  const center = {x: 100, y: 75};
  const deepest = Math.max(1, ...trees.map(({tree}) => tree.depth));
  const step = Math.min(18, 58 / deepest);
  const point = (radius, angle) => ({x: center.x + radius * Math.cos(angle * Math.PI / 180), y: center.y + radius * Math.sin(angle * Math.PI / 180)});
  const groups = [];
  let angle = -90 - (360 - gap * trees.length) * Math.max(trees[0]?.tree.leaves.get(trees[0].tree.root) ?? 1, 1) / total / 2;
  let here = null;
  let nodeCount = 0;
  for (const {claim, index, tree} of trees) {
    const span = (360 - gap * trees.length) * Math.max(tree.leaves.get(tree.root), 1) / total;
    const current = claim.id === state.claimId;
    const color = index < categories.length ? `var(${categories[index]})` : "var(--edge-soft)";
    const group = svgItem("g", {class: current ? "current" : ""});
    group.setAttribute("stroke", color);
    group.setAttribute("fill", color);
    if (!current) group.setAttribute("opacity", "0.45");
    const place = (id, from, to, level, parent) => {
      const middle = (from + to) / 2;
      const at = point(10 + level * step, middle);
      group.append(svgItem("line", {x1: parent.x.toFixed(1), y1: parent.y.toFixed(1), x2: at.x.toFixed(1), y2: at.y.toFixed(1)}));
      group.append(svgItem("circle", {cx: at.x.toFixed(1), cy: at.y.toFixed(1), r: level ? 1.3 : 1.8, stroke: "none"}));
      nodeCount += 1;
      if (current && level === 1 && !here) here = at;
      const kids = tree.children.get(id) ?? [];
      let cursor = from;
      for (const kid of kids) {
        const share = (to - from) * tree.leaves.get(kid) / tree.leaves.get(id);
        place(kid, cursor, cursor + share, level + 1, at);
        cursor += share;
      }
    };
    place(tree.root, angle, angle + span, 0, center);
    if (current && !here) here = point(10, angle + span / 2);
    angle += span + gap;
    groups.push(group);
  }
  const marks = [svgItem("circle", {cx: center.x, cy: center.y, r: 2.6, fill: "var(--ink)", stroke: "none"})];
  if (here) marks.push(svgItem("circle", {cx: here.x.toFixed(1), cy: here.y.toFixed(1), r: 11, class: "here"}));
  ui.atlasMap.replaceChildren(...groups, ...marks);
  ui.atlasCount.textContent = `${nodeCount} 个节点`;
  const keys = trees.slice(0, categories.length).map(({claim, index}) => {
    const key = item("span");
    key.style.setProperty("--c", `var(${categories[index]})`);
    key.dataset.current = String(claim.id === state.claimId);
    key.title = claim.title || "";
    const dot = item("i");
    dot.setAttribute("aria-hidden", "true");
    key.append(dot, document.createTextNode(summarize(claim.title || "未命名", 5).replace(" …", "…")));
    return key;
  });
  if (trees.length > categories.length) keys.push(item("span", `+${trees.length - categories.length}`));
  ui.atlasKeys.replaceChildren(...keys);
}

// Until moved, the map sits top-left on a wide canvas and as a small map bottom-left on a narrow one.
function atlasBox() {
  if (sizes.atlas) return sizes.atlas;
  const width = ui.stage.clientWidth;
  const height = ui.stage.clientHeight;
  if (width && width < 640) return {x: 12, y: Math.max(56, height - 128), w: 124};
  return {x: 18, y: 14, w: 186};
}

function placeAtlas() {
  const width = ui.stage.clientWidth;
  const height = ui.stage.clientHeight;
  const box = {...atlasBox()};
  box.w = Math.round(Math.max(110, Math.min(420, box.w, width ? width - 24 : box.w)));
  if (width && height) {
    const tall = ui.atlas.offsetHeight || box.w * 0.85;
    box.x = Math.round(Math.max(0, Math.min(width - box.w, box.x)));
    box.y = Math.round(Math.max(0, Math.min(height - Math.min(tall, height), box.y)));
  }
  if (sizes.atlas) sizes.atlas = box;
  ui.atlas.style.setProperty("--atlas-x", box.x + "px");
  ui.atlas.style.setProperty("--atlas-y", box.y + "px");
  ui.atlas.style.setProperty("--atlas-w", box.w + "px");
  ui.atlas.dataset.small = String(box.w < 160);
}

function dragAtlas(event, mode) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  const target = event.currentTarget;
  const start = {x: event.clientX, y: event.clientY, box: {...atlasBox()}};
  target.setPointerCapture(event.pointerId);
  ui.atlas.dataset.moving = "true";
  const move = (moveEvent) => {
    const dx = moveEvent.clientX - start.x;
    const dy = moveEvent.clientY - start.y;
    sizes.atlas = mode === "move" ? {...start.box, x: start.box.x + dx, y: start.box.y + dy}
      : {...start.box, w: start.box.w + Math.max(dx, dy * 4 / 3)};
    placeAtlas();
  };
  const stop = () => {
    target.removeEventListener("pointermove", move);
    target.removeEventListener("pointerup", stop);
    target.removeEventListener("pointercancel", stop);
    delete ui.atlas.dataset.moving;
    saveSizes();
  };
  target.addEventListener("pointermove", move);
  target.addEventListener("pointerup", stop);
  target.addEventListener("pointercancel", stop);
}

function atlasKey(event) {
  const step = event.shiftKey ? 48 : 16;
  const change = {ArrowRight: step, ArrowDown: step, ArrowLeft: -step, ArrowUp: -step}[event.key];
  if (change === undefined) return;
  event.preventDefault();
  sizes.atlas = {...atlasBox()};
  sizes.atlas.w += change;
  placeAtlas();
  saveSizes();
}

/* ---------- Review column ---------- */

function addFact(list, heading, value, mono = false) {
  if (!value?.length) return;
  const description = item("dd");
  if (mono) description.dataset.mono = "true";
  if (typeof value === "string") description.textContent = value;
  else description.append(...value);
  list.append(item("dt", heading), description);
}

function relation(id, fallback = "") {
  const graphId = state.graph.evidenceAliases.get(id) ?? id;
  const node = state.graph.nodes.get(graphId);
  if (!node) {
    const label = item("span", fallback || "登记记录");
    label.title = id;
    return label;
  }
  const choice = item("button", nodeTitle(node), "relation");
  choice.type = "button";
  choice.dataset.select = graphId;
  return choice;
}

function renderLibrary() {
  ui.queueTitle.textContent = "定理库";
  const entries = libraryEntries();
  ui.queueCount.textContent = entries.length ? `${entries.length} 条` : "";
  const rows = entries.map((entry) => {
    const row = item("li");
    const button = item("button", "", "queue-item");
    button.type = "button";
    const here = entry.uses.find((use) => use.claim === state.claimId) ?? entry.uses[0];
    button.dataset.claimNode = here.claim + "|" + here.node;
    button.title = entry.uses.map((use) => use.claimTitle).join("、");
    const name = item("span");
    name.append(mark("", "square"), item("span", entry.title, "label"));
    button.append(name, item("span", entry.uses.length > 1 ? `${entry.uses.length} 个课题引用` : summarize(here.claimTitle || "", 10), "kind"));
    row.append(button);
    return row;
  });
  ui.queue.replaceChildren(...(rows.length ? rows : [item("li", state.atlas ? "这个项目还没有引用定理库。" : "正在读取…", "queue-empty")]));
}

function renderQueue() {
  const graph = state.graph;
  const entries = [];
  ui.openReview.textContent = "待你审阅";
  if (state.panel === "library" && state.view !== "impact") {
    renderLibrary();
    return;
  }
  if (graph && state.view === "impact") {
    ui.queueTitle.textContent = "受影响的记录";
    if (state.assumption) entries.push({id: state.assumption, kind: "待重验预览", tone: "bad"});
    for (const [id, effect] of impactItems()) {
      if (graph.nodes.has(id) && id !== state.assumption) entries.push({id, kind: impactLine(effect), tone: "bad"});
    }
  } else if (graph) {
    ui.queueTitle.textContent = "待你审阅";
    for (const [id, node] of graph.nodes) {
      const tone = nodeTone(node, false);
      if (tone === "review") entries.push({id, kind: nodeStatus(node), tone});
      else if (node.status === "needs_recheck") entries.push({id, kind: status(node.status), tone: "bad"});
    }
  }
  ui.queueCount.textContent = entries.length ? `${entries.length} 项` : "";
  if (entries.length && state.view !== "impact") ui.openReview.textContent = `待你审阅 ${entries.length}`;
  const rows = entries.map(({id, kind, tone}) => {
    const row = item("li");
    const button = item("button", "", "queue-item");
    button.type = "button";
    button.dataset.select = id;
    if (id === state.selected) button.setAttribute("aria-current", "true");
    const name = item("span");
    name.append(mark(tone), item("span", nodeTitle(graph.nodes.get(id)), "label"));
    button.append(name, item("span", kind, "kind"));
    row.append(button);
    return row;
  });
  ui.queue.replaceChildren(...(rows.length ? rows : [item("li", graph ? "目前没有需要你处理的记录。" : "", "queue-empty")]));
}

function showImpactDetails(list) {
  const preview = state.impact;
  if (!preview) return;
  const nameFor = (entry) => {
    const title = entry.title ?? entry.label ?? labels.kind[entry.kind] ?? "登记记录";
    const claimTitle = entry.claim_title ?? state.claims.find((claim) => claim.id === entry.claim_id)?.title;
    return relation(entry.id, claimTitle && entry.claim_id !== state.claimId ? title + " · " + claimTitle : title);
  };
  const affected = preview.affected ?? [];
  for (const [relationType, heading] of [["direct", "直接重验"], ["indirect", "间接重验"]]) {
    addFact(list, heading, affected.filter((entry) => entry.relation === relationType && entry.status !== "needs_recheck").map(nameFor));
  }
  addFact(list, "已处于待重验", affected.filter((entry) => entry.status === "needs_recheck").map(nameFor));
  const scope = preview.scope ?? [];
  addFact(list, "受影响的未完成步骤", scope.filter((entry) => entry.kind === "obligation").map(nameFor));
  addFact(list, "需要重新确认的冻结范围", scope.filter((entry) => entry.kind !== "obligation").map(nameFor));
  if (!(affected.length || scope.length)) addFact(list, "影响", "当前没有受影响的登记记录。");
}

function provenance(node) {
  const references = item("dl", "", "facts");
  for (const [key, label] of Object.entries(referenceLabels)) {
    const value = node.reference?.[key];
    if (typeof value === "string" || typeof value === "number") addFact(references, label, String(value), true);
  }
  if (!references.childElementCount) return null;
  const container = item("details");
  container.append(item("summary", "核对来源与版本"), references);
  return container;
}

function statementBlock(label, value, lean) {
  const block = item("div", "", "detail-block");
  block.append(item("span", label));
  block.append(lean ? item("pre", value, "lean") : item("p", value, "formula"));
  return block;
}

function showDetails() {
  const graph = state.graph;
  const node = graph?.nodes.get(state.selected);
  if (!node) {
    ui.detail.replaceChildren();
    return;
  }
  const blockers = graph.blockers.get(node.id) ?? [];
  const impact = state.view === "impact";
  const effect = impact ? impactItems().get(node.id) : null;
  const tone = impact && (effect || node.id === state.assumption) ? "bad" : nodeTone(node, blockers.length > 0);
  const stateLabel = impact && node.id === state.assumption ? "待重验预览"
    : effect ? impactLine(effect) : blockers.length ? "卡点" : nodeStatus(node);
  const head = item("div", "", "detail-head");
  const badge = item("span", "", "detail-state");
  badge.append(mark(tone), document.createTextNode(stateLabel));
  head.append(item("span", [labels.kind[node.kind] ?? node.kind, node.domain].filter(Boolean).join(" · ")), badge);
  const parts = [head, item("h2", nodeTitle(node))];
  for (const blockage of blockers) {
    const explanation = blockage.reason || blockage.detail || blockage.status;
    if (explanation) parts.push(item("p", "卡点：" + explanation, "detail-note"));
  }
  if (impact && node.id === state.assumption) {
    parts.push(item("p", state.loadingImpact ? "正在读取后端影响范围…" : state.impactError
      || "撤回这个版本后，用到它的记录会标为待重验。这是只读预览，不会改动记录。", "detail-note"));
  }
  const lean = typeof node.formalization === "string" ? node.formalization : "";
  const math = typeof node.formula === "string" ? node.formula : nodeFormula(node);
  const prose = typeof node.statement === "string" && node.statement !== lean && node.statement !== math ? node.statement : "";
  if (math && math !== lean) parts.push(statementBlock("公式", summarize(math, 240), false));
  else if (prose) parts.push(statementBlock("原文", summarize(prose, 240), false));
  if (lean) parts.push(statementBlock("Lean 陈述", lean.length > 600 ? summarize(lean, 600) : lean, true));
  const facts = item("dl", "", "facts");
  if (typeof node.citation === "string") addFact(facts, "来源", node.citation);
  if (node.verification_status && node.verification_status !== node.status) addFact(facts, "Lean 记录", status(node.verification_status));
  const incoming = graph.incoming.get(node.id) ?? [];
  for (const [kind, heading] of Object.entries(labels.edge)) {
    addFact(facts, heading, incoming.filter((edge) => edge.kind === kind).map((edge) => relation(edge.source)));
  }
  const outgoing = graph.outgoing.get(node.id) ?? [];
  addFact(facts, "用于", [...new Set(outgoing.map((edge) => edge.target))].map((id) => relation(id)));
  if (impact) showImpactDetails(facts);
  if (facts.childElementCount) parts.push(facts);
  const parameters = (node.parameters ?? []).map(({name, type}) => name + " : " + type).join("\n");
  if (lean.length > 600 || prose.length > 240 || parameters) {
    const expansion = item("details");
    expansion.append(item("summary", "完整 Lean 原文"));
    if (lean.length > 600) expansion.append(statementBlock("Lean 原文", lean, true));
    if (prose.length > 240) expansion.append(statementBlock("完整原文", prose, false));
    if (parameters) expansion.append(statementBlock("已登记的 Lean 参数", parameters, true));
    parts.push(expansion);
  }
  const source = provenance(node);
  if (source) parts.push(source);
  ui.detail.replaceChildren(...parts);
}

/* ---------- Views ---------- */

function updateView() {
  ui.workbench.dataset.view = state.view;
  ui.tabs.querySelectorAll("button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
  });
  ui.impact.hidden = state.view !== "impact";
  ui.premise.hidden = !state.graph.hypotheses.length;
  ui.impact.querySelector("label").hidden = ui.premise.hidden;
  ui.impactState.textContent = state.loadingImpact ? "正在读取后端影响范围…"
    : state.impactError || (ui.premise.hidden ? "这个课题暂时没有可预览的已登记假设。" : "只读预览，不会改动记录");
  if (state.assumption) ui.premise.value = state.assumption;
  updateHighlights();
  renderQueue();
  showDetails();
  renderActivity();
}

async function loadImpact(id) {
  exitImpactRequest();
  const basis = state.graph.nodes.get(id)?.reference?.basis_version_id;
  if (!basis) return;
  const request = new AbortController();
  state.impactRequest = request;
  state.loadingImpact = true;
  updateView();
  const query = new URLSearchParams({claim: state.claimId, basis});
  try {
    const preview = await api("/api/impact?" + query, {signal: request.signal});
    if (state.impactRequest === request) state.impact = preview;
  } catch (error) {
    if (state.impactRequest === request) state.impactError = error.message;
  } finally {
    if (state.impactRequest === request) {
      state.loadingImpact = false;
      updateView();
    }
  }
}

function setView(view, changeSelection = true) {
  state.view = view;
  if (view === "impact") {
    const current = state.graph.hypotheses.find(({id}) => id === state.selected);
    state.assumption = current?.id ?? state.assumption;
    if (state.assumption && changeSelection) state.selected = state.assumption;
    updateView();
    loadImpact(state.assumption);
  } else {
    exitImpactRequest();
    updateView();
  }
}

function selectNode(id, moveFocus = false) {
  const node = state.graph?.nodes.get(id);
  if (!node) return;
  state.selected = id;
  if (node.kind === "hypothesis" && node.reference?.basis_version_id) {
    state.assumption = id;
    if (state.view === "impact") loadImpact(id);
  }
  updateView();
  revealNode(id);
  if (moveFocus) state.graph.buttons.get(id)?.button.focus({preventScroll: true});
}

/* ---------- Conversation ---------- */

function activityNode(record) {
  for (const id of [record.node_id, record.fallback_node_id]) {
    const graphId = state.graph?.evidenceAliases.get(id) ?? id;
    if (state.graph?.nodes.has(graphId)) return graphId;
  }
  return null;
}

function isCodex(record) {
  return ["codex-cli", "codex"].includes(record.provider ?? record.model_provider);
}

function modelName(record) {
  if (typeof record.model === "string" && record.model) return record.model;
  const provider = record.provider ?? record.model_provider;
  return providerNames[provider] ?? provider ?? "模型";
}

function chatEntry(record) {
  const line = item("li");
  const at = record.at || record.created_at;
  if (record.kind === "guidance") {
    line.dataset.from = "you";
    const towardsCodex = record.audience === "codex";
    const progress = record.submitted_at
      ? towardsCodex ? "已随 Codex 请求提交" : "已随模型请求提交"
      : towardsCodex ? "等待 Codex 调用" : "等待本地模型调用";
    line.append(item("p", record.text ?? "", "bubble"),
      item("span", ["你", timeText(at, true), "发给 " + guidanceAudiences[record.audience ?? "local"], progress].filter(Boolean).join(" · "), "speaker"));
  } else if (record.state === "running" && ["model", "worker", "lean", "task"].includes(record.kind)) {
    const live = item("span", "", "live");
    const who = record.kind === "model" ? modelName(record) : activityKinds[record.kind];
    live.append(icon("spin"), item("b", who), document.createTextNode(record.kind === "worker" ? "正在推进证明…" : "正在运行…"));
    line.append(live);
  } else if (record.kind === "agent_feedback" || record.kind === "verifier_feedback") {
    const speaker = item("span", "", "speaker");
    speaker.append(item("b", record.kind === "agent_feedback" ? modelName(record) : "Lean 核验"), document.createTextNode(timeText(at, true)));
    line.append(speaker, item("p", record.text ?? "", "message"));
  } else {
    const tool = item("span", "", "tool");
    tool.title = record.text ?? "";
    const label = record.kind === "model" ? `${activityKinds.model} · ${modelName(record)}` : activityKinds[record.kind] ?? "工作记录";
    const result = item("span", "", "result");
    const tone = ["failed", "blocked", "lease_expired"].includes(record.state) ? "bad"
      : ["queued", "waiting_retry", "waiting_dependency"].includes(record.state) ? "planned" : "ok";
    result.append(mark(tone), document.createTextNode(activityStates[record.state] ?? status(record.state)));
    tool.append(icon("chevron"), document.createTextNode(label), result);
    const when = timeText(at, true);
    if (when) tool.append(document.createTextNode(when));
    line.append(tool);
  }
  if (activity.allNodes) {
    const target = activityNode(record);
    if (target) {
      const link = relation(target);
      link.className = "node-link";
      line.append(link);
    }
  }
  return line;
}

function renderActivity() {
  const rows = activity.snapshot?.activity ?? [];
  const guidance = (activity.snapshot?.guidance ?? []).map((entry) => ({...entry, kind: "guidance"}));
  const {running, queued} = liveWork();
  const summary = activity.error || (!activity.snapshot ? "正在读取运行记录…"
    : running ? `${running} 项运行中` : queued ? `${queued} 项排队中` : "当前没有运行中的任务");
  ui.activityState.replaceChildren(...(running && !activity.error ? [icon("spin")] : []), document.createTextNode(summary));
  ui.activityState.dataset.error = String(Boolean(activity.error));
  const target = state.graph?.nodes.get(state.selected);
  ui.chatTarget.textContent = activity.allNodes ? "整个课题" : target ? nodeTitle(target) : "";
  ui.chatScope.setAttribute("aria-pressed", String(activity.allNodes));
  const shown = [...rows, ...guidance].filter((record) => activity.allNodes || activityNode(record) === state.selected);
  const key = JSON.stringify([Boolean(activity.snapshot), shown, activity.allNodes]);
  if (activity.feedKey !== key) {
    activity.feedKey = key;
    const nearBottom = ui.activityFeed.scrollHeight - ui.activityFeed.scrollTop - ui.activityFeed.clientHeight < 40;
    const time = (entry) => Date.parse(entry.at || entry.created_at || "") || 0;
    const live = (entry) => entry.state === "running" ? 1 : 0;
    const entries = shown.sort((a, b) => live(a) - live(b) || time(a) - time(b));
    ui.activityFeed.replaceChildren(...(entries.length ? entries.map(chatEntry)
      : [item("li", !activity.snapshot ? "等待运行记录。"
        : activity.allNodes ? "这个课题还没有运行记录或你的意见。" : "这个节点还没有对话。可以点「全部节点」查看整个课题的记录。", "chat-empty")]));
    if (nearBottom) ui.activityFeed.scrollTop = ui.activityFeed.scrollHeight;
  }
  markWork();
  updateGuidanceForm();
}

function resetActivity() {
  stopActivityPoll();
  activity.snapshot = null;
  activity.error = "";
  activity.feedKey = null;
  ui.activityState.textContent = "正在读取运行记录…";
  ui.activityState.dataset.error = "false";
  ui.activityFeed.replaceChildren(item("li", "等待运行记录。", "chat-empty"));
}

function stopActivityPoll() {
  clearTimeout(activity.timer);
  activity.request?.abort();
  activity.request = null;
}

async function pollActivity() {
  if (document.hidden || !state.graph || activity.request) return;
  clearTimeout(activity.timer);
  const claim = state.claimId;
  const request = new AbortController();
  activity.request = request;
  try {
    const reply = await api("/api/activity?claim=" + encodeURIComponent(claim), {signal: request.signal});
    if (activity.request !== request || state.claimId !== claim) return;
    if (reply.claim_id !== claim) throw new Error("返回的记录与当前课题不一致。");
    activity.snapshot = reply;
    activity.error = "";
  } catch (error) {
    if (activity.request === request) activity.error = error.message;
  } finally {
    if (activity.request === request) {
      activity.request = null;
      renderActivity();
      activity.timer = setTimeout(pollActivity, 4000);
    }
  }
}

/* ---------- Guidance ---------- */

function guidanceTarget() {
  const node = state.graph?.nodes.get(state.selected);
  if (!state.claimId || !node) return null;
  const statement = state.graph.statementId;
  return {claim: state.claimId, statement, node, key: [state.claimId, statement, node.id].join("/")};
}

function guidanceChannels() {
  const channels = activity.snapshot?.guidance_channels;
  if (Array.isArray(channels)) return Object.keys(guidanceAudiences).filter((channel) => channels.includes(channel));
  return activity.snapshot?.guidance_available ? ["local"] : [];
}

function guidanceDraft(target) {
  if (!target) return null;
  if (!guidanceDrafts.has(target.key)) guidanceDrafts.set(target.key, {text: ""});
  return guidanceDrafts.get(target.key);
}

function setGuidanceOptions(channels, target) {
  const key = channels.join("/") + "|" + (target?.node.id ?? "");
  if (ui.guidanceAudience.dataset.channels === key) return;
  ui.guidanceAudience.dataset.channels = key;
  if (!channels.length) {
    const unavailable = item("option", "当前不能发送");
    unavailable.value = "";
    ui.guidanceAudience.replaceChildren(unavailable);
    return;
  }
  const name = target ? summarize(nodeTitle(target.node), 14) + " · " : "";
  ui.guidanceAudience.replaceChildren(...channels.map((channel) => {
    const option = item("option", name + guidanceAudiences[channel]);
    option.value = channel;
    return option;
  }));
}

function updateGuidanceForm() {
  const target = guidanceTarget();
  const draft = guidanceDraft(target);
  const channels = guidanceChannels();
  const available = Boolean(target?.statement && channels.length && !activity.error);
  if (Array.isArray(activity.snapshot?.guidance_channels) && draft?.audience && !channels.includes(draft.audience)) {
    draft.audience = null;
    draft.confirmRelease = false;
    draft.requestId = null;
  }
  const audience = draft?.audience ?? (channels.includes("local") ? "local" : channels[0] ?? "");
  const towardsCodex = audience === "codex";
  const hasText = Boolean(draft?.text.trim());
  if (ui.guidanceText.value !== (draft?.text ?? "")) ui.guidanceText.value = draft?.text ?? "";
  ui.guidanceText.disabled = !available || Boolean(draft?.sending);
  setGuidanceOptions(channels, target);
  ui.guidanceAudience.value = audience;
  ui.guidanceAudience.disabled = ui.guidanceText.disabled;
  ui.guidanceScope.textContent = !available ? ""
    : towardsCodex ? "会在下一次获授权的 Codex 调用中发送到外部服务。" : "仅供已授权的本地模型在下次调用中读取。";
  const confirming = Boolean(towardsCodex && draft?.confirmRelease && !draft?.sending);
  ui.guidanceSend.disabled = ui.guidanceText.disabled || !hasText || !channels.includes(audience);
  ui.guidanceSend.dataset.confirm = String(confirming);
  ui.guidanceSend.setAttribute("aria-label", draft?.sending ? "正在发送" : confirming ? "确认发送给 Codex" : "发送意见");
  ui.guidanceSend.replaceChildren(confirming ? document.createTextNode("确认发送")
    : (() => {
      const arrow = svgItem("svg", {viewBox: "0 0 24 24", "aria-hidden": "true"});
      arrow.append(svgItem("path", {d: "M12 19V5"}), svgItem("path", {d: "m5 12 7-7 7 7"}));
      return arrow;
    })());
  const message = !available && activity.snapshot ? activity.snapshot.guidance_reason || "这个课题目前只能查看运行记录。" : "";
  const feedback = confirming ? "这条意见会随公开课题发送给外部服务 Codex。再次点击确认发送。" : draft?.message || message;
  if (ui.guidanceStatus.textContent !== feedback) ui.guidanceStatus.textContent = feedback;
  ui.guidanceStatus.dataset.error = String(Boolean(draft?.error));
  ui.guidanceStatus.dataset.confirm = String(confirming);
}

function saveDraft() {
  const target = guidanceTarget();
  if (!target) return;
  const draft = guidanceDraft(target);
  if (draft.text.trim() !== ui.guidanceText.value.trim()) draft.confirmRelease = false;
  draft.text = ui.guidanceText.value;
  if (draft.text.trim() !== draft.requestText) draft.requestId = null;
  draft.message = "";
  draft.error = false;
  updateGuidanceForm();
}

function selectGuidanceAudience() {
  const draft = guidanceDraft(guidanceTarget());
  if (!draft) return;
  draft.audience = ui.guidanceAudience.value;
  draft.confirmRelease = false;
  draft.requestId = null;
  draft.message = "";
  draft.error = false;
  updateGuidanceForm();
}

async function sendGuidance(event) {
  event.preventDefault();
  const target = guidanceTarget();
  if (!target || ui.guidanceSend.disabled) return;
  const draft = guidanceDraft(target);
  const text = draft.text.trim();
  const audience = ui.guidanceAudience.value;
  const towardsCodex = audience === "codex";
  if (!guidanceChannels().includes(audience)) return;
  if (towardsCodex && !draft.confirmRelease) {
    // Sending to an external service takes a second, explicit click.
    draft.confirmRelease = true;
    updateGuidanceForm();
    return;
  }
  draft.requestText = text;
  draft.requestId ||= crypto.randomUUID();
  draft.sending = true;
  draft.message = "";
  draft.error = false;
  updateGuidanceForm();
  try {
    await api("/api/guidance", {data: {claim_id: target.claim, statement_id: target.statement, node_id: target.node.id,
      text, request_id: draft.requestId,
      ...(Array.isArray(activity.snapshot?.guidance_channels) ? {audience} : {}),
      ...(towardsCodex ? {confirm_release: true} : {})}});
    draft.text = "";
    draft.requestId = null;
    draft.audience = null;
    draft.confirmRelease = false;
    draft.message = towardsCodex ? "意见已记录，等待 Codex 调用。" : "意见已记录，等待本地模型调用。";
    if (state.claimId === target.claim) {
      stopActivityPoll();
      pollActivity();
    }
  } catch (error) {
    draft.message = error.message;
    draft.error = true;
    draft.confirmRelease = false;
  } finally {
    draft.sending = false;
    updateGuidanceForm();
  }
}

/* ---------- Wiring ---------- */

loadSizes();
applySizes();
for (const splitter of document.querySelectorAll(".splitter")) {
  splitter.addEventListener("pointerdown", startResize);
  splitter.addEventListener("keydown", resizeKey);
}
ui.refresh.addEventListener("click", () => {
  ui.moreMenu.hidden = true;
  ui.menuMore.setAttribute("aria-expanded", "false");
  loadInitial();
});
ui.toggleSidebar.addEventListener("click", () => {
  sizes.sidebarOpen = !sidebarOpen();
  applySizes();
  saveSizes();
});
const showReview = () => {
  if (!reviewOpen()) {
    sizes.reviewOpen = true;
    applySizes();
    saveSizes();
  }
};
ui.openReview.addEventListener("click", () => {
  showReview();
  ui.queueTitle.focus?.();
});
ui.closeReview.addEventListener("click", () => {
  sizes.reviewOpen = false;
  applySizes();
  saveSizes();
});
ui.menuReview.addEventListener("click", () => {
  state.reviewFilter = !state.reviewFilter;
  state.panel = "review";
  showReview();
  renderClaims();
  renderQueue();
});
ui.menuLibrary.addEventListener("click", () => {
  state.panel = state.panel === "library" ? "review" : "library";
  showReview();
  renderClaims();
  renderQueue();
});
ui.menuAtlas.addEventListener("click", () => {
  sizes.atlasOpen = !sizes.atlasOpen;
  saveSizes();
  applySizes();
  renderAtlas();
});
ui.menuMore.addEventListener("click", () => {
  ui.moreMenu.hidden = !ui.moreMenu.hidden;
  ui.menuMore.setAttribute("aria-expanded", String(!ui.moreMenu.hidden));
});
document.addEventListener("click", (event) => {
  if (!ui.moreMenu.hidden && !event.target.closest(".more")) {
    ui.moreMenu.hidden = true;
    ui.menuMore.setAttribute("aria-expanded", "false");
  }
});
ui.resetLayout.addEventListener("click", () => {
  Object.assign(sizes, structuredClone(defaultSizes));
  saveSizes();
  applySizes();
  renderAtlas();
  state.cameraMoved = false;
  fitView();
  ui.moreMenu.hidden = true;
  ui.menuMore.setAttribute("aria-expanded", "false");
});
ui.atlasHandle.addEventListener("pointerdown", (event) => dragAtlas(event, "move"));
ui.atlasResize.addEventListener("pointerdown", (event) => dragAtlas(event, "resize"));
ui.atlasResize.addEventListener("keydown", atlasKey);
ui.queue.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-claim-node]");
  if (!button) return;
  const [claim, node] = button.dataset.claimNode.split("|");
  if (claim !== state.claimId) await loadGraph(claim);
  if (state.graph?.nodes.has(node)) selectNode(node, true);
});
window.addEventListener("resize", applySizes);
ui.claimSearch.addEventListener("input", renderClaims);
ui.claimList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-claim]");
  if (button && button.dataset.claim !== state.claimId) loadGraph(button.dataset.claim);
});
ui.tabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (button) setView(button.dataset.view);
});
ui.nodes.addEventListener("click", (event) => {
  const button = event.target.closest("[data-node]");
  if (button) selectNode(button.dataset.node);
});
for (const area of [ui.detail, ui.queue, ui.activityFeed]) {
  area.addEventListener("click", (event) => {
    const button = event.target.closest("[data-select]");
    if (button) selectNode(button.dataset.select, true);
  });
}
ui.stage.addEventListener("wheel", onWheel, {passive: false});
ui.stage.addEventListener("pointerdown", startPan);
ui.stage.addEventListener("keydown", stageKey);
ui.zoomIn.addEventListener("click", () => zoomAt(1.2));
ui.zoomOut.addEventListener("click", () => zoomAt(1 / 1.2));
ui.zoomReset.addEventListener("click", () => zoomAt(1 / state.camera.k));
ui.zoomFit.addEventListener("click", () => { state.cameraMoved = false; fitView(); });
ui.chatScope.addEventListener("click", () => {
  activity.allNodes = !activity.allNodes;
  renderActivity();
});
ui.toggleChat.addEventListener("click", () => {
  sizes.chatOpen = !sizes.chatOpen;
  applySizes();
  saveSizes();
});
ui.guidanceText.addEventListener("input", saveDraft);
ui.guidanceAudience.addEventListener("change", selectGuidanceAudience);
ui.guidanceForm.addEventListener("submit", sendGuidance);
ui.premise.addEventListener("change", () => selectNode(ui.premise.value, true));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopActivityPoll();
  else if (!ui.content.hidden) pollActivity();
});
new ResizeObserver(() => {
  applySizes();
  if (!state.cameraMoved) fitView();
}).observe(ui.stage);
loadInitial();
