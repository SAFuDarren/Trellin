import { Fragment, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import { useInbox, type InboxItem } from "../api/sections";
import { ClarificationReview } from "../components/ClarificationReview";
import { GoalReview } from "../components/GoalReview";
import { DecisionRecords } from "../components/DecisionRecords";
import { IntakeReview } from "../components/IntakeReview";
import { ReviewSigner } from "../components/project/ReviewFields";
import { rememberReviewer, savedReviewer } from "../components/project/review";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { GOAL_REVIEW_ROUTE, reviewItemHref, reviewItemKindLabel, reviewResultName } from "../shell/reviewItems";
import { formatShortcut } from "../shortcuts";
import { nodeFormula, nodeTitle, statusText, timeText } from "../workbench/text";
import type { GraphSnapshot } from "../workbench/types";
import "../styles/evidence-review.css";

type Decision = "approve" | "return";
/** #345: a reason the researcher can pick instead of typing, for an approval. */
const REASON_SUGGESTIONS = ["scope", "premises"] as const;
const GOAL_ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export function ReviewPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string, nodeId?: string) => void;
}) {
  const { t } = useTranslation();
  const inbox = useInbox(session);
  const clarification = inbox.data?.clarification_review;
  const openGoals = inbox.data?.open_research_goals ?? [];
  const items: InboxItem[] = [
    ...(inbox.data?.items ?? []),
    ...(clarification?.followups ?? []).map((followup) => ({
      item_id: followup.id,
      kind: "clarify_formalization",
      claim_id: null,
      claim_title: followup.title,
      waiting_since: followup.waiting_since,
      question: "",
      detail: { clarification_status: followup.status },
      priority: 1,
    })),
  ];
  const requestedGoal = sub.startsWith(GOAL_REVIEW_ROUTE) ? sub.slice(GOAL_REVIEW_ROUTE.length).toLowerCase() : null;
  // #345: without an item, this page lists decision records; what waits is listed in Needs you.
  const selected =
    requestedGoal === null
      ? sub
        ? items.find((item) => item.item_id === sub)
        : undefined
      : items.find((item) => item.kind === "choose_conjecture" && item.item_id === requestedGoal);
  const goalId = requestedGoal ?? (selected?.kind === "choose_conjecture" ? selected.item_id : null);
  // A decided item leaves the list, so its confirmation travels with the page that comes next.
  const [notice, setNotice] = useState<{ text: string; at: string } | null>(null);
  const shownNotice = notice?.at === sub ? notice.text : "";
  const subOf = (item: InboxItem) => (item.kind === "choose_conjecture" ? GOAL_REVIEW_ROUTE : "") + item.item_id;
  const decided = (text: string) => {
    const others = items.filter((item) => item !== selected);
    const index = selected ? items.indexOf(selected) : -1;
    const next = others[Math.max(0, Math.min(index, others.length - 1))];
    const at = next ? subOf(next) : "";
    setNotice({ text, at });
    window.location.hash = next ? reviewItemHref(next) : href("review");
  };
  return (
    <SectionLayout
      space="work"
      title={t("decisionRecords.title")}
      groups={[
        {
          name: t("review.openGoals"),
          entries: openGoals.map((goal) => ({
            key: goal.id,
            label: goal.title,
            href: href("review", GOAL_REVIEW_ROUTE + goal.id),
            current: goal.id === goalId,
          })),
        },
      ]}
    >
      {shownNotice && (
        <p className="page-notice" role="status">
          {shownNotice}
        </p>
      )}
      {goalId !== null ? (
        GOAL_ID.test(goalId) ? (
          <GoalReview key={session + "/" + goalId} session={session} goalId={goalId} onOpen={onOpen} />
        ) : (
          <>
            <PageHead title={t("review.kinds.choose_conjecture")} />
            <Empty>{t("goalReview.errors.invalid")}</Empty>
          </>
        )
      ) : inbox.isError ? (
        <Empty>{inbox.error.message}</Empty>
      ) : !selected && !sub ? (
        <DecisionRecords session={session} onOpen={onOpen} />
      ) : !selected ? (
        <>
          <PageHead title={t("rail.review")} />
          <Empty>{inbox.isPending ? t("common.loading") : t("review.itemNoLongerWaiting")}</Empty>
        </>
      ) : selected.kind === "clarify_formalization" && clarification ? (
        <ClarificationReview
          key={selected.item_id}
          session={session}
          workspace={clarification.workspace_id}
          id={selected.item_id}
          onConfirmed={(kind) => decided(t(`clarifications.confirmed.${kind}`))}
        />
      ) : selected.kind === "confirm_intake" ? (
        <IntakeReview
          key={selected.item_id}
          session={session}
          item={selected}
          signer={inbox.data?.review_identity?.display_name || undefined}
          onOpen={onOpen}
          onDecided={decided}
        />
      ) : selected.kind === "review_evidence" && selected.claim_id ? (
        <EvidenceReview
          key={selected.item_id}
          session={session}
          item={selected}
          reviewWorkspace={inbox.data?.review_identity?.workspace_id}
          signer={inbox.data?.review_identity?.display_name || undefined}
          onOpen={onOpen}
          onDecided={decided}
        />
      ) : (
        <OtherItem item={selected} onOpen={onOpen} />
      )}
    </SectionLayout>
  );
}

