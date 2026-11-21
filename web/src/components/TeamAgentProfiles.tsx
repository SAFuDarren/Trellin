import { useId, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiError } from "../api/client";
import {
  connectionChoiceLabel,
  connectionModelLabel,
  dispatchableConnection,
  useModelConnections,
  type ModelConnection,
} from "../api/modelConnections";
import { AGENT_AVATARS, useSaveTeamAgent, useTeamAgents, type TeamAgent } from "../api/teamAgents";
import { href } from "../shell/route";
import { ModelConnections } from "./ModelConnections";
import { TeamAgentAvatar } from "./TeamAgentAvatar";
import "../styles/team-agent-profiles.css";

const EMPTY_CONNECTIONS: ModelConnection[] = [];
const STARTER_SLOTS = [0, 1, 2] as const;

function runtimeStatus(t: TFunction, connection?: ModelConnection) {
  if (!connection || !dispatchableConnection(connection)) return t("teamAgents.unavailableRuntime");
  return t(`modelConnections.status.${connection.status}`);
}

function RuntimeIdentity({ connection }: { connection: ModelConnection }) {
  const { t } = useTranslation();
  const label = connectionChoiceLabel(t, connection);
  const model = connectionModelLabel(t, connection);
  return (
    <span>
      <strong>{label}</strong>
      {!label.endsWith(` · ${model}`) && <span className="team-secondary">{model}</span>}
    </span>
  );
}

