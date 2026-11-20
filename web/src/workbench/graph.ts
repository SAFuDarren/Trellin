// Graph model, top-down layout, highlights, assumption impact and live work. Pure functions, unit tested.
import type { TFunction } from "i18next";
import type {
  ActivityRecord,
  ActivitySnapshot,
  Blocker,
  GraphEdge,
  GraphNode,
  GraphSnapshot,
  ImpactEntry,
  ImpactPreview,
} from "./types";
import { claimCode, modelName, nodeTitle } from "./text";

export const CARD = { width: 160, height: 72, horizontalGap: 36, verticalGap: 60, pad: 48 };
export type CardSize = { width: number; height: number };

export function clampCardSize({ width, height }: CardSize): CardSize {
  return {
    width: Math.round(Math.max(140, Math.min(540, width))),
    height: Math.round(Math.max(60, Math.min(360, height))),
  };
}

export type Edge = GraphEdge & { key: string; drawKind: "actual" | "planned" | "context" };

export type Graph = {
  claim: GraphSnapshot["claim"];
  nodes: Map<string, GraphNode>;
  order: string[];
  edges: Edge[];
  incoming: Map<string, Edge[]>;
  outgoing: Map<string, Edge[]>;
  levels: string[][];
  blockers: Map<string, Blocker[]>;
  hypotheses: GraphNode[];
  evidenceAliases: Map<string, string>;
  statementId: string | null;
  /** Durable codes from the server, or temporary labels on an older database. */
  codes: Map<string, string>;
  focusId: string | null;
};

function index(edges: Edge[]) {
  const incoming = new Map<string, Edge[]>();
  const outgoing = new Map<string, Edge[]>();
  for (const edge of edges) {
    (incoming.get(edge.target) ?? incoming.set(edge.target, []).get(edge.target)!).push(edge);
    (outgoing.get(edge.source) ?? outgoing.set(edge.source, []).get(edge.source)!).push(edge);
  }
  return { incoming, outgoing };
}

function rankLevels(order: string[], edges: Edge[], outgoing: Map<string, Edge[]>): string[][] {
  const indegree = new Map(order.map((id) => [id, 0]));
  const depth = new Map(order.map((id) => [id, 0]));
  for (const edge of edges) indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  const queue = order.filter((id) => indegree.get(id) === 0);
  for (const id of queue) {
    for (const edge of outgoing.get(id) ?? []) {
      indegree.set(edge.target, (indegree.get(edge.target) ?? 0) - 1);
      depth.set(edge.target, Math.max(depth.get(edge.target) ?? 0, (depth.get(id) ?? 0) + 1));
      if (indegree.get(edge.target) === 0) queue.push(edge.target);
    }
  }
  const levels: string[][] = [];
  for (const id of order) (levels[depth.get(id) ?? 0] ??= []).push(id);
  return levels.filter(Boolean);
}

function arrangeLevels(levels: string[][], incoming: Map<string, Edge[]>, outgoing: Map<string, Edge[]>): void {
  const rows = new Map(
    levels.flatMap((group) => group.map((id, row) => [id, row / Math.max(1, group.length - 1)] as const)),
  );
  // Use earlier neighbours to order a level and later neighbours to tidy the preceding one.
  const passes: [string[][], Map<string, Edge[]>, "source" | "target"][] = [
    [levels, incoming, "source"],
    [[...levels].reverse(), outgoing, "target"],
  ];
  for (const [groups, neighbours, endpoint] of passes) {
    for (const group of groups) {
      const averages = new Map(
        group.map((id) => {
          const list = neighbours.get(id) ?? [];
          const mean = list.length
            ? list.reduce((sum, edge) => sum + (rows.get(edge[endpoint]) ?? 0), 0) / list.length
            : (rows.get(id) ?? 0);
          return [id, mean] as const;
        }),
      );
      group.sort((a, b) => (averages.get(a) ?? 0) - (averages.get(b) ?? 0));
      group.forEach((id, row) => rows.set(id, row / Math.max(1, group.length - 1)));
    }
  }
}