export function EvidenceReview({
  session,
  item,
  reviewWorkspace,
  signer,
  onOpen,
  onDecided,
}: {
  session: string;
  item: InboxItem;
  reviewWorkspace?: string;
  signer?: string;
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
  const [reviewer, setReviewer] = useState(() => (reviewWorkspace ? "" : savedReviewer()));
  // #328: only a database without a local identity asks who is deciding.
  const asksName = !reviewWorkspace && !signer;
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ sending?: boolean; message?: string; error?: boolean }>({});
  const requestKeys = useRef(new Map<string, string>());
  // #345: Approve and next is the primary action; returning asks for the same reason.
  const decide = async (action: Decision) => {
    if (state.sending) return;
    const actor = signer ?? reviewer.trim();
    const rationale = reason.trim();
    if (!rationale || (asksName && !actor)) {
      setState({ message: t(asksName ? "review.needNameAndReason" : "review.needReason"), error: true });
      return;
    }
    setState({ sending: true });
    try {
      if (reviewWorkspace) {
        const draft = JSON.stringify([reviewWorkspace, action, rationale]);
        const key = requestKeys.current.get(draft) ?? crypto.randomUUID();
        requestKeys.current.set(draft, key);
        const route = `/api/v1/workspaces/${encodeURIComponent(reviewWorkspace)}/evidence/${encodeURIComponent(item.item_id)}/reviews`;
        await api<{ id: string; user_id: string }>(session, route, {
          data: { decision: action, reason: rationale },
          idempotencyKey: key,
        });
      } else {
        if (asksName) rememberReviewer(actor);
        await api(session, "/api/review", {
          data: { evidence_id: item.item_id, action, actor, reason: rationale },
        });
      }
      setState({});
      setReason("");
      onDecided(t(action === "approve" ? "review.approved" : "review.returned"));
      void queries.invalidateQueries({ queryKey: ["inbox"] });
      void queries.invalidateQueries({ queryKey: ["decision-records"] });
      void queries.invalidateQueries({ queryKey: ["graph", item.claim_id] });
      void queries.invalidateQueries({ queryKey: ["atlas"] });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setState({
        message:
          error instanceof ApiError && error.code ? t(`errors.${error.code}`, { defaultValue: message }) : message,
        error: true,
      });
    }
  };
  const title = reviewResultName(item) || (node ? nodeTitle(t, node) : reviewItemKindLabel(t, item));
  const explanation = t(
    item.detail?.target === "goal"
      ? "review.goalEvidenceExplain"
      : item.detail?.kind === "counterexample"
        ? "review.counterexampleExplain"
        : "review.evidenceExplain",
  );
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
          <p className="page-note">{explanation}</p>
          {formula && formula !== lean && <p className="formula">{formula}</p>}
          {lean && <pre className="code-block">{lean}</pre>}
          {graph.isPending && <p className="inline-note">{t("common.loading")}</p>}
          <form
            className="decision"
            onSubmit={(event) => {
              event.preventDefault();
              void decide("approve");
            }}
            onKeyDown={(event) => {
              if (
                event.key !== "Enter" ||
                (!event.metaKey && !event.ctrlKey) ||
                event.altKey ||
                event.shiftKey ||
                event.nativeEvent.isComposing ||
                event.defaultPrevented
              )
                return;
              event.preventDefault();
              if (!event.repeat) event.currentTarget.requestSubmit();
            }}
          >
            {signer ? (
              <ReviewSigner name={signer} />
            ) : asksName ? (
              <>
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
              </>
            ) : null}
            <label className="field-label" htmlFor="review-reason">
              {t("review.reason")}
            </label>
            <div className="reason-suggestions" role="group" aria-label={t("review.suggestionsLabel")}>
              {REASON_SUGGESTIONS.map((key) => {
                const text = t(`review.suggestions.${key}`);
                return (
                  <button
                    key={key}
                    type="button"
                    disabled={state.sending}
                    onClick={() => setReason(reason.trim() ? `${reason.trim()}; ${text}` : text)}
                  >
                    {text}
                  </button>
                );
              })}
            </div>
            <textarea
              id="review-reason"
              rows={3}
              maxLength={2000}
              value={reason}
              placeholder={t("review.reasonHint")}
              onChange={(event) => setReason(event.target.value)}
            />
            <div className="row">
              <button
                type="submit"
                className="button primary"
                aria-keyshortcuts="Meta+Enter Control+Enter"
                disabled={state.sending}
              >
                {t("review.approveNext")}
              </button>
              <button type="button" className="button" disabled={state.sending} onClick={() => void decide("return")}>
                {t("review.return")}
              </button>
              <kbd>{formatShortcut("Primary+Enter")}</kbd>
              {state.message && (
                <span className="status" role="status" data-error={String(Boolean(state.error))}>
                  {state.message}
                </span>
              )}
            </div>
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
          </dl>
        </aside>
      </div>
    </>
  );
}

export function OtherItem({ item, onOpen }: { item: InboxItem; onOpen: (claimId: string, nodeId?: string) => void }) {
  const { t, i18n } = useTranslation();
  // #340: checksums identify the record for software, not for the researcher deciding it.
  const details = Object.entries(item.detail ?? {}).filter(
    ([key, value]) => !key.includes("sha256") && ["string", "number", "boolean"].includes(typeof value),
  );
  return (
    <>
      <PageHead
        title={reviewItemKindLabel(t, item)}
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
        <p className="inline-note">{t("review.notInApp")}</p>
      </div>
    </>
  );
}