export function TeamAgentProfiles({ session }: { session: string }) {
  const { t, i18n } = useTranslation();
  const heading = useId();
  const profiles = useTeamAgents(session);
  const connections = useModelConnections(session);
  const returnFocus = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<TeamAgent | null>(null);
  const runtimes = new Map(connections.data?.connections.map((connection) => [connection.id, connection]));
  const agents = profiles.data?.agents ?? [];
  const needle = query.trim().toLocaleLowerCase();
  const filtered = agents
    .filter((agent) => [agent.name, agent.description].some((value) => value.toLocaleLowerCase().includes(needle)))
    .sort((a, b) => a.name.localeCompare(b.name, i18n.language));

  function addAgent(button: HTMLButtonElement) {
    returnFocus.current = button;
    setNotice("");
    setEditing({
      id: crypto.randomUUID(),
      revision: 0,
      connection_id: "",
      name: "",
      description: "",
      avatar: "initials",
      instructions: "",
      starters: [],
    });
  }

  return (
    <section className="page-body team-body team-agent-profiles" aria-labelledby={heading}>
      <header className="team-heading">
        <h3 id={heading}>{t("teamAgents.title")}</h3>
        <button
          type="button"
          className="button primary"
          disabled={profiles.isError}
          onClick={(event) => addAgent(event.currentTarget)}
        >
          {t("teamAgents.add")}
        </button>
      </header>
      <p role={notice ? "status" : undefined} aria-live="polite" className="team-agent-feedback">
        {profiles.isPending
          ? t("common.loading")
          : profiles.isError
            ? t("teamAgents.listFailed")
            : notice || (!agents.length ? t("teamAgents.empty") : "")}
      </p>
      {profiles.isError && (
        <div className="choices">
          <button type="button" className="button" onClick={() => void profiles.refetch()}>
            {t("teamAgents.retry")}
          </button>
        </div>
      )}
      {agents.length > 0 && (
        <div className="team-tools">
          <label className="search-field">
            <input
              type="search"
              placeholder={t("teamAgents.search")}
              aria-label={t("teamAgents.search")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        </div>
      )}
      {filtered.length > 0 ? (
        <div className="table-wrap">
          <table className="table team-table team-agent-table">
            <thead>
              <tr>
                <th scope="col">{t("team.name")}</th>
                <th scope="col">{t("teamAgents.runtime")}</th>
                <th scope="col">{t("team.connectionStatus")}</th>
                <th scope="col">{t("team.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((agent) => {
                const runtime = runtimes.get(agent.connection_id);
                return (
                  <tr key={agent.id}>
                    <th scope="row">
                      <span className="team-agent-identity">
                        <TeamAgentAvatar avatar={agent.avatar} name={agent.name} />
                        <span>
                          {agent.name}
                          {agent.description && <span className="team-secondary">{agent.description}</span>}
                        </span>
                      </span>
                    </th>
                    <td>{runtime ? <RuntimeIdentity connection={runtime} /> : t("team.unknown")}</td>
                    <td>
                      {connections.isPending
                        ? t("common.loading")
                        : connections.isError
                          ? t("teamAgents.runtimesFailed")
                          : runtimeStatus(t, runtime)}
                    </td>
                    <td>
                      <div className="team-agent-work-actions">
                        <a
                          className="button"
                          href={href("claims", "agent-work/new/agent/" + agent.id)}
                          aria-label={t("agentWork.assignNamed", { name: agent.name })}
                        >
                          {t("agentWork.assign")}
                        </a>
                        <button
                          type="button"
                          className="button"
                          aria-label={t("teamAgents.editNamed", { name: agent.name })}
                          onClick={(event) => {
                            returnFocus.current = event.currentTarget;
                            setNotice("");
                            setEditing(agent);
                          }}
                        >
                          {t("teamAgents.edit")}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : agents.length > 0 ? (
        <p className="team-agent-feedback">{t("team.noResults")}</p>
      ) : null}
      {editing && (
        <TeamAgentWizard
          session={session}
          initial={editing}
          initialStep={editing.revision ? "details" : "runtime"}
          onClose={() => {
            setEditing(null);
            returnFocus.current?.focus();
          }}
          onSaved={(agent) => {
            setQuery("");
            setNotice(t("teamAgents.saved", { name: agent.name }));
          }}
        />
      )}
    </section>
  );
}

function TeamAgentWizard({
  session,
  initial,
  initialStep,
  onSaved,
  onClose,
}: {
  session: string;
  initial: TeamAgent;
  initialStep: "runtime" | "details";
  onSaved: (agent: TeamAgent) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const runtimeManagement = useRef<HTMLButtonElement>(null);
  const submitting = useRef(false);
  const title = useId();
  const [step, setStep] = useState(initialStep);
  const [draft, setDraft] = useState(initial);
  const [failure, setFailure] = useState("");
  const [managing, setManaging] = useState(false);
  const connections = useModelConnections(session);
  const save = useSaveTeamAgent(session);
  const runtimes = connections.data?.connections ?? EMPTY_CONNECTIONS;
  const available = runtimes.filter(dispatchableConnection);
  const runtime = available.find((connection) => connection.id === draft.connection_id);
  const ready = Boolean(runtime) && !connections.isError;
  const busy = save.isPending;
  const validName = Boolean(draft.name.trim());
  const invalidName = !validName && draft.name.length > 0;

  useLayoutEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    heading.current?.focus();
  }, [step]);

  function update(values: Partial<TeamAgent>) {
    setDraft((current) => ({ ...current, ...values }));
    setFailure("");
  }

  function changeStep(next: "runtime" | "details") {
    setFailure("");
    setStep(next);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (step !== "details" || submitting.current) return;
    if (!ready) {
      setFailure(t("teamAgents.connectionChanged"));
      return;
    }
    if (!validName) {
      setFailure(t("teamAgents.nameRequired"));
      return;
    }
    submitting.current = true;
    setFailure("");
    try {
      const { agent } = await save.mutateAsync({
        ...draft,
        name: draft.name.trim(),
        description: draft.description.trim(),
        instructions: draft.instructions.trim(),
        starters: draft.starters.map((value) => value.trim()).filter(Boolean),
      });
      onSaved(agent);
      dialog.current?.close();
    } catch (error) {
      const code = error instanceof ApiError ? (error.code ?? error.message) : "";
      setFailure(
        t(`teamAgents.errors.${code.replace(/^team_agents\./, "")}`, { defaultValue: t("teamAgents.saveFailed") }),
      );
      if (code === "team_agents.connection_unavailable") void connections.refetch();
    } finally {
      submitting.current = false;
    }
  }

  return (
    <>
      <dialog
        ref={dialog}
        className="project-entry team-agent-dialog"
        aria-labelledby={title}
        onClose={onClose}
        onCancel={(event) => {
          if (submitting.current) event.preventDefault();
        }}
      >
        <header className="project-entry-head">
          <h2 ref={heading} id={title} tabIndex={-1}>
            {t(step === "runtime" ? "teamAgents.chooseRuntime" : "teamAgents.configure")}
          </h2>
          {step === "details" && draft.name.trim() && <TeamAgentAvatar avatar={draft.avatar} name={draft.name} />}
        </header>
        <form className="project-entry-body project-entry-form" onSubmit={(event) => void submit(event)}>
          {step === "runtime" ? (
            <>
              {connections.isPending && <p aria-live="polite">{t("common.loading")}</p>}
              {connections.isError && <p role="alert">{t("teamAgents.runtimesFailed")}</p>}
              {connections.isSuccess && !available.length && (
                <p className="project-entry-note">{t("teamAgents.noRuntime")}</p>
              )}
              {runtimes.length > 0 && (
                <fieldset className="team-agent-runtime-list">
                  <legend className="sr-only">{t("teamAgents.runtime")}</legend>
                  {runtimes.map((connection) => (
                    <label className="team-agent-runtime" key={connection.id}>
                      <input
                        type="radio"
                        name="team-agent-runtime"
                        value={connection.id}
                        disabled={!dispatchableConnection(connection)}
                        checked={connection.id === draft.connection_id}
                        onChange={() => update({ connection_id: connection.id })}
                      />
                      <RuntimeIdentity connection={connection} />
                      <span className="team-agent-runtime-status">{runtimeStatus(t, connection)}</span>
                    </label>
                  ))}
                </fieldset>
              )}
              <div className="choices">
                <button type="button" ref={runtimeManagement} className="button" onClick={() => setManaging(true)}>
                  {t("teamAgents.manageRuntimes")}
                </button>
                {connections.isError && (
                  <button type="button" className="button" onClick={() => void connections.refetch()}>
                    {t("teamAgents.retry")}
                  </button>
                )}
              </div>
            </>
          ) : (
            <>
              <p className="project-entry-note">
                {runtime
                  ? t("teamAgents.runtimeSummary", {
                      runtime: t(`team.providers.${runtime.provider}`, { defaultValue: runtime.provider }),
                      model: connectionModelLabel(t, runtime),
                    })
                  : t(connections.isPending ? "common.loading" : "teamAgents.connectionChanged")}
              </p>
              <div className="project-entry-columns">
                <label>
                  {t("team.name")}
                  <input
                    value={draft.name}
                    onChange={(event) => update({ name: event.target.value })}
                    required
                    maxLength={80}
                    aria-invalid={invalidName || undefined}
                    disabled={busy}
                  />
                </label>
                <div className="team-agent-field">
                  <label htmlFor={`${title}-avatar`}>{t("teamAgents.avatar")}</label>
                  <select
                    id={`${title}-avatar`}
                    value={draft.avatar}
                    onChange={(event) => {
                      const avatar = AGENT_AVATARS.find((option) => option === event.target.value);
                      if (avatar) update({ avatar });
                    }}
                    disabled={busy}
                  >
                    {AGENT_AVATARS.map((avatar) => (
                      <option key={avatar} value={avatar}>
                        {t(`teamAgents.avatars.${avatar}`)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <label>
                {t("teamAgents.description")}
                <textarea
                  value={draft.description}
                  onChange={(event) => update({ description: event.target.value })}
                  maxLength={400}
                  rows={2}
                  disabled={busy}
                />
              </label>
              <label>
                {t("teamAgents.instructions")}
                <textarea
                  value={draft.instructions}
                  onChange={(event) => update({ instructions: event.target.value })}
                  maxLength={8000}
                  rows={4}
                  disabled={busy}
                />
              </label>
              <fieldset className="team-agent-starters">
                <legend>{t("teamAgents.starters")}</legend>
                {STARTER_SLOTS.map((index) => (
                  <label key={index}>
                    {t("teamAgents.starter", { number: index + 1 })}
                    <input
                      value={draft.starters[index] ?? ""}
                      onChange={(event) => {
                        const starters = STARTER_SLOTS.map((slot) =>
                          index === slot ? event.target.value : (draft.starters[slot] ?? ""),
                        );
                        update({ starters });
                      }}
                      maxLength={240}
                      disabled={busy}
                    />
                  </label>
                ))}
              </fieldset>
              <p className="project-entry-note">{t("teamAgents.scope")}</p>
            </>
          )}
          {(failure || (step === "details" && invalidName)) && (
            <p role="alert" className="project-entry-status">
              {failure || t("teamAgents.nameRequired")}
            </p>
          )}
          <footer className="team-agent-footer">
            {step === "details" && (
              <button className="button" type="button" disabled={busy} onClick={() => changeStep("runtime")}>
                {t("teamAgents.back")}
              </button>
            )}
            <span className="team-agent-footer-spacer" />
            <button
              className="button"
              type="button"
              disabled={busy}
              onClick={() => {
                if (!submitting.current) dialog.current?.close();
              }}
            >
              {t("common.cancel")}
            </button>
            {step === "runtime" ? (
              <button
                className="button primary"
                type="button"
                disabled={!ready}
                onClick={(event) => {
                  event.preventDefault();
                  changeStep("details");
                }}
              >
                {t("teamAgents.next")}
              </button>
            ) : (
              <button className="button primary" type="submit" disabled={busy || !ready || !validName}>
                {t(busy ? "teamAgents.saving" : initial.revision ? "teamAgents.save" : "teamAgents.create")}
              </button>
            )}
          </footer>
        </form>
      </dialog>
      {managing && (
        <ModelConnections
          session={session}
          onClose={() => {
            setManaging(false);
            runtimeManagement.current?.focus();
          }}
        />
      )}
    </>
  );
}
