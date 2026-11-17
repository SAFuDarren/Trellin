import { describe, expect, it } from "vitest";
import i18n from "../i18n";
import { buildGraph, CARD, highlights, impactItems, layoutGraph, liveWork } from "./graph";
import { atlasShape } from "./atlas";
import { nodeTitle, nodeTone } from "./text";
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
  const graph = buildGraph(t, snapshot);

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

  it("names numbered nodes and evidence in the current language", () => {
    expect(nodeTitle(t, graph.nodes.get("obligation:r:1")!)).toBe("Proof obligation 1");
    expect(nodeTitle(i18n.getFixedT("zh-CN"), graph.nodes.get("obligation:r:1")!)).toBe("证明义务 1");
    expect(nodeTitle(t, graph.nodes.get("lean:e")!)).toBe("reverseZeroAdd");
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

  it("counts live work per node with model names", () => {
    const work = liveWork(
      t,
      graph,
      {
        claim_id: CLAIM,
        activity: [
          { id: "1", kind: "model", state: "running", node_id: "obligation:r:2", model: "gpt-5-codex" },
          { id: "2", kind: "model", state: "running", node_id: "obligation:r:2", model: "gpt-5-codex" },
          { id: "3", kind: "lean", state: "queued", node_id: "missing", fallback_node_id: "goal:c" },
          { id: "4", kind: "lean", state: "succeeded", node_id: "goal:c" },
        ],
      },
      false,
    );
    expect(work.running).toBe(2);
    expect(work.queued).toBe(1);
    expect(work.byNode.get("obligation:r:2")!.running.get("gpt-5-codex")).toBe(2);
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
});
