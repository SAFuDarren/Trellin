import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useStopAutomation, type AutomationApproval, type AutomationTemplateId } from "../api/automation";
import { useProgress, type Policy } from "../api/sections";
import { AutomationDialog, AutomationTemplateCards, automationError } from "../components/AutomationDialog";
import { EmptyState } from "../components/EmptyState";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { statusText, timeText } from "../workbench/text";
import "../styles/automation.css";

const FILTERS = ["all", "active", "proposed", "stopped"] as const;

function bucket(policy: Policy): (typeof FILTERS)[number] {
  if (policy.stopped_at) return "stopped";
  if (!policy.approved_at) return "proposed";
  return "active";
}

function templateOf(policy: Policy): AutomationTemplateId {
  return policy.policy?.actions?.length ? "lean-reuse" : "observe";
}

type Opened = { template?: AutomationTemplateId; proposal?: Policy } | null;

export function ProgressPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const progress = useProgress(session);
  const [opened, setOpened] = useState<Opened>(null);
  const [notice, setNotice] = useState("");
  const policies = progress.data?.policies ?? [];
  const filter = (FILTERS as readonly string[]).includes(sub) ? (sub as (typeof FILTERS)[number]) : "all";
  const shown = policies.filter((policy) => filter === "all" || bucket(policy) === filter);
  const empty = progress.isSuccess && policies.length === 0;

  function approved(result: AutomationApproval) {
    setNotice(t(`automation.run.${result.run}`));
  }

  const newButton = (
    <button type="button" className="button primary" onClick={() => setOpened({})}>
      {t("automation.new")}
    </button>
  );

  return (
    <SectionLayout
      title={t("rail.progress")}
      // #94: the filters only help once rules exist.
      groups={
        empty
          ? []
          : [
              {
                entries: FILTERS.map((key) => ({
                  key,
                  label: t(`progressPage.filter.${key}`),
                  href: href("progress", key === "all" ? "" : key),
                  current: filter === key,
                  count: key === "all" ? policies.length : policies.filter((policy) => bucket(policy) === key).length,
                })),
              },
            ]
      }
    >
      <PageHead
        title={t(empty ? "rail.progress" : `progressPage.filter.${filter}`)}
        note={t("progressPage.note")}
        actions={empty ? undefined : newButton}
      />
      <div className="page-body">
        {notice && (
          <p className="automation-notice" role="status">
            {notice}
          </p>
        )}
        {progress.isError ? (
          // #328: an unavailable list says so and can be retried.
          <EmptyState
            title={t("progressPage.unavailable")}
            reason={t("progressPage.unavailableReason")}
            action={{ label: t("agentWork.retry"), onClick: () => void progress.refetch() }}
          />
        ) : progress.isPending ? (
          <Empty>{t("common.loading")}</Empty>
        ) : empty ? (
          <section className="automation-empty" aria-labelledby="automation-empty-title">
            <h2 id="automation-empty-title">{t("automation.empty.title")}</h2>
            <p>{t("automation.empty.body")}</p>
            <AutomationTemplateCards onChoose={(template) => setOpened({ template })} />
            <div className="automation-empty-actions">{newButton}</div>
          </section>
        ) : shown.length === 0 ? (
          <EmptyState
            title={t(`progressPage.noneIn.${filter}`)}
            reason={t("progressPage.noneReason")}
            action={{ label: t("progressPage.showAll"), href: href("progress") }}
          />
        ) : (
          <div className="policy-cards">
            {shown.map((policy) => (
              <PolicyCard
                key={policy.id}
                session={session}
                policy={policy}
                language={i18n.language}
                onOpen={onOpen}
                onApprove={() => setOpened({ proposal: policy })}
              />
            ))}
          </div>
        )}
      </div>
      {opened && (
        <AutomationDialog
          session={session}
          template={opened.template}
          proposal={
            opened.proposal && {
              policy_id: opened.proposal.id,
              claim_id: opened.proposal.claim_id,
              claim_title: opened.proposal.claim_title,
              fingerprint: opened.proposal.policy_sha256 ?? "",
              state: opened.proposal.state,
              approved: false,
              stopped: false,
              template: templateOf(opened.proposal),
              metric: opened.proposal.policy?.metric ?? null,
              actions: opened.proposal.policy?.actions ?? null,
              model_rounds: opened.proposal.policy?.lean_model_rounds ?? 0,
              budget_seconds: opened.proposal.policy?.budget_seconds ?? null,
              max_rounds: opened.proposal.policy?.max_rounds ?? null,
            }
          }
          onApproved={approved}
          onClose={() => setOpened(null)}
        />
      )}
    </SectionLayout>
  );
}

