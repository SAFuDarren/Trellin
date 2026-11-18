import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api } from "../api/client";
import { useTeam, type Doctor, type Job, type Team } from "../api/sections";
import { Clock, Mark, Spin } from "../components/Icons";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { harnessName, modelName, timeText } from "../workbench/text";

const PAGES = ["runtimes", "agents", "usage", "jobs"] as const;
type Page = (typeof PAGES)[number];
const LIVE = new Set(["running"]);
const WAITING = new Set(["queued", "waiting_retry"]);

function duration(t: TFunction, ms: string | number | null | undefined): string {
  const value = Number(ms);
  if (!ms || !Number.isFinite(value)) return "—";
  const seconds = Math.round(value / 1000);
  return seconds < 60
    ? t("team.seconds", { count: seconds })
    : t("team.minutes", { minutes: Math.floor(seconds / 60), seconds: seconds % 60 });
}

function jobLabel(t: TFunction, job: Job): string {
  if (job.model_job || job.model) return modelName(t, job);
  if (job.kind === "verify")
    return (
      t("activityKind.lean") + (job.target ? " · " + t(`team.targets.${job.target}`, { defaultValue: job.target }) : "")
    );
  return t("activityKind.task") + " · " + job.kind;
}

export function TeamPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string, nodeId?: string) => void;
}) {
  const { t } = useTranslation();
  const team = useTeam(session);
  const page: Page = (PAGES as readonly string[]).includes(sub) ? (sub as Page) : "runtimes";
  const data = team.data;
  const running = data?.jobs.filter((job) => LIVE.has(job.state)).length ?? 0;
  const queued = data?.jobs.filter((job) => WAITING.has(job.state)).length ?? 0;
  const entry = (key: Page, count?: string | number, tone?: "good" | "live") => ({
    key,
    label: t(`team.pages.${key}`),
    href: href("team", key),
    current: page === key,
    count,
    countTone: tone,
  });
  return (
    <SectionLayout
      title={t("rail.team")}
      groups={[
        {
          name: t("team.groups.agents"),
          entries: [
            entry("agents", data ? data.agents.length + 1 : ""),
            entry("jobs", running || queued ? `${running}/${queued}` : "", running ? "live" : undefined),
          ],
        },
        {
          name: t("team.groups.resources"),
          entries: [entry("runtimes", data ? t("team.online", { count: 1 }) : "", "good"), entry("usage")],
        },
      ]}
    >
      <PageHead title={t(`team.pages.${page}`)} note={t(`team.notes.${page}`)} />
      {team.isError ? (
        <Empty>{team.error.message}</Empty>
      ) : !data ? (
        <Empty>{t("common.loading")}</Empty>
      ) : page === "runtimes" ? (
        <Runtimes session={session} data={data} running={running} />
      ) : page === "agents" ? (
        <Agents data={data} />
      ) : page === "usage" ? (
        <Usage data={data} />
      ) : (
        <Jobs data={data} onOpen={onOpen} />
      )}
    </SectionLayout>
  );
}

