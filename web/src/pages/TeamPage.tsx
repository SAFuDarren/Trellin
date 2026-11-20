import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api } from "../api/client";
import { useTeam, type Doctor, type Job, type Team } from "../api/sections";
import { Clock, Mark, Spin } from "../components/Icons";
import { ModelConnections } from "../components/ModelConnections";
import { PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { harnessName, modelName, prettyModel, timeText } from "../workbench/text";
import "../styles/team.css";

const PAGES = ["runtimes", "agents", "models", "usage", "jobs"] as const;
type Page = (typeof PAGES)[number];
type Option = { value: string; label: string };
const WAITING = new Set(["queued", "waiting_retry"]);

function measurement(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function recordedSum(values: (string | number | null | undefined)[]): number | null {
  const recorded = values.map(measurement).filter((value) => value !== null);
  return recorded.length ? recorded.reduce((sum, value) => sum + value, 0) : null;
}

function duration(t: TFunction, ms: string | number | null | undefined): string {
  const value = measurement(ms);
  if (value === null) return t("team.unknown");
  const seconds = Math.round(value / 1000);
  return seconds < 60
    ? t("team.seconds", { count: seconds })
    : t("team.minutes", { minutes: Math.floor(seconds / 60), seconds: seconds % 60 });
}

function providerName(t: TFunction, provider: string): string {
  return t(`team.providers.${provider}`, { defaultValue: harnessName(provider) });
}

function jobLabel(t: TFunction, job: Job): string {
  if (job.model_job || job.model) return modelName(t, job);
  if (job.kind === "verify")
    return (
      t("activityKind.lean") + (job.target ? " · " + t(`team.targets.${job.target}`, { defaultValue: job.target }) : "")
    );
  return t("activityKind.task") + " · " + job.kind;
}

function includes(query: string, ...values: (string | null | undefined)[]) {
  const needle = query.trim().toLowerCase();
  return !needle || values.some((value) => value?.toLowerCase().includes(needle));
}

function Filters({
  searchLabel,
  filterLabel,
  all,
  query,
  onSearch,
  filter,
  onFilter,
  options,
}: {
  searchLabel: string;
  filterLabel: string;
  all: string;
  query: string;
  onSearch: (query: string) => void;
  filter: string;
  onFilter: (filter: string) => void;
  options: Option[];
}) {
  return (
    <div className="team-tools">
      <label className="search-field">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5" />
          <path d="m20 20-4.2-4.2" />
        </svg>
        <input
          type="search"
          aria-label={searchLabel}
          placeholder={searchLabel}
          value={query}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
      <select aria-label={filterLabel} value={filter} onChange={(event) => onFilter(event.target.value)}>
        <option value="">{all}</option>
        {options.map(({ value, label }) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
    </div>
  );
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
  const running = data?.jobs.filter((job) => job.state === "running").length ?? 0;
  const entry = (key: Page, count?: number) => ({
    key,
    label: t(`team.pages.${key}`),
    href: href("team", key),
    current: page === key,
    count,
    countTone: key === "jobs" && running > 0 ? ("live" as const) : undefined,
  });
  return (
    <SectionLayout
      title={t("rail.team")}
      groups={[
        {
          name: t("team.groups.agents"),
          entries: [entry("agents"), entry("models"), entry("jobs", running || undefined)],
        },
        { name: t("team.groups.resources"), entries: [entry("runtimes"), entry("usage")] },
      ]}
    >
      <PageHead title={t(`team.pages.${page}`)} note={t(`team.notes.${page}`)} />
      {page === "models" ? (
        <div className="page-body team-body">
          <ModelConnections session={session} />
        </div>
      ) : team.isError ? (
        <p className="page-empty" role="alert">
          {t("team.unavailable")}
        </p>
      ) : !data ? (
        <p className="page-empty" role="status">
          {t("common.loading")}
        </p>
      ) : page === "runtimes" ? (
        <Runtimes session={session} data={data} />
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

function Runtimes({ session, data }: { session: string; data: Team }) {
  const { t } = useTranslation();
  const [fresh, setFresh] = useState(0);
  const doctor = useQuery({
    queryKey: ["doctor", fresh],
    enabled: fresh > 0,
    queryFn: ({ signal }) => api<Doctor>(session, fresh > 1 ? "/api/doctor?fresh=1" : "/api/doctor", { signal }),
  });
  const failing = doctor.data?.checks.filter((check) => check.status === "fail" || check.status === "warn") ?? [];
  return (
    <div className="page-body team-body">
      <div className="table-wrap">
        <table className="table team-table">
          <thead>
            <tr>
              <th scope="col">{t("team.computer")}</th>
              <th scope="col">{t("team.system")}</th>
              <th scope="col">{t("team.architecture")}</th>
              <th scope="col">{t("team.python")}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">
                <strong>{data.machine.name || t("team.unnamedMachine")}</strong>
                <span className="team-secondary">{t("team.thisMachine")}</span>
              </th>
              <td>{data.machine.system || t("team.unknown")}</td>
              <td>{data.machine.arch || t("team.unknown")}</td>
              <td>{data.machine.python || t("team.unknown")}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <ModelConnections session={session} />
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
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState("");
  const agents = data.agents.map((agent) => ({
    ...agent,
    name: providerName(t, agent.provider),
    key: agent.provider + "/" + (agent.model ?? ""),
  }));
  const verifier = {
    ...data.verifier,
    name: t("team.verifier"),
    provider: "lean",
    model: null,
    key: "verifier",
  };
  const entries = data.verifier.runs > 0 ? [...agents, verifier] : agents;
  const providers = new Map(entries.map(({ provider: value, name }) => [value, name]));
  if (provider && !providers.has(provider)) providers.set(provider, providerName(t, provider));
  const filtered = entries.filter(
    (agent) =>
      (!provider || provider === agent.provider) &&
      includes(query, agent.name, agent.provider, agent.model, agent.model && prettyModel(agent.model)),
  );
  return (
    <div className="page-body team-body">
      <Filters
        searchLabel={t("team.filters.searchAgents")}
        filterLabel={t("team.filters.provider")}
        all={t("team.filters.allProviders")}
        query={query}
        onSearch={setQuery}
        filter={provider}
        onFilter={setProvider}
        options={[...providers].map(([value, label]) => ({ value, label }))}
      />
      {filtered.length ? (
        <div className="table-wrap">
          <table className="table team-table">
            <thead>
              <tr>
                <th scope="col">{t("team.agent")}</th>
                <th scope="col">{t("team.recordedModel")}</th>
                <th scope="col">{t("team.lastRun")}</th>
                <th scope="col" className="num">
                  {t("team.runs")}
                </th>
                <th scope="col" className="num">
                  {t("team.average")}
                </th>
                <th scope="col" className="num">
                  {t("team.failures")}
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((agent) => (
                <tr key={agent.key}>
                  <th scope="row">{agent.name}</th>
                  <td>{agent.model ? prettyModel(agent.model) : t("team.unknown")}</td>
                  <td>{timeText(i18n.language, agent.last_at, true) || t("team.unknown")}</td>
                  <td className="num">{agent.runs}</td>
                  <td className="num">{duration(t, agent.average_ms)}</td>
                  <td className="num">{agent.failures}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="team-empty" role="status">
          {t(entries.length ? "team.noResults" : "team.noAgents")}
        </p>
      )}
    </div>
  );
}

function Usage({ data }: { data: Team }) {
  const { t, i18n } = useTranslation();
  const count = new Intl.NumberFormat(i18n.language);
  const usd = new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD" });
  const tokens = recordedSum(data.agents.flatMap((agent) => [agent.input_tokens, agent.output_tokens]));
  const cost = recordedSum(data.agents.map((agent) => agent.cost_usd));
  const limit = typeof data.budget.policy?.limit_usd === "string" ? measurement(data.budget.policy.limit_usd) : null;
  const reserved = measurement(data.budget.month.reserved_usd);
  const unknown = t("team.unknown");
  return (
    <div className="page-body team-body">
      <dl className="team-usage">
        <div>
          <dt>{t("team.usage.month")}</dt>
          <dd>
            {reserved === null ? unknown : usd.format(reserved)}
            <span className="team-secondary">
              {limit === null ? t("team.usage.noLimit") : t("team.usage.limit", { limit: usd.format(limit) })}
            </span>
          </dd>
        </div>
        <div>
          <dt>{t("team.usage.tokens")}</dt>
          <dd>
            {tokens === null ? unknown : count.format(tokens)}
            <span className="team-secondary">{t("team.usage.allTime")}</span>
          </dd>
        </div>
        <div>
          <dt>{t("team.usage.cost")}</dt>
          <dd>
            {cost === null ? unknown : usd.format(cost)}
            <span className="team-secondary">{t("team.usage.recorded")}</span>
          </dd>
        </div>
      </dl>
      <p className="inline-note">{t("team.usage.howToLimit")}</p>
      <pre className="code-block">
        {'research-multiplier research model-budget limit --usd 20 --actor NAME --reason "..."'}
      </pre>
    </div>
  );
}

function Jobs({ data, onOpen }: { data: Team; onOpen: (claimId: string, nodeId?: string) => void }) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  const stateLabel = (value: string) => t(`activityState.${value}`, { defaultValue: value });
  const states = [...new Set([...data.jobs.map((job) => job.state), state])].filter(Boolean);
  const filtered = data.jobs.filter(
    (job) =>
      (!state || state === job.state) && includes(query, job.claim_title, job.model, job.provider, jobLabel(t, job)),
  );
  return (
    <div className="page-body team-body">
      <Filters
        searchLabel={t("team.filters.searchJobs")}
        filterLabel={t("team.filters.state")}
        all={t("team.filters.allStates")}
        query={query}
        onSearch={setQuery}
        filter={state}
        onFilter={setState}
        options={states.map((value) => ({ value, label: stateLabel(value) }))}
      />
      {filtered.length ? (
        <div className="table-wrap">
          <table className="table team-table">
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
              {filtered.map((job) => {
                const claimId = job.claim_id;
                const claim = job.claim_title || (claimId ? t("sidebar.untitled") : t("team.unknown"));
                return (
                  <tr key={job.id}>
                    <td>
                      <span className="team-job-state">
                        {job.state === "running" ? (
                          <Spin />
                        ) : WAITING.has(job.state) ? (
                          <Clock />
                        ) : (
                          <Mark
                            tone={
                              job.state === "failed" || job.failure_code ? "bad" : job.state === "succeeded" ? "ok" : ""
                            }
                          />
                        )}
                        {stateLabel(job.state)}
                      </span>
                    </td>
                    <td>{jobLabel(t, job)}</td>
                    <td>
                      {claimId ? (
                        <button
                          type="button"
                          className="relation team-relation"
                          onClick={() => onOpen(claimId, job.node_id ?? undefined)}
                        >
                          {claim}
                        </button>
                      ) : (
                        claim
                      )}
                    </td>
                    <td>
                      {timeText(i18n.language, job.running_since || job.submitted_at || job.enqueued_at, true) ||
                        t("team.unknown")}
                    </td>
                    <td className="num">{duration(t, job.execution_ms)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="team-empty" role="status">
          {t(data.jobs.length ? "team.noResults" : "team.noJobs")}
        </p>
      )}
    </div>
  );
}
