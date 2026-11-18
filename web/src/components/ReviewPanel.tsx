import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { Claim } from "../api/client";
import { impactItems, impactLineKey, type Graph, type View } from "../workbench/graph";
import { kindText, nodeFormula, nodeStatus, nodeTitle, nodeTone, statusText, summarize } from "../workbench/text";
import type { ImpactEntry, ImpactPreview } from "../workbench/types";
import { Mark } from "./Icons";

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
  view: View;
  selected: string | null;
  assumption: string | null;
  impact: ImpactPreview | undefined;
  impactLoading: boolean;
  impactError: string;
  onSelect: (id: string) => void;
  onClose: () => void;
};

export function queueEntries(
  t: TFunction,
  graph: Graph | null,
  view: View,
  assumption: string | null,
  impact: ImpactPreview | undefined,
) {
  const entries: { id: string; kind: string; tone: "review" | "bad" }[] = [];
  if (!graph) return entries;
  if (view === "impact") {
    if (assumption) entries.push({ id: assumption, kind: t("impact.previewLabel"), tone: "bad" });
    for (const [id, effect] of impactItems(graph, impact)) {
      if (graph.nodes.has(id) && id !== assumption) entries.push({ id, kind: t(impactLineKey(effect)), tone: "bad" });
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
  const { graph, view, selected, assumption, impact } = props;
  const entries = queueEntries(t, graph, view, assumption, impact);
  return (
    <aside className="review" id="review" aria-label={t("review.label")}>
      <section className="queue" aria-labelledby="queue-title">
        <header>
          <h2 id="queue-title" tabIndex={-1}>
            {view === "impact" ? t("review.affected") : t("review.title")}
          </h2>
          <span id="queue-count">{entries.length ? t("review.count", { count: entries.length }) : ""}</span>
          <button type="button" className="icon-button" aria-label={t("review.close")} onClick={props.onClose}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
              <path d="M14.5 4.5v15" />
            </svg>
          </button>
        </header>
        <ul>
          {entries.length === 0 && <li className="queue-empty">{graph ? t("review.empty") : ""}</li>}
          {graph &&
            entries.map(({ id, kind, tone }) => (
              <li key={id}>
                <button
                  type="button"
                  className="queue-item"
                  aria-current={id === selected ? "true" : undefined}
                  onClick={() => props.onSelect(id)}
                >
                  <span>
                    <Mark tone={tone} />
                    <span className="label">{nodeTitle(t, graph.nodes.get(id)!)}</span>
                  </span>
                  <span className="kind">{kind}</span>
                </button>
              </li>
            ))}
        </ul>
      </section>
      <section className="node-detail" aria-label={t("review.detail")}>
        <NodeDetail {...props} />
      </section>
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

function NodeDetail({
  graph,
  claims,
  claimId,
  view,
  selected,
  assumption,
  impact,
  impactLoading,
  impactError,
  onSelect,
}: Props) {
  const { t } = useTranslation();
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
  const affected = isImpact ? impactItems(graph, impact) : new Map<string, ImpactEntry>();
  const effect = affected.get(node.id);
  const tone = isImpact && (effect || node.id === assumption) ? "bad" : nodeTone(node, blockers.length > 0);
  const stateLabel =
    isImpact && node.id === assumption
      ? t("impact.previewLabel")
      : effect
        ? t(impactLineKey(effect))
        : blockers.length
          ? t("node.blocked")
          : nodeStatus(t, node);
  const lean = typeof node.formalization === "string" ? node.formalization : "";
  const math = typeof node.formula === "string" ? node.formula : nodeFormula(node);
  const prose =
    typeof node.statement === "string" && node.statement !== lean && node.statement !== math ? node.statement : "";
  const incoming = graph.incoming.get(node.id) ?? [];
  const outgoing = graph.outgoing.get(node.id) ?? [];
  const parameters = (node.parameters ?? []).map(({ name, type }) => `${name} : ${type}`).join("\n");
  const references = REFERENCE_KEYS.filter((key) => ["string", "number"].includes(typeof node.reference?.[key]));
  const nameFor = (entry: ImpactEntry) => {
    const title = entry.title ?? entry.label ?? t(`kind.${entry.kind}`, { defaultValue: t("kind.record") });
    const claimTitle = entry.claim_title ?? claims.find((claim) => claim.id === entry.claim_id)?.title;
    return relation(entry.id, claimTitle && entry.claim_id !== claimId ? `${title} · ${claimTitle}` : title);
  };
  const affectedList = impact?.affected ?? [];
  const scope = impact?.scope ?? [];
  return (
    <>
      <div className="detail-head">
        <span>{[kindText(t, node.kind), node.domain].filter(Boolean).join(" · ")}</span>
        <span className="detail-state">
          <Mark tone={tone} />
          {stateLabel}
        </span>
      </div>
      <h2>{nodeTitle(t, node)}</h2>
      {blockers.map((blockage, position) => {
        const explanation = blockage.reason || blockage.detail || blockage.status;
        return explanation ? (
          <p className="detail-note" key={position}>
            {t("node.blockedReason", { reason: explanation })}
          </p>
        ) : null;
      })}
      {isImpact && node.id === assumption && (
        <p className="detail-note">{impactLoading ? t("impact.loading") : impactError || t("impact.explain")}</p>
      )}
      {math && math !== lean ? (
        <Block label={t("detail.formula")} value={summarize(math, 240)} />
      ) : prose ? (
        <Block label={t("detail.statement")} value={summarize(prose, 240)} />
      ) : null}
      {lean && <Block label={t("detail.lean")} value={lean.length > 600 ? summarize(lean, 600) : lean} lean />}
      <dl className="facts">
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
      {(lean.length > 600 || prose.length > 240 || parameters) && (
        <details>
          <summary>{t("detail.full")}</summary>
          {lean.length > 600 && <Block label={t("detail.leanFull")} value={lean} lean />}
          {prose.length > 240 && <Block label={t("detail.statementFull")} value={prose} />}
          {parameters && <Block label={t("detail.parameters")} value={parameters} lean />}
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