export function buildGraph(t: TFunction, snapshot: GraphSnapshot): Graph {
  const ordered = [...snapshot.nodes].sort(
    (a, b) => nodeTitle(t, a).localeCompare(nodeTitle(t, b)) || a.id.localeCompare(b.id),
  );
  const nodes = new Map(ordered.map((node) => [node.id, node]));
  const evidenceAliases = new Map(
    ordered
      .filter((node) => node.reference?.evidence_id)
      .map((node) => ["evidence:" + node.reference!.evidence_id, node.id] as const),
  );
  const blockers = new Map<string, Blocker[]>();
  for (const blocker of snapshot.blockers ?? [])
    (blockers.get(blocker.node_id) ?? blockers.set(blocker.node_id, []).get(blocker.node_id)!).push(blocker);
  const edges: Edge[] = snapshot.edges
    .filter((edge) => nodes.has(edge.source) && nodes.has(edge.target) && edge.source !== edge.target)
    .map((edge, position) => ({
      ...edge,
      key: `${edge.source}>${edge.target}>${edge.kind}>${position}`,
      drawKind: edge.kind === "scope" ? "context" : edge.kind === "planned" ? "planned" : "actual",
    }));
  const { incoming, outgoing } = index(edges);
  const order = ordered.map((node) => node.id);
  const levels = rankLevels(order, edges, outgoing);
  arrangeLevels(levels, incoming, outgoing);
  return {
    claim: snapshot.claim,
    nodes,
    order,
    edges,
    incoming,
    outgoing,
    levels,
    blockers,
    hypotheses: ordered.filter((node) => node.kind === "hypothesis" && node.reference?.basis_version_id),
    evidenceAliases,
    statementId: snapshot.claim.statement_id ?? null,
    focusId: snapshot.focus_id ?? null,
    codes: nodeCodes(snapshot.claim, levels, nodes),
  };
}

function nodeCodes(claim: GraphSnapshot["claim"], levels: string[][], nodes: Map<string, GraphNode>) {
  const codes = new Map<string, string>();
  if (claim.code) {
    for (const node of nodes.values()) {
      if (node.code) codes.set(node.id, node.code);
    }
    return codes;
  }
  const prefix = claimCode(claim);
  [...levels]
    .reverse()
    .flat()
    .forEach((id, position) => {
      codes.set(id, nodes.get(id)?.code || `${prefix}-${String(position + 1).padStart(2, "0")}`);
    });
  return codes;
}

export type Point = { x: number; y: number };
export type Layout = { points: Map<string, Point>; width: number; height: number; paths: Map<string, string> };

/** Dependencies sit below what uses them. Every rank reserves room for its tallest and widest cards. */
export function layoutGraph(graph: Graph, sizes: ReadonlyMap<string, CardSize>): Layout {
  const sizeOf = (id: string) => sizes.get(id) ?? CARD;
  const rows = graph.levels
    .slice()
    .reverse()
    .map((nodes) => ({
      nodes,
      width:
        nodes.reduce((total, id) => total + sizeOf(id).width, 0) + CARD.horizontalGap * Math.max(0, nodes.length - 1),
      height: nodes.reduce((tallest, id) => Math.max(tallest, sizeOf(id).height), 0),
    }));
  const contentWidth = rows.reduce((widest, row) => Math.max(widest, row.width), CARD.width);
  const width = contentWidth + CARD.pad * 2;
  const points = new Map<string, Point>();
  let y = CARD.pad;
  rows.forEach((row, position) => {
    let x = CARD.pad + (contentWidth - row.width) / 2;
    for (const id of row.nodes) {
      points.set(id, { x, y });
      x += sizeOf(id).width + CARD.horizontalGap;
    }
    y += row.height + (position < rows.length - 1 ? CARD.verticalGap : 0);
  });
  const paths = new Map<string, string>();
  for (const edge of graph.edges) {
    const a = points.get(edge.source);
    const b = points.get(edge.target);
    if (!a || !b) continue;
    const ax = a.x + sizeOf(edge.source).width / 2;
    const bx = b.x + sizeOf(edge.target).width / 2;
    const top = a.y;
    const bottom = b.y + sizeOf(edge.target).height;
    const middle = (top + bottom) / 2;
    paths.set(edge.key, `M ${ax} ${top} C ${ax} ${middle}, ${bx} ${middle}, ${bx} ${bottom}`);
  }
  return { points, width, height: y + CARD.pad, paths };
}

