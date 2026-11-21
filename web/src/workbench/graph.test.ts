import { describe, expect, it } from "vitest";
import i18n from "../i18n";
import { buildGraph, CARD, highlights, impactItems, layoutGraph, liveWork } from "./graph";
import { atlasShape } from "./atlas";
import { claimCode, modelName, nodeTitle, nodeTone, normalizeReferenceCode, prettyModel } from "./text";
import type { GraphSnapshot } from "./types";

const t = i18n.getFixedT("en");
const CLAIM = "11111111-1111-4111-8111-111111111111";
const snapshot: GraphSnapshot = {
  claim: { id: CLAIM, title: "Zero symmetry", status: "open", domain: "lean-core", statement_id: "s1" },
  focus_id: "lean:e",
  nodes: [
    { id: "goal:c", kind: "goal", title: "Original claim", status: "open" },
    {
      id: "item:h",
      kind: "hypothesis",
      title: "Assumption H1",
      status: "confirmed",
      reference: { basis_version_id: "h1" },
    },
    { id: "local-lemma:r", kind: "reduction", title: "Checked reduction 1", status: "planned" },
    { id: "obligation:r:1", kind: "obligation", title: "Proof obligation 1", status: "verified" },
    { id: "obligation:r:2", kind: "obligation", title: "Proof obligation 2", status: "open" },
    {
      id: "lean:e",
      kind: "evidence",
      status: "awaiting_review",
      citation: "FirstProof.reverseZeroAdd",
      reference: { evidence_id: "e1" },
    },
  ],
  edges: [
    { source: "lean:e", target: "goal:c", kind: "actual" },
    { source: "local-lemma:r", target: "goal:c", kind: "planned" },
    { source: "obligation:r:1", target: "local-lemma:r", kind: "planned" },
    { source: "obligation:r:2", target: "local-lemma:r", kind: "planned" },
    { source: "item:h", target: "lean:e", kind: "actual" },
    { source: "item:h", target: "obligation:r:1", kind: "actual" },
    { source: "item:h", target: "item:h", kind: "actual" },
  ],
  blockers: [{ node_id: "obligation:r:2", reason: "cannot derive" }],
};

