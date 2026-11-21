import type { ReactNode } from "react";
import type { FloatingOption } from "./FloatingControls";
import { InspectorActions } from "./InspectorActions";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { Claim } from "../api/client";
import type { InboxItem } from "../api/sections";
import type { ClaimProgress } from "../workbench/claimProgress";
import { impactItems, impactLineKey, workLabel, type Graph, type NodeWork, type View } from "../workbench/graph";
import { kindText, nodeStatus, nodeTitle, nodeTone, statusText } from "../workbench/text";
import type { ImpactEntry, ImpactPreview } from "../workbench/types";
import { ClaimProgressSummary, claimProgressText } from "./ClaimProgress";
import { ClaimReviewLinks } from "./ClaimReviewLinks";
import { Mark } from "./Icons";
import { reviewItemHref, reviewLinkLabel, reviewOptions } from "../shell/reviewItems";

const REFERENCE_KEYS = [
  "claim_id",
  "statement_id",
  "statement_version",
  "statement_sha256",
  "basis_version_id",
  "version",
  "lemma_id",
  "check_id",
  "evidence_id",
  "verification_attempt_id",
  "implication_id",
  "position",
  "source_claim_id",
  "module",
  "sha256",
] as const;

type Props = {
  graph: Graph | null;
  claims: Claim[];
  claimId: string | null;
  claimProgress?: ClaimProgress;
  claimReviews?: readonly InboxItem[];
  view: View;
  selected: string | null;
  work?: NodeWork;
  assumption: string | null;
  impact: ImpactPreview | undefined;
  impactLoading: boolean;
  impactError: string;
  notice?: { text: string; error: boolean };
  actions?: { primary: FloatingOption; secondary: FloatingOption[] } | null;
  children?: ReactNode;
  onSelect: (id: string) => void;
  onReviewPremise: (id: string) => void;
  onClose: () => void;
};

export function queueEntries(
  t: TFunction,
  graph: Graph | null,
  view: View,
  assumption: string | null,
  affected: ReadonlyMap<string, ImpactEntry> | undefined,
) {
  const entries: { id: string; kind: string; tone: "review" | "bad" }[] = [];
  if (!graph) return entries;
  if (view === "impact") {
    if (assumption) entries.push({ id: assumption, kind: t("impact.previewLabel"), tone: "bad" });
    for (const [id, effect] of affected ?? []) {
      entries.push({ id, kind: t(impactLineKey(effect)), tone: "bad" });
    }
    return entries;
  }
  for (const [id, node] of graph.nodes) {
    const tone = nodeTone(node, false);
    if (tone === "review") entries.push({ id, kind: nodeStatus(t, node), tone });
    else if (node.status === "needs_recheck") entries.push({ id, kind: statusText(t, node.status), tone: "bad" });
  }
  return entries;
}

export function ReviewPanel(props: Props) {
  const { t } = useTranslation();
  const { graph, claims, claimId, view, selected, assumption, impact } = props;
  const isImpact = view === "impact";
  const affected = isImpact && graph && impact && assumption ? impactItems(graph, impact, assumption) : undefined;
  const entries = queueEntries(t, graph, view, assumption, affected);
  const count = isImpact
    ? affected && t("impact.summary", { count: affected.size })
    : entries.length > 0 && t("review.count", { count: entries.length });
  const reviewByEvidence = new Map(
    props.claimReviews?.filter((item) => item.kind === "review_evidence").map((item) => [item.item_id, item]),
  );
  return (
    <aside className="review" id="review" aria-label={t("review.label")}>
      <section className="queue" aria-labelledby="queue-title">
        <header>
          <h2 id="queue-title" tabIndex={-1}>
            {isImpact ? t("review.affected") : t("review.title")}
          </h2>
          <span id="queue-count">{count}</span>
          <button type="button" className="icon-button" aria-label={t("review.close")} onClick={props.onClose}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
              <path d="M14.5 4.5v15" />
            </svg>
          </button>
        </header>
        <ul>
          {entries.length === 0 && graph && (
            <li className="queue-empty">{t(isImpact ? "impact.noPremise" : "review.empty")}</li>
          )}
          {entries.map(({ id, kind, tone }) => {
            const node = graph?.nodes.get(id);
            const evidenceId = node?.reference?.evidence_id;
            const item = !isImpact && typeof evidenceId === "string" ? reviewByEvidence.get(evidenceId) : undefined;
            const effect = affected?.get(id);
            const label = node
              ? nodeTitle(t, node)
              : effect
                ? impactTitle(t, effect, claims, claimId)
                : t("kind.record");
            const content = (
              <>
                <span>
                  <Mark tone={tone} />
                  <span className="label" title={label}>
                    {label}
                  </span>
                </span>
                <span className="kind">{kind}</span>
              </>
            );
            return (
              <li key={id}>
                {item ? (
                  <a className="queue-item" href={reviewItemHref(item)}>
                    {content}
                  </a>
                ) : node ? (
                  <button
                    type="button"
                    className="queue-item"
                    aria-current={id === selected ? "true" : undefined}
                    onClick={() => props.onSelect(id)}
                  >
                    {content}
                  </button>
                ) : (
                  <span className="queue-item">{content}</span>
                )}
              </li>
            );
          })}
        </ul>
      </section>
      <section className="node-detail" id="node-detail" tabIndex={-1} aria-label={t("review.detail")}>
        {props.notice && (
          <p className="notice" role={props.notice.error ? "alert" : "status"} data-error={props.notice.error}>
            {props.notice.text}
          </p>
        )}
        <NodeDetail {...props} />
      </section>
      {props.children}
    </aside>
  );
}

