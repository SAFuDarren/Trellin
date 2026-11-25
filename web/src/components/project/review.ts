import { useInbox } from "../../api/sections";

const REVIEWER_KEY = "research-multiplier-reviewer";

export function reviewIdentity(data: FormData): { actor: string; reason: string } {
  const actor = String(data.get("actor") ?? "").trim();
  const reason = String(data.get("reason") ?? "").trim();
  rememberReviewer(actor);
  return { actor, reason };
}

export function rememberReviewer(actor: string) {
  try {
    window.localStorage.setItem(REVIEWER_KEY, actor);
  } catch {
    // The name still applies to this decision.
  }
}

export function savedReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

/**
 * #328: the researcher the project database signs decisions as. When it is known, forms show it
 * instead of asking for a name; databases without a local identity still ask (#10).
 */
export function useReviewSigner(session: string): string | undefined {
  return useInbox(session).data?.review_identity?.display_name || undefined;
}

/** A formal statement as the researcher reads it: the Lean proposition alone, or the recorded form in full. */
export function formalText(value: Record<string, unknown> | null | undefined): string {
  if (!value) return "";
  if (Object.keys(value).length === 1) {
    const lean = value.lean as { proposition?: unknown } | undefined;
    if (typeof lean?.proposition === "string" && Object.keys(lean).length === 1) return lean.proposition;
  }
  return JSON.stringify(value, null, 2);
}