function Runtimes({ session, data, running }: { session: string; data: Team; running: number }) {
  const { t } = useTranslation();
  const [fresh, setFresh] = useState(0);
  const doctor = useQuery({
    queryKey: ["doctor", fresh],
    enabled: fresh > 0,
    queryFn: ({ signal }) => api<Doctor>(session, fresh > 1 ? "/api/doctor?fresh=1" : "/api/doctor", { signal }),
  });
  const ready = data.services.filter((service) => service.ready);
  const failing = doctor.data?.checks.filter((check) => check.status === "fail" || check.status === "warn") ?? [];
  return (
    <div className="page-body">
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{t("team.name")}</th>
              <th scope="col">{t("team.channel")}</th>
              <th scope="col" className="num">
                {t("team.runs")}
              </th>
              <th scope="col">{t("team.load")}</th>
              <th scope="col">{t("team.status")}</th>
            </tr>
          </thead>
          <tbody>
            <tr className="group-row">
              <td colSpan={5}>
                {t("team.machines")}
                <span>1</span>
              </td>
            </tr>
            <tr>
              <td>
                <b>{data.machine.name || t("team.unnamedMachine")}</b>{" "}
                <span className="muted">{t("team.thisMachine")}</span>
              </td>
              <td className="mono">
                {[
                  ...ready.filter((service) => service.transport === "cli").map((service) => service.provider),
                  "Lean",
                ].join(" · ")}
              </td>
              <td className="num">
                {data.agents.reduce((sum, agent) => sum + Number(agent.runs), 0) + data.verifier.runs}
              </td>
              <td>{running ? t("team.runningNow", { count: running }) : t("team.idle")}</td>
              <td>
                <span className="pill">
                  <span className="status-dot" data-tone="ok" />
                  {t("team.online", { count: 1 })}
                </span>
              </td>
            </tr>
            <tr>
              <td colSpan={5} className="muted">
                {t("team.remoteSoon")}
              </td>
            </tr>
            <tr className="group-row">
              <td colSpan={5}>
                {t("team.services")}
                <span>{data.services.length}</span>
              </td>
            </tr>
            {data.services.map((service) => (
              <tr key={service.provider}>
                <td>
                  <b>{t(`team.providers.${service.provider}`, { defaultValue: service.provider })}</b>
                </td>
                <td className="mono">{t(`team.via.${service.via}`, { name: service.detail })}</td>
                <td className="num">{service.runs}</td>
                <td className="muted">{service.transport}</td>
                <td>
                  <span className="pill" data-tone={service.ready === false ? "warn" : undefined}>
                    <span
                      className="status-dot"
                      data-tone={service.ready ? "ok" : service.ready === false ? "warn" : "none"}
                    />
                    {service.ready
                      ? t("team.ready")
                      : service.ready === false
                        ? t("team.notConfigured")
                        : t("team.unchecked")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="callout">
        <div className="body">
          <strong>{t("team.check.title")}</strong>
          <span>
            {doctor.isFetching
              ? t("team.check.running")
              : doctor.data
                ? failing.length
                  ? t("team.check.problems", { count: failing.length })
                  : t("team.check.allGood", {
                      count: doctor.data.checks.filter((check) => check.status === "ok").length,
                    })
                : doctor.isError
                  ? doctor.error.message
                  : t("team.check.hint")}
          </span>
        </div>
        <button
          type="button"
          className="button"
          disabled={doctor.isFetching}
          onClick={() => setFresh((value) => value + 1)}
        >
          {doctor.data ? t("team.check.again") : t("team.check.run")}
        </button>
      </div>
      {doctor.data && (
        <ul className="checks">
          {doctor.data.checks.map((check) => (
            <li key={check.id}>
              <span
                className="status-dot"
                data-tone={
                  check.status === "ok"
                    ? "ok"
                    : check.status === "fail"
                      ? "bad"
                      : check.status === "warn"
                        ? "warn"
                        : "none"
                }
              />
              <span>{check.title}</span>
              <span className="detail">
                {check.status === "ok" || !check.fix ? check.detail : `${check.detail} — ${check.fix}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Agents({ data }: { data: Team }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="page-body">
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{t("team.name")}</th>
              <th scope="col">{t("team.channel")}</th>
              <th scope="col" className="num">
                {t("team.runs")}
              </th>
              <th scope="col">{t("team.lastRun")}</th>
              <th scope="col" className="num">
                {t("team.average")}
              </th>
              <th scope="col" className="num">
                {t("team.failures")}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.agents.map((agent) => (
              <tr key={agent.provider + (agent.model ?? "")}>
                <td className="mono">{modelName(t, agent)}</td>
                <td className="muted">{harnessName(agent.provider)}</td>
                <td className="num">{agent.runs}</td>
                <td className="muted">{timeText(i18n.language, agent.last_at, true)}</td>
                <td className="num">{duration(t, agent.average_ms)}</td>
                <td className="num">{agent.failures}</td>
              </tr>
            ))}
            <tr>
              <td>{t("team.verifier")}</td>
              <td className="muted">{"Lean"}</td>
              <td className="num">{data.verifier.runs}</td>
              <td className="muted">{timeText(i18n.language, data.verifier.last_at, true)}</td>
              <td className="num">{duration(t, data.verifier.average_ms)}</td>
              <td className="num">{data.verifier.failures}</td>
            </tr>
          </tbody>
        </table>
      </div>
      {data.agents.length === 0 && <p className="inline-note">{t("team.noModels")}</p>}
    </div>
  );
}

function Usage({ data }: { data: Team }) {
  const { t } = useTranslation();
  const tokens = data.agents.reduce(
    (sum, agent) => sum + Number(agent.input_tokens ?? 0) + Number(agent.output_tokens ?? 0),
    0,
  );
  const cost = data.agents.reduce((sum, agent) => sum + Number(agent.cost_usd ?? 0), 0);
  const limit =
    data.budget.policy && typeof data.budget.policy.limit_usd === "string" ? data.budget.policy.limit_usd : null;
  return (
    <div className="page-body">
      <div className="stats">
        <div className="stat">
          <span className="label">{t("team.usage.month")}</span>
          <span className="value">{"$" + Number(data.budget.month.reserved_usd ?? 0).toFixed(2)}</span>
          <span className="note">
            {limit ? t("team.usage.limit", { limit: "$" + Number(limit).toFixed(2) }) : t("team.usage.noLimit")}
          </span>
        </div>
        <div className="stat">
          <span className="label">{t("team.usage.tokens")}</span>
          <span className="value">{tokens.toLocaleString()}</span>
          <span className="note">{t("team.usage.allTime")}</span>
        </div>
        <div className="stat">
          <span className="label">{t("team.usage.cost")}</span>
          <span className="value">{"$" + cost.toFixed(2)}</span>
          <span className="note">{t("team.usage.recorded")}</span>
        </div>
      </div>
      <p className="inline-note">{t("team.usage.howToLimit")}</p>
      <pre className="code-block">
        {'research-multiplier research model-budget limit --usd 20 --actor NAME --reason "..."'}
      </pre>
    </div>
  );
}

function Jobs({ data, onOpen }: { data: Team; onOpen: (claimId: string, nodeId?: string) => void }) {
  const { t, i18n } = useTranslation();
  if (!data.jobs.length) return <Empty>{t("team.noJobs")}</Empty>;
  return (
    <div className="page-body">
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{t("team.status")}</th>
              <th scope="col">{t("team.what")}</th>
              <th scope="col">{t("team.claim")}</th>
              <th scope="col">{t("team.when")}</th>
              <th scope="col" className="num">
                {t("team.took")}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.jobs.map((job) => (
              <tr
                key={job.id}
                data-row="true"
                tabIndex={0}
                onClick={() => job.claim_id && onOpen(job.claim_id, job.node_id ?? undefined)}
              >
                <td>
                  <span className="pill">
                    {LIVE.has(job.state) ? (
                      <Spin />
                    ) : WAITING.has(job.state) ? (
                      <Clock />
                    ) : (
                      <Mark tone={job.state === "failed" || job.failure_code ? "bad" : "ok"} />
                    )}
                    {t(`activityState.${job.state}`, { defaultValue: job.state })}
                  </span>
                </td>
                <td className={job.model ? "mono" : undefined}>{jobLabel(t, job)}</td>
                <td>{job.claim_title || t("sidebar.untitled")}</td>
                <td className="muted">
                  {timeText(i18n.language, job.running_since || job.submitted_at || job.enqueued_at, true)}
                </td>
                <td className="num">{duration(t, job.execution_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
