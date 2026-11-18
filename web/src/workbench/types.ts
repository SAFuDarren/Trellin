// Shapes returned by the local workbench API (docs/interfaces.md). Fields not listed are ignored.

export type Reference = Partial<Record<string, string | number>> & {
  basis_version_id?: string;
  evidence_id?: string;
};

export type GraphNode = {
  id: string;
  /** Short display code such as FLT-03; derived in the interface when the server sends none. */
  code?: string;
  kind: string;
  title?: string;
  status?: string;
  domain?: string | null;
  citation?: string;
  formula?: string;
  proposition?: string;
  formalization?: string;
  statement?: string;
  verification_status?: string;
  parameters?: { name: string; type: string }[];
  reference?: Reference;
};

export type GraphEdge = { source: string; target: string; kind: string };
export type Blocker = { node_id: string; kind?: string; reason?: string; detail?: string; status?: string };

export type GraphSnapshot = {
  claim: {
    id: string;
    title: string | null;
    status: string;
    domain: string | null;
    statement_id?: string | null;
    code?: string | null;
  };
  focus_id?: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  blockers?: Blocker[];
};

export type ImpactEntry = {
  id: string;
  kind: string;
  relation?: string;
  status?: string;
  title?: string;
  label?: string;
  claim_id?: string;
  claim_title?: string;
};
export type ImpactPreview = { affected?: ImpactEntry[]; scope?: ImpactEntry[] };

export type ActivityRecord = {
  id: string;
  kind: string;
  state: string;
  text?: string;
  at?: string | null;
  created_at?: string;
  submitted_at?: string | null;
  node_id?: string;
  fallback_node_id?: string;
  provider?: string;
  model_provider?: string;
  model?: string;
  /** Reasoning effort the agent ran with, for example "xhigh". */
  effort?: string;
  audience?: string;
};
export type GuidanceNote = {
  id: string;
  node_id: string;
  text: string;
  created_at: string;
  submitted_at?: string | null;
  audience?: string;
};
export type ActivitySnapshot = {
  claim_id: string;
  activity: ActivityRecord[];
  guidance?: GuidanceNote[];
  guidance_channels?: string[];
  guidance_available?: boolean;
  guidance_reason?: string;
};

export type AtlasClaim = {
  id: string;
  title: string | null;
  domain: string | null;
  status: string;
  nodes: { id: string; kind: string; status?: string; title?: string }[];
  edges: [string, string][];
  awaiting_review?: number;
  blocked?: number;
};
export type Atlas = { claims: AtlasClaim[] };
