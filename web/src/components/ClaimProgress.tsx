import type { TFunction } from "i18next";
import type { ClaimProgress } from "../workbench/claimProgress";
import { nodeStatus, nodeTone, type Tone } from "../workbench/text";
import "../styles/claim-progress.css";

type ProgressTone = Tone | "live";
const TONES: Record<NonNullable<ClaimProgress["state"]>, ProgressTone> = {
  review: "review",
  decision: "bad",
  verifying: "live",
  running: "live",
  recheck: "bad",
  ready: "open",
  queued: "planned",
};

export function claimProgressText(t: TFunction, progress: ClaimProgress | undefined, status: string | undefined) {
  const recorded = progress?.state;
  const state = status === "needs_recheck" && recorded !== "decision" && recorded !== "review" ? "recheck" : recorded;
  const fallback = { id: "", kind: "goal", status };
  const count = progress?.obligations;
  return {
    state,
    label: state ? t(`claimProgress.states.${state}`) : nodeStatus(t, fallback),
    tone: state ? TONES[state] : nodeTone(fallback, false),
    count: count?.total ? t("claimProgress.obligations", count) : undefined,
    emptyCountLabel: count?.total === 0 ? t("claimProgress.noObligations") : undefined,
  };
}

export function ClaimProgressSummary({
  label,
  tone,
  count,
  emptyCountLabel,
  detail,
  showState = true,
}: ReturnType<typeof claimProgressText> & { detail?: string | null; showState?: boolean }) {
  if (!showState && !count && !emptyCountLabel && !detail) return null;
  return (
    <span className="claim-progress">
      {showState && (
        <span className="claim-progress-state" data-tone={tone}>
          <span className="mark" data-tone={tone} aria-hidden="true" />
          <span>{label}</span>
        </span>
      )}
      {count ? (
        <span className="claim-progress-count">{count}</span>
      ) : emptyCountLabel ? (
        <span className="claim-progress-empty" role="img" aria-label={emptyCountLabel} title={emptyCountLabel} />
      ) : (
        detail && <span>{detail}</span>
      )}
    </span>
  );
}