function traceEdges(first: string, edgeIndex: Map<string, Edge[]>, endpoint: "source" | "target"): Set<Edge> {
  const reached = new Set([first]);
  const traversed = new Set<Edge>();
  for (const id of reached) {
    for (const edge of edgeIndex.get(id) ?? []) {
      traversed.add(edge);
      reached.add(edge[endpoint]);
    }
  }
  return traversed;
}

export function impactItems(graph: Graph, impact: ImpactPreview | null | undefined): Map<string, ImpactEntry> {
  const results = new Map<string, ImpactEntry>();
  for (const group of [impact?.affected, impact?.scope]) {
    for (const entry of group ?? []) {
      const id = graph.evidenceAliases.get(entry.id) ?? entry.id;
      if (!results.has(id)) results.set(id, entry);
    }
  }
  return results;
}

/** Which impact label applies: the key under `impact.*`. */
export function impactLineKey(effect: ImpactEntry): string {
  if (effect.relation) return effect.status === "needs_recheck" ? "impact.alreadyRecheck" : "impact.needsRecheck";
  return effect.kind === "obligation" ? "impact.waitingReduction" : "impact.scopeConfirm";
}

export type View = "proof" | "references" | "impact";

export function highlights(
  graph: Graph,
  view: View,
  selected: string | null,
  assumption: string | null,
  affected: Map<string, ImpactEntry>,
): { nodes: Set<string>; edges: Set<Edge> } {
  const nodes = new Set<string>(selected ? [selected] : []);
  const edges = new Set<Edge>();
  if (view === "impact") {
    if (assumption) nodes.add(assumption);
    for (const id of affected.keys()) nodes.add(id);
    for (const edge of graph.edges) if (nodes.has(edge.source) && nodes.has(edge.target)) edges.add(edge);
    return { nodes, edges };
  }
  if (selected) {
    for (const edge of traceEdges(selected, graph.incoming, "source")) edges.add(edge);
    for (const edge of traceEdges(selected, graph.outgoing, "target")) edges.add(edge);
    for (const edge of graph.outgoing.get(selected) ?? []) {
      if (edge.kind === "planned" && graph.nodes.get(edge.target)?.kind === "reduction") {
        for (const upstream of traceEdges(edge.target, graph.incoming, "source")) edges.add(upstream);
      }
    }
  }
  for (const edge of graph.edges) {
    const a = graph.nodes.get(edge.source)?.domain;
    const b = graph.nodes.get(edge.target)?.domain;
    if (view === "references" && edge.kind === "actual" && a && b && a !== b) edges.add(edge);
  }
  for (const edge of edges) {
    nodes.add(edge.source);
    nodes.add(edge.target);
  }
  return { nodes, edges };
}

export function activityNode(
  graph: Graph | null,
  record: { node_id?: string; fallback_node_id?: string },
): string | null {
  if (!graph) return null;
  for (const id of [record.node_id, record.fallback_node_id]) {
    if (!id) continue;
    const graphId = graph.evidenceAliases.get(id) ?? id;
    if (graph.nodes.has(graphId)) return graphId;
  }
  return null;
}

export type NodeWork = { running: Map<string, number>; queued: Map<string, number> };

export function liveWork(t: TFunction, graph: Graph | null, activity: ActivitySnapshot | undefined, failed: boolean) {
  const byNode = new Map<string, NodeWork>();
  let running = 0;
  let queued = 0;
  for (const record of failed ? [] : (activity?.activity ?? [])) {
    if (!["running", "queued", "waiting_retry"].includes(record.state)) continue;
    const node = activityNode(graph, record);
    if (!node) continue;
    const entry = byNode.get(node) ?? byNode.set(node, { running: new Map(), queued: new Map() }).get(node)!;
    const bucket = record.state === "running" ? entry.running : entry.queued;
    const name = workName(t, record);
    bucket.set(name, (bucket.get(name) ?? 0) + 1);
    if (record.state === "running") running += 1;
    else queued += 1;
  }
  return { byNode, running, queued };
}

function workName(t: TFunction, record: ActivityRecord): string {
  return record.kind === "model"
    ? modelName(t, record)
    : t(`activityKind.${record.kind}`, { defaultValue: t("activityKind.task") });
}

export function workLabel(counts: Map<string, number>, separator: string): string {
  return [...counts].map(([name, count]) => (count > 1 ? `${name} ×${count}` : name)).join(separator);
}
