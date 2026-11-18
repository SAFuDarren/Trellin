import { Fragment, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api } from "../api/client";
import { useInbox, type InboxItem } from "../api/sections";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { nodeFormula, nodeTitle, statusText, timeText } from "../workbench/text";
import type { GraphSnapshot } from "../workbench/types";

const REVIEWER_KEY = "research-multiplier-reviewer";

function readReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

export function kindLabel(t: TFunction, item: InboxItem): string {
  if (item.kind === "review_evidence" && item.detail?.kind === "counterexample")
    return t("review.kinds.counterexample");
  return t(`review.kinds.${item.kind}`, { defaultValue: item.kind.replace(/_/g, " ") });
}

export function ReviewPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string, nodeId?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const inbox = useInbox(session);
  const items = inbox.data?.items ?? [];
  const selected = items.find((item) => item.item_id === sub) ?? items[0];
  // A decided item leaves the inbox, so its confirmation is kept here rather than on the item.
  const [notice, setNotice] = useState("");
  useEffect(() => setNotice(""), [sub]);
  return (
    <SectionLayout
      title={t("rail.review")}
      groups={[
        {
          name: t("review.waiting", { count: items.length }),
          empty: inbox.isPending ? t("common.loading") : t("review.none"),
          entries: items.map((item) => ({
            key: item.item_id,
            label: item.claim_title || t("sidebar.untitled"),
            sub: `${kindLabel(t, item)} · ${timeText(i18n.language, item.waiting_since, true)}`,
            href: href("review", item.item_id),
            current: item === selected,
            dot: item.priority <= 1 ? "bad" : "warn",
          })),
        },
      ]}
    >
      {notice && (
        <p className="page-notice" role="status">
          {notice}
        </p>
      )}
      {inbox.isError ? (
        <Empty>{inbox.error.message}</Empty>
      ) : !selected ? (
        <>
          <PageHead title={t("rail.review")} />
          <Empty>{inbox.isPending ? t("common.loading") : t("review.allDone")}</Empty>
        </>
      ) : selected.kind === "review_evidence" && selected.claim_id ? (
        <EvidenceReview
          key={selected.item_id}
          session={session}
          item={selected}
          onOpen={onOpen}
          onDecided={setNotice}
        />
      ) : (
        <OtherItem item={selected} onOpen={onOpen} />
      )}
    </SectionLayout>
  );
}

