const REVIEWER_KEY = "research-multiplier-reviewer";

export function reviewIdentity(data: FormData): { actor: string; reason: string } {
  const actor = String(data.get("actor") ?? "").trim();
  const reason = String(data.get("reason") ?? "").trim();
  try {
    window.localStorage.setItem(REVIEWER_KEY, actor);
  } catch {
    // The name still applies to this decision.
  }
  return { actor, reason };
}

export function savedReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}
