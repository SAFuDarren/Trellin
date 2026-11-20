// Shapes returned by the local workbench API (docs/interfaces.md). Fields not listed are ignored.

export type Reference = Partial<Record<string, string | number>> & {
  basis_version_id?: string;
  evidence_id?: string;
  condition_id?: string;
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
  formalization_status?: "approved" | "pending";
  statement?: string;
  verification_status?: string;
  parameters?: { name: string; type: string }[];
  reference?: Reference;
};

export type GraphEdge = { source: string; target: string; kind: string };
export type Blocker = { node_id: string; kind?: string; reason?: string; detail?: string; status?: string };

export type ProposedPremise = {
  id: string;
  sha256: string;
  wording: string;
  lean: string;
  question: string;
  impact: string;
  state: "pending" | "rejected" | "revised" | "resolved" | "revision_pending";
  reason?: string;
  revised_claim_id?: string;
};

export type PremiseReview = {
  available: boolean;
  frame_sha256: string;
  target: { wording: string; lean: string };
  basis: { id: string; kind: string; wording: string; lean?: string }[];
  proposals: ProposedPremise[];
};

export type GraphSnapshot = {
  claim: {
    id: string;
    title: string | null;
    status: string;
    domain: string | null;
    statement_id?: string | null;
    lemma_plan_ready?: boolean;
    premise_review?: PremiseReview;
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
  current_scope?: boolean;
};
export type GuidanceNote = {
  id: string;
  node_id: string;
  text: string;
  created_at: string;
  submitted_at?: string | null;
  audience?: string;
  current_scope?: boolean;
};
export type ProofDraft = {
  revision: string;
  text: string;
  updated_at: string;
};
export type ActivitySnapshot = {
  claim_id: string;
  activity: ActivityRecord[];
  guidance?: GuidanceNote[];
  guidance_channels?: string[];
  guidance_available?: boolean;
  guidance_reason?: string;
  guidance_blocker?: string | null;
  proof_draft_available?: boolean;
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