function EvidenceReview({
  session,
  item,
  onOpen,
  onDecided,
}: {
  session: string;
  item: InboxItem;
  onOpen: (claimId: string, nodeId?: string) => void;
  onDecided: (message: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const queries = useQueryClient();
  const graph = useQuery({
    queryKey: ["graph", item.claim_id],
    queryFn: ({ signal }) =>
      api<GraphSnapshot>(session, "/api/graph?claim=" + encodeURIComponent(item.claim_id!), { signal }),
  });
  const node = graph.data?.nodes.find((entry) => entry.reference?.evidence_id === item.item_id);
  const premises = node
    ? graph
        .data!.edges.filter((edge) => edge.target === node.id)
        .map((edge) => graph.data!.nodes.find((entry) => entry.id === edge.source))
    : [];
  const uses = node
    ? graph
        .data!.edges.filter((edge) => edge.source === node.id)
        .map((edge) => graph.data!.nodes.find((entry) => entry.id === edge.target))
    : [];
  const [reviewer, setReviewer] = useState(readReviewer);
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ sending?: boolean; message?: string; error?: boolean }>({});
  const decide = async (action: "approve" | "return") => {
    if (!reviewer.trim() || !reason.trim()) {
      setState({ message: t("review.needNameAndReason"), error: true });
      return;
    }
    try {
      window.localStorage.setItem(REVIEWER_KEY, reviewer.trim());
    } catch {
      // The name still applies to this decision.
    }
    setState({ sending: true });
    try {
      await api(session, "/api/review", {
        data: { evidence_id: item.item_id, action, actor: reviewer.trim(), reason: reason.trim() },
      });
      setState({});
      setReason("");
      onDecided(t(action === "approve" ? "review.approved" : "review.returned"));
      void queries.invalidateQueries({ queryKey: ["inbox"] });
      void queries.invalidateQueries({ queryKey: ["graph", item.claim_id] });
      void queries.invalidateQueries({ queryKey: ["atlas"] });
    } catch (error) {
      setState({ message: error instanceof Error ? error.message : String(error), error: true });
    }
  };
  const title = node ? nodeTitle(t, node) : t("review.kinds.review_evidence");
  const lean = node?.formalization ?? "";
  const formula = node ? nodeFormula(node) : "";
  return (
    <>
      <PageHead
        title={title}
        note={`${t("review.kinds.review_evidence")} · ${timeText(i18n.language, item.waiting_since, true)}`}
        actions={
          <button type="button" className="button" onClick={() => onOpen(item.claim_id!, node?.id)}>
            {t("common.openInWorkbench")}
          </button>
        }
      />
      <div className="page-body split">
        <div className="primary">
          <div className="crumbs">
            <span>{item.claim_title}</span>
          </div>
          <p className="page-note">{t("review.evidenceExplain")}</p>
          {formula && formula !== lean && <p className="formula">{formula}</p>}
          {lean && <pre className="code-block">{lean}</pre>}
          {graph.isPending && <p className="inline-note">{t("common.loading")}</p>}
          <form
            className="decision"
            onSubmit={(event) => {
              event.preventDefault();
              void decide("approve");
            }}
          >
            <label className="field-label" htmlFor="reviewer">
              {t("review.reviewer")}
            </label>
            <input
              id="reviewer"
              className="reviewer"
              value={reviewer}
              maxLength={120}
              onChange={(event) => setReviewer(event.target.value)}
            />
            <label className="field-label" htmlFor="review-reason">
              {t("review.reason")}
            </label>
            <textarea
              id="review-reason"
              rows={3}
              maxLength={2000}
              value={reason}
              placeholder={t("review.reasonHint")}
              onChange={(event) => setReason(event.target.value)}
            />
            <div className="row">
              <button type="submit" className="button primary" disabled={state.sending}>
                {t("review.approve")}
              </button>
              <button type="button" className="button" disabled={state.sending} onClick={() => void decide("return")}>
                {t("review.return")}
              </button>
              {state.message && (
                <span className="status" role="status" data-error={String(Boolean(state.error))}>
                  {state.message}
                </span>
              )}
            </div>
            <p className="inline-note">{t("review.identityNote")}</p>
          </form>
        </div>
        <aside className="aside">
          <h3 className="field-label">{t("review.context")}</h3>
          <dl className="facts">
            {node?.status && (
              <>
                <dt>{t("review.status")}</dt>
                <dd>{statusText(t, node.verification_status ?? node.status)}</dd>
              </>
            )}
            {premises.length > 0 && (
              <>
                <dt>{t("edge.actual")}</dt>
                <dd>{premises.map((premise) => (premise ? nodeTitle(t, premise) : "")).join(", ")}</dd>
              </>
            )}
            {uses.length > 0 && (
              <>
                <dt>{t("detail.usedBy")}</dt>
                <dd>{uses.map((use) => (use ? nodeTitle(t, use) : "")).join(", ")}</dd>
              </>
            )}
            {typeof item.detail?.sha256 === "string" && (
              <>
                <dt>{"SHA-256"}</dt>
                <dd className="mono">{String(item.detail.sha256).slice(0, 16)}…</dd>
              </>
            )}
          </dl>
        </aside>
      </div>
    </>
  );
}

function OtherItem({ item, onOpen }: { item: InboxItem; onOpen: (claimId: string, nodeId?: string) => void }) {
  const { t, i18n } = useTranslation();
  const details = Object.entries(item.detail ?? {}).filter(([, value]) =>
    ["string", "number", "boolean"].includes(typeof value),
  );
  return (
    <>
      <PageHead
        title={kindLabel(t, item)}
        note={timeText(i18n.language, item.waiting_since, true)}
        actions={
          item.claim_id ? (
            <button type="button" className="button" onClick={() => onOpen(item.claim_id!)}>
              {t("common.openInWorkbench")}
            </button>
          ) : undefined
        }
      />
      <div className="page-body">
        {item.claim_title && <div className="crumbs">{item.claim_title}</div>}
        <p className="page-note">{item.question}</p>
        {details.length > 0 && (
          <dl className="facts">
            {details.map(([key, value]) => (
              <Fragment key={key}>
                <dt>{key.replace(/_/g, " ")}</dt>
                <dd className="mono">{String(value)}</dd>
              </Fragment>
            ))}
          </dl>
        )}
        <p className="inline-note">{t("review.cliNote")}</p>
        <pre className="code-block">{"research-multiplier review inbox"}</pre>
      </div>
    </>
  );
}