describe("graph model", () => {
  const graph = buildGraph(snapshot);

  it("drops self-loops and ranks dependencies below what uses them", () => {
    expect(graph.edges).toHaveLength(6);
    expect(graph.levels[0]).toEqual(
      ["item:h", "obligation:r:2"].sort((a, b) => graph.levels[0]!.indexOf(a) - graph.levels[0]!.indexOf(b)),
    );
    expect(graph.levels.at(-1)).toEqual(["goal:c"]);
    const layout = layoutGraph(graph, new Map());
    expect(layout.points.get("goal:c")!.y).toBe(CARD.pad);
    expect(layout.points.get("item:h")!.y).toBeGreaterThan(layout.points.get("lean:e")!.y);
    expect(layout.paths.size).toBe(6);
  });

  it("spaces different card sizes and connects the centres of their opposing edges", () => {
    const sizes = new Map([
      ["goal:c", { width: 290, height: 180 }],
      ["lean:e", { width: 225, height: 100 }],
    ]);
    const layout = layoutGraph(graph, sizes);
    const root = layout.points.get("goal:c")!;
    const child = layout.points.get("lean:e")!;
    const firstDependency = layout.points.get(graph.levels.at(-2)![0]!)!;
    expect(firstDependency.y).toBe(root.y + 180 + CARD.verticalGap);
    expect(layout.width).toBeGreaterThanOrEqual(root.x + 290 + CARD.pad);
    for (const nodes of graph.levels) {
      for (let i = 1; i < nodes.length; i += 1) {
        const previous = nodes[i - 1]!;
        const next = nodes[i]!;
        expect(layout.points.get(next)!.x - layout.points.get(previous)!.x).toBe(
          (sizes.get(previous)?.width ?? CARD.width) + CARD.horizontalGap,
        );
      }
    }
    const edge = graph.edges.find(({ source, target }) => source === "lean:e" && target === "goal:c")!;
    expect(layout.paths.get(edge.key)).toMatch(
      new RegExp(`^M ${child.x + 112.5} ${child.y} C .* ${root.x + 145} ${root.y + 180}$`),
    );
  });

  it("names numbered nodes and evidence in the current language", () => {
    expect(nodeTitle(t, graph.nodes.get("obligation:r:1")!)).toBe("Proof obligation 1");
    expect(nodeTitle(i18n.getFixedT("zh-CN"), graph.nodes.get("obligation:r:1")!)).toBe("证明义务 1");
    expect(nodeTitle(t, graph.nodes.get("lean:e")!)).toBe("reverseZeroAdd");
  });

  it("keeps the same layout and codes when the visible titles change language", () => {
    const obligation = "obligation:r:1";
    const evidence = "lean:e";
    const siblingSnapshot: GraphSnapshot = {
      ...snapshot,
      nodes: [
        { ...snapshot.nodes[0]!, code: "ZS-12" },
        snapshot.nodes.find(({ id }) => id === obligation)!,
        snapshot.nodes.find(({ id }) => id === evidence)!,
      ],
      edges: [
        { source: obligation, target: "goal:c", kind: "planned" },
        { source: evidence, target: "goal:c", kind: "actual" },
      ],
    };
    const sizes = new Map([[obligation, { width: 300, height: 96 }]]);
    const describeGraph = (language: "en" | "zh-CN") => {
      const translate = i18n.getFixedT(language);
      const graph = buildGraph(siblingSnapshot);
      return {
        title: nodeTitle(translate, graph.nodes.get(obligation)!),
        order: graph.order,
        layout: layoutGraph(graph, sizes),
        codes: graph.codes,
      };
    };
    const en = describeGraph("en");
    const zh = describeGraph("zh-CN");

    expect(en.title).toBe("Proof obligation 1");
    expect(zh.title).toBe("证明义务 1");
    expect(zh.order).toEqual(en.order);
    expect(zh.layout).toEqual(en.layout);
    expect(zh.codes).toEqual(en.codes);
    expect(en.codes.get("goal:c")).toBe("ZS-12");
    expect(en.codes.get(obligation)).toBe("ZS-02");
    expect(en.codes.get(evidence)).toBe("ZS-03");
  });

  it("gives tones that match the review states", () => {
    expect(nodeTone(graph.nodes.get("lean:e")!, false)).toBe("review");
    expect(nodeTone(graph.nodes.get("item:h")!, false)).toBe("");
    expect(nodeTone(graph.nodes.get("obligation:r:2")!, true)).toBe("bad");
    expect(nodeTone(graph.nodes.get("goal:c")!, false)).toBe("open");
  });

  it("highlights the selected node's dependency chain and the reduction it feeds", () => {
    const lit = highlights(graph, "proof", "obligation:r:1", null, new Map());
    expect([...lit.nodes].sort()).toEqual(
      ["goal:c", "item:h", "local-lemma:r", "obligation:r:1", "obligation:r:2"].sort(),
    );
  });

  it("maps impact entries through evidence aliases", () => {
    const affected = impactItems(graph, { affected: [{ id: "evidence:e1", kind: "evidence", relation: "direct" }] });
    expect([...affected.keys()]).toEqual(["lean:e"]);
    const lit = highlights(graph, "impact", "item:h", "item:h", affected);
    expect(lit.nodes.has("lean:e")).toBe(true);
    expect([...lit.edges].map((edge) => edge.key)).toHaveLength(1);
  });

  it("counts live work per node with recorded harness and model names", () => {
    const work = liveWork(
      t,
      graph,
      {
        claim_id: CLAIM,
        activity: [
          {
            id: "1",
            kind: "model",
            state: "running",
            node_id: "obligation:r:2",
            provider: "codex-cli",
            model: "gpt-5-codex",
          },
          {
            id: "2",
            kind: "model",
            state: "running",
            node_id: "obligation:r:2",
            provider: "codex-cli",
            model: "gpt-5-codex",
          },
          { id: "3", kind: "lean", state: "queued", node_id: "missing", fallback_node_id: "goal:c" },
          { id: "4", kind: "lean", state: "succeeded", node_id: "goal:c" },
        ],
      },
      false,
    );
    expect(work.running).toBe(2);
    expect(work.queued).toBe(1);
    expect(work.byNode.get("obligation:r:2")!.running.get("Codex · GPT-5 Codex")).toBe(2);
    expect(work.byNode.get("goal:c")!.queued.get("Lean check")).toBe(1);
  });
});

