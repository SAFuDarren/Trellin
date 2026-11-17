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
  if (node.kind === "evidence" && node.status === "pending") return statusText(t, "awaiting_review");
  if (node.kind === "hypothesis" && node.status === "approved") return statusText(t, "confirmed");
  return statusText(t, node.status);
}

export function nodeTitle(t: TFunction, node: GraphNode): string {
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
  if (title && !UUID_TITLE.test(title) && title.length <= 60) return title;
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

export function nodeSub(t: TFunction, node: GraphNode): { text: string; math: boolean } {
  if (node.kind === "evidence") {
    const id = node.reference?.evidence_id;
    return { text: kindText(t, "evidence") + (id ? " · " + String(id).slice(0, 8) : ""), math: false };
  }
  const formula = nodeFormula(node);
  if (formula && formula !== nodeTitle(t, node)) return { text: summarize(formula, 44), math: true };
  return { text: t(`kind.${node.kind}`, { defaultValue: "" }), math: false };
}

export function nodeTone(node: GraphNode, blocked: boolean): Tone {
  if (blocked) return "bad";
  if (node.kind === "evidence" && ["pending", "awaiting_review"].includes(node.status ?? "")) return "review";
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

export function modelName(
  t: TFunction,
  record: { model?: string; provider?: string; model_provider?: string },
): string {
  if (typeof record.model === "string" && record.model) return record.model;
  const provider = record.provider ?? record.model_provider;
  return (provider && PROVIDERS[provider]) ?? provider ?? t("model.fallback");
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
