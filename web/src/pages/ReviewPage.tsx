import { Fragment, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import { useInbox, type InboxItem } from "../api/sections";
import { ClarificationReview } from "../components/ClarificationReview";
import { GoalReview } from "../components/GoalReview";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { WorkbenchViewMenu } from "../components/WorkbenchViewMenu";
import { href } from "../shell/route";
import { GOAL_REVIEW_ROUTE, reviewItemHref, reviewItemKindLabel, reviewResultName } from "../shell/reviewItems";
import { formatShortcut } from "../shortcuts";
import { nodeFormula, nodeTitle, statusText, timeText } from "../workbench/text";
import type { GraphSnapshot } from "../workbench/types";
import "../styles/evidence-review.css";

const REVIEWER_KEY = "research-multiplier-reviewer";
const DECISIONS = ["approve", "return"] as const;
const GOAL_ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

function readReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

export function ReviewPage({
  session,
  sub,
  onOpen,
  pendingReviews,
}: {
  session: string;
  sub: string;
  pendingReviews: number | undefined;
  onOpen: (claimId: string, nodeId?: string) => void;
}) {
  const { t, i18n } = useTranslation();
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
  const selected =
    requestedGoal === null
      ? sub
        ? items.find((item) => item.item_id === sub)
        : items[0]
      : items.find((item) => item.kind === "choose_conjecture" && item.item_id === requestedGoal);
  const goalId = requestedGoal ?? (selected?.kind === "choose_conjecture" ? selected.item_id : null);
  // A decided item leaves the inbox, so its confirmation is kept here rather than on the item.
  const [notice, setNotice] = useState("");
  useEffect(() => setNotice(""), [sub]);
  return (
    <SectionLayout
      title={t("rail.review")}
      headerActions={<WorkbenchViewMenu current="review" pendingReviews={pendingReviews} />}
      groups={[
        {
          name: t("review.waiting", { count: items.length }),
          empty: inbox.isPending ? t("common.loading") : t("review.none"),
          entries: items.map((item) => {
            const evidence = item.kind === "review_evidence";
            const kind = reviewItemKindLabel(t, item);
            const summary = [
              evidence
                ? item.claim_title
                : typeof item.detail?.clarification_status === "string"
                  ? t(`clarifications.status.${item.detail.clarification_status}`)
                  : kind,
              timeText(i18n.language, item.waiting_since, true),
            ];
            return {
              key: item.item_id,
              label: evidence
                ? reviewResultName(item) || kind
                : item.claim_title ||
                  (typeof item.detail?.title === "string" ? item.detail.title : t("sidebar.untitled")),
              sub: summary.filter(Boolean).join(" · "),
              href: reviewItemHref(item),
              current: item === selected,
              dot: item.priority <= 1 ? "bad" : "warn",
            };
          }),
        },
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
      {notice && (
        <p className="page-notice" role="status">
          {notice}
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
      ) : !selected ? (
        <>
          <PageHead title={t("rail.review")} />
          <Empty>
            {inbox.isPending
              ? t("common.loading")
              : sub && !notice
                ? t("review.itemNoLongerWaiting")
                : t(openGoals.length ? "review.chooseOpenGoal" : "review.allDone")}
          </Empty>
        </>
      ) : selected.kind === "clarify_formalization" && clarification ? (
        <ClarificationReview
          key={selected.item_id}
          session={session}
          workspace={clarification.workspace_id}
          id={selected.item_id}
          onConfirmed={(kind) => setNotice(t(`clarifications.confirmed.${kind}`))}
        />
      ) : selected.kind === "review_evidence" && selected.claim_id ? (
        <EvidenceReview
          key={selected.item_id}
          session={session}
          item={selected}
          reviewWorkspace={inbox.data?.review_identity?.workspace_id}
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
  reviewWorkspace,
  onOpen,
  onDecided,
}: {
  session: string;
  item: InboxItem;
  reviewWorkspace?: string;
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
  const [reviewer, setReviewer] = useState(() => (reviewWorkspace ? "" : readReviewer()));
  const [reason, setReason] = useState("");
  const [choice, setChoice] = useState<(typeof DECISIONS)[number] | null>(null);
  const [state, setState] = useState<{ sending?: boolean; message?: string; error?: boolean }>({});
  const requestKeys = useRef(new Map<string, string>());
  const decide = async () => {
    if (state.sending) return;
    const action = choice;
    const actor = reviewer.trim();
    const rationale = reason.trim();
    if (!rationale || (!reviewWorkspace && !actor)) {
      setState({ message: t(reviewWorkspace ? "review.needReason" : "review.needNameAndReason"), error: true });
      return;
    }
    if (!action) {
      setState({ message: t("review.chooseDecision"), error: true });
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
        try {
          window.localStorage.setItem(REVIEWER_KEY, actor);
        } catch {
          // The name still applies to this decision.
        }
        await api(session, "/api/review", {
          data: { evidence_id: item.item_id, action, actor, reason: rationale },
        });
      }
      setState({});
      setReason("");
      onDecided(t(action === "approve" ? "review.approved" : "review.returned"));
      void queries.invalidateQueries({ queryKey: ["inbox"] });
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
              void decide();
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
            {!reviewWorkspace && (
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
            )}
            <fieldset className="evidence-decision-choice" disabled={state.sending}>
              <legend className="field-label">{t("review.decisionLabel")}</legend>
              {DECISIONS.map((action) => (
                <label key={action}>
                  <input
                    type="radio"
                    name="review-decision"
                    value={action}
                    checked={choice === action}
                    onChange={() => setChoice(action)}
                  />
                  {t(`review.${action}`)}
                </label>
              ))}
            </fieldset>
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
              <button
                type="submit"
                className="button primary"
                aria-keyshortcuts="Meta+Enter Control+Enter"
                disabled={state.sending}
              >
                {t("review.submitDecision")}
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
        <p className="inline-note">{t("review.cliNote")}</p>
        <pre className="code-block">{"research-multiplier review inbox"}</pre>
      </div>
    </>
  );
}