function PolicyCard({
  session,
  policy,
  language,
  onOpen,
  onApprove,
}: {
  session: string;
  policy: Policy;
  language: string;
  onOpen: (claimId: string) => void;
  onApprove: () => void;
}) {
  const { t } = useTranslation();
  const state = bucket(policy);
  const stop = useStopAutomation(session);
  const [stopping, setStopping] = useState(false);
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState("");
  const template = templateOf(policy);

  async function confirmStop() {
    if (!reason.trim() || stop.isPending) return;
    setFailure("");
    try {
      await stop.mutateAsync({ policy_id: policy.id, reason: reason.trim() });
      setStopping(false);
    } catch (error) {
      setFailure(automationError(t, error));
    }
  }

  return (
    <article className="policy-card">
      <header>
        <div className="name">
          <strong>{policy.claim_title || t("sidebar.untitled")}</strong>
          <span>{t(`automation.templates.${template}.name`)}</span>
        </div>
        <span className="pill" data-tone={state === "proposed" ? "warn" : undefined}>
          <span className="status-dot" data-tone={state === "active" ? "ok" : state === "proposed" ? "warn" : "none"} />
          {t(`progressPage.state.${state}`)}
        </span>
        <div className="automation-card-actions">
          {state === "proposed" && policy.policy_sha256 && (
            <button type="button" className="button primary" onClick={onApprove}>
              {t("automation.reviewAndApprove")}
            </button>
          )}
          {state !== "stopped" && !stopping && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setReason(t("automation.stopDefault"));
                setStopping(true);
              }}
            >
              {t("automation.stop")}
            </button>
          )}
          <button type="button" className="button" onClick={() => onOpen(policy.claim_id)}>
            {t("common.openInWorkbench")}
          </button>
        </div>
      </header>
      {stopping && (
        <div className="automation-stop" role="group" aria-label={t("automation.stop")}>
          <label>
            {t("automation.stopReason")}
            <input value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} />
          </label>
          <p>{t("automation.stopNote")}</p>
          <div className="choices">
            <button
              type="button"
              className="button danger"
              disabled={!reason.trim() || stop.isPending}
              onClick={() => void confirmStop()}
            >
              {t("automation.stopConfirm")}
            </button>
            <button type="button" className="button" onClick={() => setStopping(false)}>
              {t("common.cancel")}
            </button>
          </div>
          {failure && <p role="alert">{failure}</p>}
        </div>
      )}
      <dl className="facts">
        <dt>{t("progressPage.now")}</dt>
        <dd>
          {policy.state ? t(`progressPage.states.${policy.state}`, { defaultValue: statusText(t, policy.state) }) : "—"}
        </dd>
        <dt>{t("progressPage.lastRun")}</dt>
        <dd>
          {policy.last_run_at
            ? `${timeText(language, policy.last_run_at, true)} · ${t(`activityState.${policy.last_status}`, { defaultValue: policy.last_status ?? "" })}`
            : t("progressPage.never")}
          {policy.last_reason ? ` — ${policy.last_reason}` : ""}
        </dd>
        <dt>{t("progressPage.approved")}</dt>
        <dd>
          {policy.approved_at
            ? `${policy.approved_by ?? ""} · ${timeText(language, policy.approved_at, true)}`
            : t("progressPage.notApproved")}
        </dd>
        {policy.stopped_at && (
          <>
            <dt>{t("progressPage.stopped")}</dt>
            <dd>{`${timeText(language, policy.stopped_at, true)}${policy.stop_reason ? " — " + policy.stop_reason : ""}`}</dd>
          </>
        )}
      </dl>
    </article>
  );
}
