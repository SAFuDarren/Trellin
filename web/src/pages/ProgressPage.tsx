import { useTranslation } from "react-i18next";
import { useProgress, type Policy } from "../api/sections";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { statusText, timeText } from "../workbench/text";

const FILTERS = ["all", "active", "proposed", "stopped"] as const;

function bucket(policy: Policy): (typeof FILTERS)[number] {
  if (policy.stopped_at) return "stopped";
  if (!policy.approved_at) return "proposed";
  return "active";
}

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
  const policies = progress.data?.policies ?? [];
  const filter = (FILTERS as readonly string[]).includes(sub) ? (sub as (typeof FILTERS)[number]) : "all";
  const shown = policies.filter((policy) => filter === "all" || bucket(policy) === filter);
  return (
    <SectionLayout
      title={t("rail.progress")}
      groups={[
        {
          entries: FILTERS.map((key) => ({
            key,
            label: t(`progressPage.filter.${key}`),
            href: href("progress", key === "all" ? "" : key),
            current: filter === key,
            count: key === "all" ? policies.length : policies.filter((policy) => bucket(policy) === key).length,
          })),
        },
      ]}
    >
      <PageHead title={t(`progressPage.filter.${filter}`)} note={t("progressPage.note")} />
      <div className="page-body">
        <p className="page-note">{t("progressPage.explain")}</p>
        {progress.isError ? (
          <Empty>{progress.error.message}</Empty>
        ) : shown.length === 0 ? (
          <Empty>{progress.isPending ? t("common.loading") : t("progressPage.none")}</Empty>
        ) : (
          <div className="rules">
            {shown.map((policy) => {
              const state = bucket(policy);
              return (
                <article className="rule" key={policy.id}>
                  <header>
                    <div className="name">
                      <strong>{policy.claim_title || t("sidebar.untitled")}</strong>
                      <span>
                        {t("progressPage.summary", {
                          metric: policy.policy?.metric ?? "—",
                          rounds: policy.policy?.lean?.model_rounds ?? 0,
                        })}
                      </span>
                    </div>
                    <span className="pill" data-tone={state === "proposed" ? "warn" : undefined}>
                      <span
                        className="status-dot"
                        data-tone={state === "active" ? "ok" : state === "proposed" ? "warn" : "none"}
                      />
                      {t(`progressPage.state.${state}`)}
                    </span>
                    <button type="button" className="button" onClick={() => onOpen(policy.claim_id)}>
                      {t("common.openInWorkbench")}
                    </button>
                  </header>
                  <dl className="facts">
                    <dt>{t("progressPage.now")}</dt>
                    <dd>
                      {policy.state
                        ? t(`progressPage.states.${policy.state}`, { defaultValue: statusText(t, policy.state) })
                        : "—"}
                    </dd>
                    <dt>{t("progressPage.lastRun")}</dt>
                    <dd>
                      {policy.last_run_at
                        ? `${timeText(i18n.language, policy.last_run_at, true)} · ${t(`activityState.${policy.last_status}`, { defaultValue: policy.last_status ?? "" })}`
                        : t("progressPage.never")}
                      {policy.last_reason ? ` — ${policy.last_reason}` : ""}
                    </dd>
                    <dt>{t("progressPage.approved")}</dt>
                    <dd>
                      {policy.approved_at
                        ? `${policy.approved_by ?? ""} · ${timeText(i18n.language, policy.approved_at, true)}`
                        : t("progressPage.notApproved")}
                    </dd>
                    {policy.stopped_at && (
                      <>
                        <dt>{t("progressPage.stopped")}</dt>
                        <dd>{`${timeText(i18n.language, policy.stopped_at, true)}${policy.stop_reason ? " — " + policy.stop_reason : ""}`}</dd>
                      </>
                    )}
                  </dl>
                </article>
              );
            })}
          </div>
        )}
        <p className="inline-note">{t("progressPage.cli")}</p>
        <pre className="code-block">
          {
            'research-multiplier advance propose …\nresearch-multiplier advance approve POLICY_UUID --actor NAME --reason "…"'
          }
        </pre>
      </div>
    </SectionLayout>
  );
}
