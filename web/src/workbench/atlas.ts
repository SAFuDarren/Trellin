// Radial overview of every claim's dependency tree (the "global position" map).
import type { AtlasClaim } from "./types";

export type AtlasTree = {
  root: string | undefined;
  children: Map<string, string[]>;
  leaves: Map<string, number>;
  depth: number;
};

export function atlasTree(claim: AtlasClaim): AtlasTree {
  const ids = new Set(claim.nodes.map((node) => node.id));
  const sources = new Map<string, string[]>();
  const hasOutgoing = new Set<string>();
  for (const [source, target] of claim.edges) {
    if (!ids.has(source) || !ids.has(target)) continue;
    hasOutgoing.add(source);
    (sources.get(target) ?? sources.set(target, []).get(target)!).push(source);
  }
  const root =
    claim.nodes.find((node) => node.kind === "goal")?.id ?? claim.nodes.find((node) => !hasOutgoing.has(node.id))?.id;
  const children = new Map<string, string[]>();
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
  const leaves = new Map<string, number>();
  const count = (id: string): number => {
    const kids = children.get(id) ?? [];
    const total = kids.length ? kids.reduce((sum, kid) => sum + count(kid), 0) : 1;
    leaves.set(id, total);
    return total;
  };
  let depth = 0;
  const measure = (id: string, level: number) => {
    depth = Math.max(depth, level);
    for (const kid of children.get(id) ?? []) measure(kid, level + 1);
  };
  if (root) {
    count(root);
    measure(root, 0);
  }
  return { root, children, leaves, depth };
}

export type AtlasShape = {
  groups: {
    claimId: string;
    index: number;
    current: boolean;
    lines: [number, number, number, number][];
    dots: [number, number, number][];
  }[];
  here: { x: number; y: number } | null;
  nodeCount: number;
  keys: { claimId: string; index: number; title: string | null; current: boolean }[];
  more: number;
};

export const ATLAS_COLOURS = 5;
// Radii in the 200 × 150 view box, measured from its centre.
const ATLAS_INNER = 10;
const ATLAS_OUTER = 68;
const ATLAS_ROOT_ONLY = 40;

export function atlasShape(claims: AtlasClaim[], currentId: string | null): AtlasShape {
  const trees = claims
    .map((claim, position) => ({ claim, index: position, tree: atlasTree(claim) }))
    .filter(({ tree }) => tree.root);
  const total = trees.reduce((sum, entry) => sum + Math.max(entry.tree.leaves.get(entry.tree.root!) ?? 1, 1), 0);
  const gap = trees.length > 1 ? 6 : 0;
  const center = { x: 100, y: 75 };
  // Claims start on an inner ring and their proof steps fan outwards to OUTER.
  // Without any recorded steps, the claims alone use a wider ring so that they
  // stay apart instead of collapsing into a small circle around the centre.
  const deepest = Math.max(0, ...trees.map(({ tree }) => tree.depth));
  const inner = deepest ? ATLAS_INNER : ATLAS_ROOT_ONLY;
  const step = deepest ? Math.min(18, (ATLAS_OUTER - inner) / deepest) : 0;
  const point = (radius: number, angle: number) => ({
    x: center.x + radius * Math.cos((angle * Math.PI) / 180),
    y: center.y + radius * Math.sin((angle * Math.PI) / 180),
  });
  const firstLeaves = trees[0] ? Math.max(trees[0].tree.leaves.get(trees[0].tree.root!) ?? 1, 1) : 1;
  let angle = -90 - ((360 - gap * trees.length) * firstLeaves) / Math.max(total, 1) / 2;
  let here: { x: number; y: number } | null = null;
  let nodeCount = 0;
  const groups: AtlasShape["groups"] = [];
  for (const { claim, index, tree } of trees) {
    const root = tree.root!;
    const span = ((360 - gap * trees.length) * Math.max(tree.leaves.get(root) ?? 1, 1)) / Math.max(total, 1);
    const current = claim.id === currentId;
    const group: AtlasShape["groups"][number] = { claimId: claim.id, index, current, lines: [], dots: [] };
    const place = (id: string, from: number, to: number, level: number, parent: { x: number; y: number }) => {
      const at = point(inner + level * step, (from + to) / 2);
      group.lines.push([parent.x, parent.y, at.x, at.y]);
      group.dots.push([at.x, at.y, level ? 1.3 : deepest ? 1.8 : 3]);
      nodeCount += 1;
      if (current && level === 1 && !here) here = at;
      let cursor = from;
      for (const kid of tree.children.get(id) ?? []) {
        const share = ((to - from) * (tree.leaves.get(kid) ?? 1)) / (tree.leaves.get(id) ?? 1);
        place(kid, cursor, cursor + share, level + 1, at);
        cursor += share;
      }
    };
    place(root, angle, angle + span, 0, center);
    if (current && !here) here = point(inner, angle + span / 2);
    angle += span + gap;
    groups.push(group);
  }
  const keys = trees
    .slice(0, ATLAS_COLOURS)
    .map(({ claim, index }) => ({ claimId: claim.id, index, title: claim.title, current: claim.id === currentId }));
  return { groups, here, nodeCount, keys, more: Math.max(0, trees.length - ATLAS_COLOURS) };
}