describe("atlas", () => {
  it("places every claim's nodes and marks the current claim", () => {
    const shape = atlasShape(
      [
        {
          id: "a",
          title: "A",
          domain: null,
          status: "open",
          nodes: [
            { id: "g", kind: "goal" },
            { id: "x", kind: "local_lemma" },
          ],
          edges: [["x", "g"]],
        },
        { id: "b", title: "B", domain: null, status: "open", nodes: [{ id: "g2", kind: "goal" }], edges: [] },
      ],
      "a",
    );
    expect(shape.nodeCount).toBe(3);
    expect(shape.groups.find((group) => group.claimId === "a")!.current).toBe(true);
    expect(shape.here).not.toBeNull();
    expect(shape.keys.map((key) => key.claimId)).toEqual(["a", "b"]);
  });

  it.each([1, 12])("keeps %i root-only claims distinguishable", (count) => {
    const claims = Array.from({ length: count }, (_, index) => ({
      id: `c${index}`,
      title: `C${index}`,
      domain: null,
      status: "open",
      nodes: [{ id: `g${index}`, kind: "goal" }],
      edges: [] as [string, string][],
    }));
    const roots = atlasShape(claims, "c0").groups.map((group) => group.dots[0]!);
    expect(roots).toHaveLength(count);
    for (const [x, y] of roots) expect(Math.hypot(x - 100, y - 75)).toBeGreaterThanOrEqual(30);
    for (const [index, [x, y, r]] of roots.entries()) {
      for (const [ox, oy, or] of roots.slice(index + 1))
        expect(Math.hypot(x - ox, y - oy)).toBeGreaterThan(3 * (r + or));
    }
  });

  it("spreads root-only claims apart instead of drawing a small ring (#154)", () => {
    const claims = Array.from({ length: 3 }, (_, index) => ({
      id: `c${index}`,
      title: `C${index}`,
      domain: null,
      status: "open",
      nodes: [{ id: `g${index}`, kind: "goal" }],
      edges: [] as [string, string][],
    }));
    const shape = atlasShape(claims, "c1");
    const roots = shape.groups.map((group) => group.dots[0]!);
    const centre = { x: 100, y: 75 };
    for (const [x, y] of roots) expect(Math.hypot(x - centre.x, y - centre.y)).toBeGreaterThanOrEqual(30);
    for (const [index, [x, y]] of roots.entries()) {
      for (const [ox, oy] of roots.slice(index + 1)) expect(Math.hypot(x - ox, y - oy)).toBeGreaterThan(40);
    }
    // The dashed marker surrounds the selected claim's own dot.
    const [sx, sy] = roots[1]!;
    expect(Math.hypot(shape.here!.x - sx, shape.here!.y - sy)).toBeLessThan(0.01);
  });

  it("keeps the established radii once a claim has recorded steps", () => {
    const shape = atlasShape(
      [
        {
          id: "a",
          title: "A",
          domain: null,
          status: "open",
          nodes: [
            { id: "g", kind: "goal" },
            { id: "x", kind: "local_lemma" },
          ],
          edges: [["x", "g"]],
        },
        { id: "b", title: "B", domain: null, status: "open", nodes: [{ id: "g2", kind: "goal" }], edges: [] },
      ],
      "a",
    );
    const radius = ([x, y]: number[]) => Math.hypot(x! - 100, y! - 75);
    const [root, step] = shape.groups[0]!.dots;
    expect(radius(root!)).toBeCloseTo(10);
    expect(radius(step!)).toBeCloseTo(28);
  });
});

describe("codes and agent names", () => {
  it("numbers nodes from the claim down, with the claim's initials", () => {
    const graph = buildGraph(snapshot);
    expect(graph.codes.get("goal:c")).toBe("ZS-01");
    expect(new Set(graph.codes.values()).size).toBe(snapshot.nodes.length);
  });

  it("uses only durable node codes when the claim has a server code", () => {
    const graph = buildGraph({
      ...snapshot,
      claim: { ...snapshot.claim, code: "FLT" },
      nodes: snapshot.nodes.map((node) => (node.id === "goal:c" ? { ...node, code: "FLT-00" } : node)),
    });
    expect(graph.codes.get("goal:c")).toBe("FLT-00");
    expect(graph.codes.has("local-lemma:r")).toBe(false);
  });

  it("derives claim codes from titles, with a fallback for titles without Latin words", () => {
    expect(claimCode({ id: "x", title: "Fermat's Last Theorem" })).toBe("FLT");
    expect(claimCode({ id: "x", title: "Sums of two squares" })).toBe("STS");
    expect(claimCode({ id: "abcdef12-0000", title: "自然数加零的对称性" })).toBe("CABC");
  });

  it("does not expand a non-ASCII letter into a valid reference code", () => {
    expect(normalizeReferenceCode(" flow-09 ")).toBe("FLOW-09");
    expect(normalizeReferenceCode("ß-09")).toBe("ß-09");
  });

  it("names agents by model and effort", () => {
    expect(prettyModel("gpt-6.2-sol")).toBe("GPT-6.2 Sol");
    expect(prettyModel("gpt-5-codex")).toBe("GPT-5 Codex");
    expect(prettyModel("claude-opus-5-5")).toBe("Claude Opus 5.5");
    expect(prettyModel("Qwen 3 Max")).toBe("Qwen 3 Max");
    expect(modelName(t, { provider: "codex-cli", model: "gpt-6.2-sol", effort: "xhigh" })).toBe("GPT-6.2 Sol xhigh");
    expect(modelName(t, { provider: "codex-cli" })).toBe("Codex");
  });
});
