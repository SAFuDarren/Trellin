// Human-readable names and tones for graph records, through the message catalogs.
import type { TFunction } from "i18next";
import type { GraphNode } from "./types";

export type Tone = "ok" | "review" | "bad" | "open" | "planned" | "";

const TONES: [Exclude<Tone, "">, string[]][] = [
  ["ok", ["verified", "proved", "proved_goal", "approved", "route_checked", "lemma_verified"]],
  ["review", ["awaiting_review", "awaiting_statement_review"]],
  ["bad", ["needs_recheck", "blocked", "failed", "rejected", "returned", "stale"]],
  ["open", ["open", "unsolved", "pending", "ready"]],
  ["planned", ["planned", "awaiting_obligations", "waiting_for_theorems", "waiting_for_reduction"]],
];

const UUID_TITLE = /^(?:[^:]+:)?[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i;

export function summarize(value: string, limit: number): string {
  const summary = String(value).trim().replace(/\s+/g, " ");
  return summary.length > limit ? summary.slice(0, limit).trimEnd() + " …" : summary;
}

export function statusText(t: TFunction, value: string | undefined): string {
  if (!value) return "";
  return t(`status.${value}`, { defaultValue: value });
}

export function kindText(t: TFunction, kind: string): string {
  return t(`kind.${kind}`, { defaultValue: kind });
}

export function nodeStatus(t: TFunction, node: GraphNode): string {
  if (["evidence", "proposed_premise"].includes(node.kind) && node.status === "pending")
    return statusText(t, "awaiting_review");
  if (node.kind === "hypothesis" && node.status === "approved") return statusText(t, "confirmed");
  return statusText(t, node.status);
}

export function nodeTitle(t: TFunction, node: GraphNode, { full = false } = {}): string {
  const raw = typeof node.title === "string" ? node.title : "";
  const numbered = raw.match(/^(Proof obligation|Checked reduction|Lean lemma) (\d+)$/);
  const title = numbered
    ? t(
        numbered[1] === "Proof obligation"
          ? "node.obligation"
          : numbered[1] === "Checked reduction"
            ? "node.reduction"
            : "node.lemma",
        {
          n: numbered[2],
        },
      )
    : raw;
  if (node.kind === "evidence" && typeof node.citation === "string" && node.citation)
    return node.citation.split(".").pop() ?? node.citation;
  if (node.kind === "evidence") return kindText(t, "evidence");
  if (node.kind === "library" && node.citation) return node.citation;
  if (title && !UUID_TITLE.test(title) && (full || title.length <= 60)) return title;
  const formula = node.proposition || (node.kind === "hypothesis" ? node.formalization : undefined);
  if (typeof formula === "string" && formula.length <= 44 && !formula.includes("\n")) return formula;
  return t(`kind.${node.kind}`, { defaultValue: t("kind.record") });
}

export function nodeFormula(node: GraphNode): string {
  for (const value of [node.formula, node.proposition, node.formalization]) {
    if (typeof value === "string" && value.trim() && !value.includes("\n") && value.length <= 120) return value.trim();
  }
  return "";
}

export function nodeSub(t: TFunction, node: GraphNode, title = nodeTitle(t, node)): { text: string; math: boolean } {
  if (node.kind === "evidence") {
    const id = node.reference?.evidence_id;
    return { text: kindText(t, "evidence") + (id ? " · " + String(id).slice(0, 8) : ""), math: false };
  }
  const formula = nodeFormula(node);
  if (formula && formula !== title) return { text: summarize(formula, 44), math: true };
  return { text: t(`kind.${node.kind}`, { defaultValue: "" }), math: false };
}

export function nodeTone(node: GraphNode, blocked: boolean): Tone {
  if (blocked) return "bad";
  if (
    ["evidence", "proposed_premise"].includes(node.kind) &&
    ["pending", "awaiting_review"].includes(node.status ?? "")
  )
    return "review";
  // An approved premise or library entry is an input, not a checked result.
  if (
    ["hypothesis", "library"].includes(node.kind) &&
    ["approved", "confirmed", "active", "available"].includes(node.status ?? "")
  )
    return "";
  for (const [tone, values] of TONES) {
    if (values.includes(node.status ?? "")) return node.kind === "goal" && tone === "planned" ? "open" : tone;
  }
  return node.kind === "goal" ? "open" : "";
}

const PROVIDERS: Record<string, string> = { "codex-cli": "Codex", codex: "Codex", "claude-code": "Claude Code" };
const STOP_WORDS = new Set(["a", "an", "and", "for", "in", "of", "on", "the", "to", "with"]);

/** The harness an agent runs in, for example "Codex". */
export function harnessName(provider: string | null | undefined): string {
  return (provider && PROVIDERS[provider]) ?? provider ?? "";
}

/** Readable model names: "gpt-6.2-sol" → "GPT-6.2 Sol", "claude-opus-5-5" → "Claude Opus 5.5". */
export function prettyModel(model: string): string {
  if (/[A-Z ]/.test(model)) return model;
  const gpt = /^gpt-(\d+(?:\.\d+)?)(?:-(.+))?$/.exec(model);
  if (gpt) return ["GPT-" + gpt[1], ...(gpt[2] ?? "").split("-").filter(Boolean).map(capitalize)].join(" ");
  const claude = /^claude-([a-z]+)-(\d+)(?:-(\d+))?$/.exec(model);
  if (claude) return `Claude ${capitalize(claude[1] ?? "")} ${claude[2]}${claude[3] ? "." + claude[3] : ""}`;
  return model;
}

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** An agent as people name it: model and reasoning effort, for example "GPT-6.2 Sol xhigh". */
export function modelName(
  t: TFunction,
  record: { model?: string | null; provider?: string | null; model_provider?: string | null; effort?: string | null },
): string {
  const effort = typeof record.effort === "string" && record.effort ? " " + record.effort : "";
  if (typeof record.model === "string" && record.model) return prettyModel(record.model) + effort;
  return (harnessName(record.provider ?? record.model_provider) || t("model.fallback")) + effort;
}

/** A short code for a claim: the server's, or the initials of its title ("Fermat's Last Theorem" → "FLT"). */
export function claimCode(claim: { id: string; title?: string | null; code?: string | null }): string {
  if (claim.code) return claim.code;
  const words = (claim.title ?? "").split(/[^A-Za-z0-9']+/).filter((word) => /^[A-Za-z]/.test(word));
  const initials = words
    .filter((word) => !STOP_WORDS.has(word.toLowerCase()))
    .map((word) => word.charAt(0).toUpperCase())
    .join("")
    .slice(0, 4);
  return initials.length >= 2 ? initials : "C" + claim.id.replace(/-/g, "").slice(0, 3).toUpperCase();
}

/** Canonicalize ASCII letters without turning an unsupported Unicode character into a valid code. */
export function normalizeReferenceCode(value: string): string {
  return value.trim().replace(/[a-z]/g, (letter) => letter.toUpperCase());
}

export function isCodex(record: { provider?: string; model_provider?: string }): boolean {
  return ["codex-cli", "codex"].includes(record.provider ?? record.model_provider ?? "");
}

export function timeText(language: string, value: string | null | undefined, withDay = false): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const options: Intl.DateTimeFormatOptions = withDay
    ? { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" };
  return new Intl.DateTimeFormat(language, options).format(date);
}
