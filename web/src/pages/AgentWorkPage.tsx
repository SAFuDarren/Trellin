import { useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  AGENT_WORK_MODES,
  useAgentWork,
  useAgentWorkActions,
  useAgentWorkTask,
  type AgentWorkDecision,
  type AgentWorkMode,
  type AgentWorkPreview,
  type AgentWorkRequest,
  type AgentWorkRetryPreview,
  type AgentWorkSummary,
  type AgentWorkTask,
} from "../api/agentWork";
import { ApiError } from "../api/client";
import { useTeamAgents } from "../api/teamAgents";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { useDesktopProjectList } from "../components/SidebarProjects";
import { href } from "../shell/route";
import { harnessName, timeText } from "../workbench/text";
import "../styles/project-entry.css";
import "../styles/agent-work.css";

const ROOT = "agent-work";
const TASK_ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const SHA = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const agentWorkHref = (path = "") => href("claims", path ? `${ROOT}/${path}` : ROOT);
const eligibleInput = (task: AgentWorkSummary) =>
  task.delivery_sha256 && ["delivered", "accepted"].includes(task.status);
function workError(t: TFunction, error: unknown, fallback: string) {
  const code = error instanceof ApiError ? (error.code ?? error.message) : typeof error === "string" ? error : "";
  const key = code.startsWith("connection.") ? `errors.${code}` : `agentWork.errors.${code.split(".").at(-1)}`;
  return t(key, { defaultValue: t(`agentWork.errors.${fallback}`) });
}

export function AgentWorkPage({ session, sub }: { session: string; sub: string }) {
  const { t } = useTranslation();
  const work = useAgentWork(session);
  const tasks = work.data?.tasks ?? [];
  const [, path, target, id] = sub.split("/");
  const waiting = tasks.filter((task) => task.status === "delivered");
  const filtered = path === "waiting";
  const creating = path === "new";
  const taskId = TASK_ID.test(path ?? "") ? path : null;
  const originId = creating && TASK_ID.test(id ?? "") ? id : undefined;
  const candidates = tasks.filter(eligibleInput);
  const predecessor = target === "delivery" ? candidates.find((task) => task.id === originId) : undefined;
  const visible = filtered ? waiting : tasks;

  return (
    <SectionLayout
      title={t("agentWork.title")}
      groups={[
        {
          entries: [
            { key: "all", label: t("agentWork.all"), href: agentWorkHref(), current: !path, count: tasks.length },
            {
              key: "waiting",
              label: t("agentWork.waiting"),
              href: agentWorkHref("waiting"),
              current: filtered,
              count: waiting.length,
            },
            { key: "claims", label: t("rail.claims"), href: href("claims") },
          ],
        },
      ]}
    >
      <div className="agent-work-view">
        <PageHead
          title={t(creating ? "agentWork.newTask" : filtered ? "agentWork.waiting" : "agentWork.title")}
          actions={
            work.data?.available &&
            !creating && (
              <a className="button primary" href={agentWorkHref("new")}>
                {t("agentWork.newTask")}
              </a>
            )
          }
        />
        {work.isPending ? (
          <Empty>{t("common.loading")}</Empty>
        ) : work.isError && !work.data ? (
          <div className="page-body">
            <p className="project-entry-status" role="alert">
              {t("agentWork.errors.load")}
            </p>
            <div className="project-entry-buttons">
              <button type="button" className="button" onClick={() => void work.refetch()}>
                {t("agentWork.retry")}
              </button>
            </div>
          </div>
        ) : !work.data?.available ? (
          <Empty>{t("agentWork.unavailable")}</Empty>
        ) : creating && target === "delivery" && !predecessor ? (
          <Empty>{t("agentWork.errors.predecessor")}</Empty>
        ) : creating ? (
          <NewAgentWork
            key={`${session}/${sub}`}
            session={session}
            agentId={target === "agent" ? originId : undefined}
            predecessor={predecessor}
            candidates={candidates}
          />
        ) : taskId ? (
          <AgentWorkDetail key={`${session}/${taskId}`} session={session} id={taskId} />
        ) : visible.length > 0 ? (
          <AgentWorkList tasks={visible} />
        ) : (
          <Empty>{t(filtered ? "agentWork.emptyWaiting" : "agentWork.empty")}</Empty>
        )}
      </div>
    </SectionLayout>
  );
}