function Fact({ heading, children, mono }: { heading: string; children: ReactNode[] | string; mono?: boolean }) {
  if (typeof children === "string" ? !children : children.length === 0) return null;
  return (
    <>
      <dt>{heading}</dt>
      <dd data-mono={mono ? "true" : undefined}>{children}</dd>
    </>
  );
}

function impactTitle(t: TFunction, entry: ImpactEntry, claims: Claim[], claimId: string | null): string {
  const title = entry.title ?? entry.label ?? t(`kind.${entry.kind}`, { defaultValue: t("kind.record") });
  const claimTitle = entry.claim_title ?? claims.find((claim) => claim.id === entry.claim_id)?.title;
  return claimTitle && entry.claim_id !== claimId ? `${title} · ${claimTitle}` : title;
}

function NodeDetail({
  graph,
  claims,
  claimId,
  claimProgress,
  claimReviews,
  view,
  selected,
  work,
  assumption,
  impact,
  impactLoading,
  impactError,
  actions,
  onSelect,
  onReviewPremise,
}: Props) {
  const { t, i18n } = useTranslation();
  const node = graph && selected ? graph.nodes.get(selected) : undefined;
  if (!graph || !node) return null;
  const relation = (id: string, fallback = "") => {
    const graphId = graph.evidenceAliases.get(id) ?? id;
    const target = graph.nodes.get(graphId);
    if (!target) {
      return (
        <span key={id} title={id}>
          {fallback || t("kind.record")}
        </span>
      );
    }
    return (
      <button type="button" className="relation" key={id} onClick={() => onSelect(graphId)}>
        {nodeTitle(t, target)}
      </button>
    );
  };
  const blockers = graph.blockers.get(node.id) ?? [];
  const isImpact = view === "impact";
  const effect = isImpact ? impactItems(graph, impact, assumption).get(node.id) : undefined;
  const tone = isImpact && (effect || node.id === assumption) ? "bad" : nodeTone(node, blockers.length > 0);
  const stateLabel =
    isImpact && node.id === assumption
      ? t("impact.previewLabel")
      : effect
        ? t(impactLineKey(effect))
        : blockers.length
          ? t("node.blocked")
          : nodeStatus(t, node);
  const claimStatus = claims.find((claim) => claim.id === graph.claim.id)?.status ?? graph.claim.status;
  const progress = claimProgressText(t, claimProgress, claimStatus);
  const goal = node.kind === "goal";
  const goalReviews = goal && !isImpact ? claimReviews : undefined;
  const evidenceReviews =
    !isImpact && claimReviews
      ? claimReviews.filter(
          (item) => item.kind === "review_evidence" && (goal || item.item_id === node.reference?.evidence_id),
        )
      : [];
  const primaryReview = evidenceReviews[0];
  const otherReviews = goalReviews?.filter((item) => item.kind !== "review_evidence");
  const reviewAction = {
    id: "review-evidence",
    label: t("rail.review"),
    href: primaryReview && evidenceReviews.length === 1 ? reviewItemHref(primaryReview) : undefined,
  };
  const showNodeState = !goal || isImpact || (claimStatus !== "proved" && blockers.length > 0 && !progress.state);
  const lean = typeof node.formalization === "string" ? node.formalization : "";
  const math = node.formula || node.proposition || "";
  const listSeparator = t("chat.listSeparator", { defaultValue: ", " });
  const prose =
    typeof node.statement === "string" && node.statement !== lean && node.statement !== math ? node.statement : "";
  const incoming = graph.incoming.get(node.id) ?? [];
  const outgoing = graph.outgoing.get(node.id) ?? [];
  const parameters = (node.parameters ?? []).map(({ name, type }) => `${name} : ${type}`).join("\n");
  const references = REFERENCE_KEYS.filter((key) => ["string", "number"].includes(typeof node.reference?.[key]));
  const nameFor = (entry: ImpactEntry) => relation(entry.id, impactTitle(t, entry, claims, claimId));
  const conditionId = node.kind === "proposed_premise" ? node.reference?.condition_id : undefined;
  const affectedList = impact?.affected ?? [];
  const scope = impact?.scope ?? [];
  return (
    <>
      <header className="detail-header">
        <h2>{node.kind === "goal" && graph.claim.title ? graph.claim.title : nodeTitle(t, node, { full: true })}</h2>
        <div className="detail-head">
          <ClaimProgressSummary
            {...progress}
            label={showNodeState ? stateLabel : progress.label}
            tone={showNodeState ? tone : progress.tone}
            count={goal ? progress.count : undefined}
            emptyCountLabel={goal ? progress.emptyCountLabel : undefined}
            showState={showNodeState || progress.state !== "review" || !goalReviews?.length}
          />
          {goal && primaryReview && (
            <span className="tag claim-review-link" data-tone="review">
              {reviewLinkLabel(t, evidenceReviews)}
            </span>
          )}
          {otherReviews && <ClaimReviewLinks items={otherReviews} />}
          <span>{[graph.codes.get(node.id), kindText(t, node.kind), node.domain].filter(Boolean).join(" · ")}</span>
        </div>
        {actions ? (
          <InspectorActions
            primary={primaryReview ? reviewAction : actions.primary}
            primaryOptions={evidenceReviews.length > 1 ? reviewOptions(t, i18n.language, evidenceReviews) : undefined}
            secondary={[
              ...actions.secondary,
              ...(primaryReview ? [actions.primary] : []),
              ...(conditionId
                ? [{ id: "review-premise", label: t("premise.review"), onSelect: () => onReviewPremise(conditionId) }]
                : []),
            ]}
          />
        ) : (
          conditionId && (
            <button type="button" className="button" onClick={() => onReviewPremise(conditionId)}>
              {t("premise.review")}
            </button>
          )
        )}
      </header>
      {blockers.map((blockage, position) => {
        const explanation = blockage.reason || blockage.detail || blockage.status;
        return explanation ? (
          <p className="detail-note" key={position}>
            {t("node.blockedReason", { reason: explanation })}
          </p>
        ) : null;
      })}
      {isImpact && node.id === assumption && (
        <p className="detail-note">
          {impactError || (impactLoading && !impact ? t("impact.loading") : t("impact.explain"))}
        </p>
      )}
      {prose && <Block label={t("detail.statement")} value={prose} />}
      {math && math !== lean && <Block label={t("detail.formula")} value={math} />}
      {lean && <Block label={t("detail.lean")} value={lean} lean />}
      <dl className="facts">
        {work && (
          <>
            <Fact heading={t("legend.running")}>{workLabel(work.running, listSeparator)}</Fact>
            <Fact heading={t("legend.queued")}>{workLabel(work.queued, listSeparator)}</Fact>
          </>
        )}
        {typeof node.citation === "string" && <Fact heading={t("detail.source")}>{node.citation}</Fact>}
        {node.verification_status && node.verification_status !== node.status && (
          <Fact heading={t("detail.leanRecord")}>{statusText(t, node.verification_status)}</Fact>
        )}
        {(["actual", "planned", "scope"] as const).map((kind) => (
          <Fact key={kind} heading={t(`edge.${kind}`)}>
            {incoming.filter((edge) => edge.kind === kind).map((edge) => relation(edge.source))}
          </Fact>
        ))}
        <Fact heading={t("detail.usedBy")}>
          {[...new Set(outgoing.map((edge) => edge.target))].map((id) => relation(id))}
        </Fact>
        {isImpact && impact && (
          <>
            <Fact heading={t("impact.direct")}>
              {affectedList
                .filter((entry) => entry.relation === "direct" && entry.status !== "needs_recheck")
                .map(nameFor)}
            </Fact>
            <Fact heading={t("impact.indirect")}>
              {affectedList
                .filter((entry) => entry.relation === "indirect" && entry.status !== "needs_recheck")
                .map(nameFor)}
            </Fact>
            <Fact heading={t("impact.inRecheck")}>
              {affectedList.filter((entry) => entry.status === "needs_recheck").map(nameFor)}
            </Fact>
            <Fact heading={t("impact.openSteps")}>
              {scope.filter((entry) => entry.kind === "obligation").map(nameFor)}
            </Fact>
            <Fact heading={t("impact.frozenScope")}>
              {scope.filter((entry) => entry.kind !== "obligation").map(nameFor)}
            </Fact>
            {!(affectedList.length || scope.length) && <Fact heading={t("impact.effect")}>{t("impact.none")}</Fact>}
          </>
        )}
      </dl>
      {parameters && (
        <details>
          <summary>{t("detail.parameters")}</summary>
          <pre className="lean">{parameters}</pre>
        </details>
      )}
      {references.length > 0 && (
        <details>
          <summary>{t("detail.provenance")}</summary>
          <dl className="facts">
            {references.map((key) => (
              <Fact key={key} heading={t(`reference.${key}`)} mono>
                {String(node.reference?.[key])}
              </Fact>
            ))}
          </dl>
        </details>
      )}
    </>
  );
}

function Block({ label, value, lean }: { label: string; value: string; lean?: boolean }) {
  return (
    <div className="detail-block">
      <span>{label}</span>
      {lean ? <pre className="lean">{value}</pre> : <p className="formula">{value}</p>}
    </div>
  );
}
