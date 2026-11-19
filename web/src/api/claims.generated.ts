// Generated from workbench.openapi.json by pnpm gen:api.

export type Claim = {
  id: string;
  title: string | null;
  status: string;
  domain: string | null;
};

export type ClaimsList = {
  items: Array<Claim>;
  next_cursor: null;
  claim_id: string | null;
  claim_codes?: boolean;
};