function AgentWorkList({ tasks }: { tasks: AgentWorkSummary[] }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="page-body">
      <ul className="agent-work-list">
        {tasks.map((task) => (
          <li key={task.id}>
            <a href={agentWorkHref(task.id)}>
              <strong>{task.title}</strong>
              <span>{t(`agentWork.status.${task.status}`)}</span>
              <span className="agent-work-secondary">
                {task.agent_name} · {t(`agentWork.mode.${task.mode}`)}
              </span>
              <time className="agent-work-secondary" dateTime={task.updated_at}>
                {timeText(i18n.language, task.updated_at, true)}
              </time>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

type Draft = {
  agent_id: string;
  mode: AgentWorkMode;
  title: string;
  instructions: string;
  criteria: string;
  checkout: string;
  revision: string;
  files: string;
  predecessor_id: string;
};

function NewAgentWork({
  session,
  agentId,
  predecessor,
  candidates,
}: {
  session: string;
  agentId?: string;
  predecessor?: AgentWorkSummary;
  candidates: AgentWorkSummary[];
}) {
  const { t } = useTranslation();
  const actions = useAgentWorkActions(session);
  const agents = useTeamAgents(session);
  const [taskId] = useState(() => crypto.randomUUID());
  const [draft, setDraft] = useState<Draft>({
    agent_id: agentId ?? "",
    mode: predecessor ? "review" : "implement",
    title: predecessor ? t("agentWork.reviewTitle", { title: predecessor.title }) : "",
    instructions: "",
    criteria: "",
    checkout: "",
    revision: "",
    files: "",
    predecessor_id: predecessor?.id ?? "",
  });
  const [prepared, setPrepared] = useState<{ request: AgentWorkRequest; approval: AgentWorkPreview } | null>(null);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const busy = useRef(false);
  const requiresSource = draft.mode === "implement";

  function update(values: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...values }));
    setNotice("");
  }

  async function preview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current || !draft.agent_id) return;
    const files = draft.files
      .split("\n")
      .map((path) => path.trim())
      .filter(Boolean);
    const request: AgentWorkRequest = {
      task_id: taskId,
      agent_id: draft.agent_id,
      mode: draft.mode,
      title: draft.title.trim(),
      instructions: draft.instructions.trim(),
      criteria: draft.criteria.trim(),
      ...(requiresSource ? { checkout: draft.checkout.trim(), revision: draft.revision.trim(), files } : {}),
      ...(draft.mode === "review" ? { predecessor_id: draft.predecessor_id } : {}),
    };
    if (
      !request.title ||
      !request.instructions ||
      !request.criteria ||
      (draft.mode === "review" && !request.predecessor_id) ||
      (requiresSource &&
        (!request.checkout || !SHA.test(request.revision ?? "") || files.length === 0 || files.length > 12))
    ) {
      setNotice(t("agentWork.errors.invalid"));
      return;
    }
    busy.current = true;
    setPending(true);
    setNotice("");
    try {
      const approval = await actions.preview(request);
      setPrepared({ request, approval });
    } catch (error) {
      setNotice(workError(t, error, "preview"));
    } finally {
      busy.current = false;
      setPending(false);
    }
  }

  async function start() {
    if (!prepared || busy.current) return;
    busy.current = true;
    setPending(true);
    setNotice("");
    try {
      const task = await actions.start(prepared.request, prepared.approval);
      window.location.hash = agentWorkHref(task.id);
    } catch (error) {
      setNotice(workError(t, error, "start"));
    } finally {
      busy.current = false;
      setPending(false);
    }
  }

  function edit() {
    setPrepared(null);
    setNotice("");
  }

  if (agents.isPending) return <Empty>{t("common.loading")}</Empty>;
  if (agents.isError) {
    return (
      <div className="page-body agent-work-body">
        <p className="project-entry-status" role="alert">
          {t("teamAgents.listFailed")}
        </p>
        <div className="project-entry-buttons">
          <button type="button" className="button" onClick={() => void agents.refetch()}>
            {t("agentWork.retry")}
          </button>
        </div>
      </div>
    );
  }
  if (!agents.data.agents.length) {
    return (
      <div className="page-body agent-work-body">
        <p>{t("agentWork.noAgents")}</p>
        <div className="project-entry-buttons">
          <a className="button" href={href("team", "agents")}>
            {t("agentWork.openTeam")}
          </a>
        </div>
      </div>
    );
  }
  return (
    <div className="page-body agent-work-body">
      {notice && (
        <p className="project-entry-status" role="alert">
          {notice}
        </p>
      )}
      {prepared ? (
        <AgentWorkApproval
          approval={prepared.approval}
          checkout={prepared.request.checkout}
          pending={pending}
          onBack={edit}
          onStart={start}
        />
      ) : (
        <form className="project-entry-form" onSubmit={(event) => void preview(event)}>
          {predecessor && (
            <p className="page-note">
              {t("agentWork.reviewing")}{" "}
              <a className="text-link" href={agentWorkHref(predecessor.id)}>
                {predecessor.title}
              </a>
            </p>
          )}
          <div className="project-entry-columns">
            <label>
              {t("agentWork.agent")}
              <select value={draft.agent_id} onChange={(event) => update({ agent_id: event.target.value })} required>
                <option value="">{t("agentWork.chooseAgent")}</option>
                {agents.data.agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </label>
            {!predecessor && (
              <label>
                {t("agentWork.taskType")}
                <select value={draft.mode} onChange={(event) => update({ mode: event.target.value as AgentWorkMode })}>
                  {AGENT_WORK_MODES.map((mode) => (
                    <option key={mode} value={mode}>
                      {t(`agentWork.mode.${mode}`)}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {draft.mode === "review" && !predecessor && (
            <label>
              {t("agentWork.inputDelivery")}
              <select
                value={draft.predecessor_id}
                onChange={(event) => update({ predecessor_id: event.target.value })}
                required
              >
                <option value="">{t(candidates.length ? "agentWork.chooseDelivery" : "agentWork.noDeliveries")}</option>
                {candidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.title}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            {t("agentWork.taskTitle")}
            <input
              required
              maxLength={160}
              value={draft.title}
              onChange={(event) => update({ title: event.target.value })}
            />
          </label>
          <label>
            {t("agentWork.instructions")}
            <textarea
              rows={4}
              required
              maxLength={6000}
              value={draft.instructions}
              onChange={(event) => update({ instructions: event.target.value })}
            />
          </label>
          <label>
            {t("agentWork.criteria")}
            <textarea
              rows={2}
              required
              maxLength={4000}
              value={draft.criteria}
              onChange={(event) => update({ criteria: event.target.value })}
            />
          </label>
          {requiresSource && (
            <>
              <div className="project-entry-columns">
                <label>
                  {t("agentWork.checkout")}
                  <input
                    required
                    maxLength={400}
                    placeholder="inputs/example"
                    value={draft.checkout}
                    onChange={(event) => update({ checkout: event.target.value })}
                  />
                </label>
                <label>
                  {t("agentWork.baseRevision")}
                  <input
                    required
                    maxLength={64}
                    spellCheck={false}
                    value={draft.revision}
                    onChange={(event) => update({ revision: event.target.value })}
                  />
                </label>
              </div>
              <label>
                {t("agentWork.files")}
                <textarea
                  rows={3}
                  required
                  spellCheck={false}
                  value={draft.files}
                  onChange={(event) => update({ files: event.target.value })}
                />
              </label>
              <p className="page-note">{t("agentWork.sourceHint")}</p>
            </>
          )}
          <div className="project-entry-buttons">
            <button type="submit" className="button primary" disabled={pending}>
              {t(pending ? "common.loading" : "agentWork.preview")}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function AgentWorkApproval({
  approval,
  checkout,
  generation,
  pending,
  onBack,
  onStart,
}: {
  approval: AgentWorkPreview;
  checkout?: string | null;
  generation?: number;
  pending: boolean;
  onBack: () => void;
  onStart: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [approved, setApproved] = useState(false);
  const projects = useDesktopProjectList();
  const projectName = projects.data?.projects.find((project) => project.id === projects.data?.activeId)?.name;
  return (
    <form
      className="project-entry-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (approved && !pending) void onStart();
      }}
    >
      <h3>{t(generation ? "agentWork.retryPreview" : "agentWork.preview")}</h3>
      <dl className="facts">
        <dt>{t("agentWork.project")}</dt>
        <dd title={approval.workspace_id}>{projectName ?? approval.workspace_id}</dd>
        <dt>{t("agentWork.agent")}</dt>
        <dd>{approval.agent_name}</dd>
        <dt>{t("agentWork.runtime")}</dt>
        <dd>
          {harnessName(approval.provider)} · {approval.model}
        </dd>
        {generation && (
          <>
            <dt>{t("agentWork.attempt")}</dt>
            <dd>{generation}</dd>
          </>
        )}
        {checkout && (
          <>
            <dt>{t("agentWork.checkout")}</dt>
            <dd className="mono">{checkout}</dd>
          </>
        )}
        {approval.base_revision && (
          <>
            <dt>{t("agentWork.baseRevision")}</dt>
            <dd className="mono">{approval.base_revision}</dd>
          </>
        )}
        {approval.predecessor_sha256 && (
          <>
            <dt>{t("agentWork.inputDigest")}</dt>
            <dd className="mono">{approval.predecessor_sha256}</dd>
          </>
        )}
        <dt>{t("agentWork.tools")}</dt>
        <dd>
          {approval.tools.length
            ? approval.tools.map((tool) => t(`agentWork.tool.${tool}`, { defaultValue: tool })).join(", ")
            : t("agentWork.noTools")}
        </dd>
        {approval.limits && (
          <>
            <dt>{t("agentWork.timeLimit")}</dt>
            <dd>{t("agentWork.seconds", { count: approval.limits.wall_seconds })}</dd>
            {approval.limits.output_token_limit_enforced && (
              <>
                <dt>{t("agentWork.outputTokens")}</dt>
                <dd>{approval.limits.max_output_tokens.toLocaleString()}</dd>
              </>
            )}
          </>
        )}
      </dl>
      <h4>{t("agentWork.outgoing")}</h4>
      <pre tabIndex={0} aria-label={t("agentWork.outgoing")} className="code-block agent-work-payload">
        {approval.outgoing}
      </pre>
      <label className="project-entry-check">
        <input
          type="checkbox"
          required
          checked={approved}
          disabled={pending}
          onChange={(event) => setApproved(event.target.checked)}
        />
        {t(approval.external ? "agentWork.authorizeExternal" : "agentWork.authorizeLocal")}
      </label>
      <div className="project-entry-buttons">
        <button type="button" className="button" disabled={pending} onClick={onBack}>
          {t(generation ? "common.cancel" : "agentWork.back")}
        </button>
        <button type="submit" className="button primary" disabled={pending || !approved}>
          {t(pending ? "common.loading" : generation ? "agentWork.startRetry" : "agentWork.start")}
        </button>
      </div>
    </form>
  );
}

function AgentWorkDetail({ session, id }: { session: string; id: string }) {
  const { t, i18n } = useTranslation();
  const result = useAgentWorkTask(session, id);
  if (result.isPending) return <Empty>{t("common.loading")}</Empty>;
  const task = result.data;
  if (!task) return <Empty>{t("agentWork.errors.loadTask")}</Empty>;
  const canHandoff = task.delivery && task.mode !== "review" && eligibleInput(task);
  return (
    <div className="page-body agent-work-body">
      <header className="agent-work-heading">
        <h3>{task.title}</h3>
        <p role="status">{t(`agentWork.status.${task.status}`)}</p>
      </header>
      <dl className="facts">
        <dt>{t("agentWork.agent")}</dt>
        <dd>{task.agent_name}</dd>
        <dt>{t("agentWork.taskType")}</dt>
        <dd>{t(`agentWork.mode.${task.mode}`)}</dd>
        {task.base_revision && (
          <>
            <dt>{t("agentWork.baseRevision")}</dt>
            <dd className="mono">{task.base_revision}</dd>
          </>
        )}
        {task.checkout && (
          <>
            <dt>{t("agentWork.checkout")}</dt>
            <dd className="mono">{task.checkout}</dd>
          </>
        )}
        {task.predecessor_id && (
          <>
            <dt>{t("agentWork.inputDelivery")}</dt>
            <dd>
              <a href={agentWorkHref(task.predecessor_id)} className="text-link">
                {t("agentWork.openInput")}
              </a>
            </dd>
            {task.predecessor_sha256 && (
              <>
                <dt>{t("agentWork.inputDigest")}</dt>
                <dd className="mono">{task.predecessor_sha256}</dd>
              </>
            )}
          </>
        )}
        <dt>{t("agentWork.updated")}</dt>
        <dd>{timeText(i18n.language, task.updated_at, true)}</dd>
      </dl>
      {task.error && (
        <p className="project-entry-status" role="alert">
          {workError(t, task.error, "run")}
        </p>
      )}
      {(task.status === "failed" || task.status === "interrupted") && <AgentWorkRetry session={session} task={task} />}
      {task.instructions && (
        <section className="agent-work-section">
          <h4>{t("agentWork.instructions")}</h4>
          <p>{task.instructions}</p>
        </section>
      )}
      {task.criteria && (
        <section className="agent-work-section">
          <h4>{t("agentWork.criteria")}</h4>
          <p>{task.criteria}</p>
        </section>
      )}
      {task.delivery && (
        <section className="agent-work-section">
          <h4>{t("agentWork.delivery")}</h4>
          {task.delivery.summary && <p>{task.delivery.summary}</p>}
          {task.delivery.body && (
            <pre tabIndex={0} aria-label={t("agentWork.delivery")} className="code-block agent-work-payload">
              {task.delivery.body}
            </pre>
          )}
          {task.delivery.checks.length > 0 && (
            <>
              <h4>{t("agentWork.checks")}</h4>
              <ul className="agent-work-checks">
                {task.delivery.checks.map((check, index) => (
                  <li key={index}>{t(`agentWork.check.${check}`, { defaultValue: check })}</li>
                ))}
              </ul>
            </>
          )}
          {task.delivery_sha256 && (
            <dl className="facts">
              <dt>{t("agentWork.deliveryDigest")}</dt>
              <dd className="mono">{task.delivery_sha256}</dd>
            </dl>
          )}
          {canHandoff && (
            <div className="project-entry-buttons">
              <a className="button" href={agentWorkHref("new/delivery/" + task.id)}>
                {t("agentWork.assignReview")}
              </a>
            </div>
          )}
        </section>
      )}
      {task.review && (
        <section className="agent-work-section">
          <h4>{t("agentWork.humanDecision")}</h4>
          <dl className="facts">
            <dt>{t(`agentWork.decision.${task.review.decision}`)}</dt>
            <dd>{timeText(i18n.language, task.review.reviewed_at, true)}</dd>
            <dt>{t("agentWork.reason")}</dt>
            <dd>{task.review.reason}</dd>
          </dl>
        </section>
      )}
      {task.status === "delivered" && task.delivery && task.delivery_sha256 && (
        <AgentWorkReview key={task.delivery_sha256} session={session} task={task} digest={task.delivery_sha256} />
      )}
    </div>
  );
}

function AgentWorkRetry({ session, task }: { session: string; task: AgentWorkTask }) {
  const { t } = useTranslation();
  const actions = useAgentWorkActions(session);
  const [request] = useState(() => ({ task_id: task.id, action_id: crypto.randomUUID() }));
  const [approval, setApproval] = useState<AgentWorkRetryPreview | null>(null);
  const [failure, setFailure] = useState("");
  const [pending, setPending] = useState(false);
  const busy = useRef(false);

  async function advance() {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setFailure("");
    try {
      if (approval) await actions.retry(request, approval);
      else setApproval(await actions.retryPreview(request));
    } catch (error) {
      setFailure(workError(t, error, approval ? "start" : "preview"));
    } finally {
      busy.current = false;
      setPending(false);
    }
  }

  return (
    <section className="agent-work-section">
      {failure && (
        <p className="project-entry-status" role="alert">
          {failure}
        </p>
      )}
      {approval ? (
        <AgentWorkApproval
          approval={approval}
          checkout={task.checkout}
          generation={approval.generation}
          pending={pending}
          onBack={() => {
            setApproval(null);
            setFailure("");
          }}
          onStart={advance}
        />
      ) : (
        <div className="project-entry-buttons">
          <button type="button" className="button" disabled={pending} onClick={() => void advance()}>
            {t(pending ? "common.loading" : "agentWork.retryTask")}
          </button>
        </div>
      )}
    </section>
  );
}

function AgentWorkReview({ session, task, digest }: { session: string; task: AgentWorkTask; digest: string }) {
  const { t } = useTranslation();
  const actions = useAgentWorkActions(session);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const previous = useRef<{ decision: AgentWorkDecision; reason: string; id: string }>(null);

  async function decide(decision: AgentWorkDecision) {
    const clean = reason.trim();
    if (busy.current || !clean) return;
    if (previous.current?.decision !== decision || previous.current.reason !== clean)
      previous.current = { decision, reason: clean, id: crypto.randomUUID() };
    busy.current = true;
    setPending(true);
    setError("");
    try {
      await actions.review({
        task_id: task.id,
        action_id: previous.current.id,
        delivery_sha256: digest,
        decision,
        reason: clean,
      });
    } catch (cause) {
      setError(workError(t, cause, "review"));
    } finally {
      busy.current = false;
      setPending(false);
    }
  }
  return (
    <section className="project-entry-form decision" aria-label={t("agentWork.humanDecision")}>
      <label>
        {t("agentWork.reason")}
        <textarea
          rows={2}
          maxLength={2000}
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
            setError("");
          }}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <div className="project-entry-buttons">
        <button
          type="button"
          className="button primary"
          disabled={pending || !reason.trim()}
          onClick={() => void decide("accept")}
        >
          {t("agentWork.accept")}
        </button>
        <button
          type="button"
          className="button"
          disabled={pending || !reason.trim()}
          onClick={() => void decide("return")}
        >
          {t("agentWork.return")}
        </button>
      </div>
    </section>
  );
}
