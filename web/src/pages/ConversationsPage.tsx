import { useEffect, useRef, useState, type ReactNode, type Ref } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import {
  conversationErrorText,
  conversationRecipientLabel,
  useConversationActions,
  useConversations,
  type ConversationSummary,
} from "../api/conversations";
import { useThreads, type Thread } from "../api/sections";
import { useTeamAgents, type TeamAgent } from "../api/teamAgents";
import { ConversationPanel } from "../components/ConversationPanel";
import { DIRECT_MODEL } from "../components/ConversationRecipients";
import { EmployeeChatHeader, EmployeeStatus } from "../components/EmployeeChatHeader";
import { FeedEntry } from "../components/FeedEntry";
import { Menu } from "../components/FloatingControls";
import { Chevron, Clock } from "../components/Icons";
import { NewConversation } from "../components/NewConversation";
import { Empty, PageHead } from "../components/SectionLayout";
import { TeamAgentAvatar } from "../components/TeamAgentAvatar";
import { href } from "../shell/route";
import { nodeTitle, timeText } from "../workbench/text";
import "../styles/chats-page.css";

const EMPLOYEE_ROUTE = "employee/";
const HISTORY_ROUTE = "history";
const NEW_ROUTE = "new";
const DIRECT_ROUTE = "new/model";

function proofKey(thread: Thread) {
  return thread.claim_id + "|" + thread.node_id;
}

export function ConversationsPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string, nodeId?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const proof = useThreads(session);
  const direct = useConversations(session);
  const profiles = useTeamAgents(session);
  const actions = useConversationActions(session);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<{ route: string; message: string; named: boolean }>();
  const [employeeQuery, setEmployeeQuery] = useState("");
  const lastTask = useRef(new Map<string, string>());
  const taskSwitcher = useRef<HTMLButtonElement>(null);
  const refocusTaskSwitcher = useRef(false);
  const createRequests = useRef(new Map<string, { title: string; requestId: string }>());
  const conversations = direct.data?.threads ?? [];
  const employees = [...(profiles.data?.agents ?? [])].sort((a, b) => a.name.localeCompare(b.name, i18n.language));
  const tasksByEmployee = new Map<string, ConversationSummary[]>(employees.map(({ id }) => [id, []]));
  const history: ConversationSummary[] = [];
  for (const thread of conversations) {
    const employeeTasks = thread.agent_id ? tasksByEmployee.get(thread.agent_id) : undefined;
    (employeeTasks ?? history).push(thread);
  }
  const employeeId = sub.startsWith(EMPLOYEE_ROUTE) ? sub.slice(EMPLOYEE_ROUTE.length) : undefined;
  const historyView = sub === HISTORY_ROUTE;
  const newView = sub === NEW_ROUTE || sub === DIRECT_ROUTE;
  const showProof = sub.includes("|");
  const selected = sub ? conversations.find((item) => item.id === sub) : undefined;
  const employee = employees.find((item) => item.id === (selected?.agent_id ?? employeeId));
  const tasks = employee ? (tasksByEmployee.get(employee.id) ?? []) : [];
  const current = selected ?? (!sub && !profiles.isPending && employees.length === 0 ? conversations[0] : undefined);
  const currentId = current?.id;
  const currentEmployeeId = employee?.id;
  const currentHash = window.location.hash;
  const backToHistory = employees.length > 0 && (showProof || Boolean(selected && !employee));
  const proofRows = proof.data?.threads ?? [];
  const selectedProof = showProof ? proofRows.find((thread) => proofKey(thread) === sub) : undefined;
  const back = (
    <a href={href("chat", backToHistory ? HISTORY_ROUTE : "")} className="chat-back">
      <Chevron />
      {t(backToHistory ? "employeeChats.history" : "conversations.backToList")}
    </a>
  );

  useEffect(() => {
    if (currentEmployeeId && currentId) lastTask.current.set(currentEmployeeId, currentId);
    if (refocusTaskSwitcher.current) {
      taskSwitcher.current?.focus();
      refocusTaskSwitcher.current = false;
    }
  }, [currentEmployeeId, currentId]);

  function openEmployee(profile: TeamAgent) {
    if (profile.id === currentEmployeeId && currentId) return href("chat", currentId);
    const previous = lastTask.current.get(profile.id);
    const matches = tasksByEmployee.get(profile.id) ?? [];
    const thread = matches.find((item) => item.id === previous) ?? matches[0];
    return href("chat", thread?.id ?? EMPLOYEE_ROUTE + profile.id);
  }

  async function create(agent?: TeamAgent, title = t("conversations.defaultTitle")) {
    const from = window.location.hash;
    const key = agent && `${session}/${agent.id}`;
    const pending = key ? createRequests.current.get(key) : undefined;
    const requestId = key ? (pending?.title === title ? pending.requestId : crypto.randomUUID()) : undefined;
    if (key && requestId) createRequests.current.set(key, { title, requestId });
    setCreating(true);
    setCreateError(undefined);
    try {
      const thread = await actions.create(title, {}, agent?.id, requestId);
      if (key) createRequests.current.delete(key);
      if (window.location.hash === from) window.location.hash = href("chat", thread.id);
      return true;
    } catch (error) {
      const retry = key && error instanceof ApiError && (error.code ?? error.message) === "conversation.stale";
      if (retry) createRequests.current.delete(key);
      setCreateError({
        route: sub,
        message: retry ? t("employeeChats.retryCreate") : conversationErrorText(t, error),
        named: Boolean(agent),
      });
      return false;
    } finally {
      setCreating(false);
    }
  }

  function row(thread: ConversationSummary) {
    const turn = thread.latest_turn;
    const model = turn ? conversationRecipientLabel(t, turn.provider, turn.model ?? turn.requested_model) : "";
    const status = thread.running
      ? t("conversations.working")
      : turn?.status === "failed"
        ? t("conversations.lastSendFailed")
        : turn?.status === "answered"
          ? t("conversations.replied")
          : "";
    const at = turn?.at ?? thread.updated_at;
    return (
      <li key={thread.id}>
        <a
          className="chat-list-item"
          href={href("chat", thread.id)}
          aria-current={current?.id === thread.id ? "page" : undefined}
        >
          <span className="chat-list-item-heading">
            <strong title={thread.title}>{thread.title}</strong>
            <time dateTime={at}>{timeText(i18n.language, at, true)}</time>
          </span>
          {(model || status) && (
            <span className="chat-list-info">
              {model && (
                <span className="chat-list-model" title={model}>
                  {model}
                </span>
              )}
              {status && (
                <span className="chat-list-status" data-state={turn?.status}>
                  {status}
                </span>
              )}
            </span>
          )}
          <span className="chat-list-preview" title={turn?.preview}>
            {turn?.preview || t("conversations.emptyThread")}
          </span>
        </a>
      </li>
    );
  }

  const newConversation = (
    <button
      type="button"
      className="icon-button"
      disabled={creating}
      aria-label={t("conversations.new")}
      title={t("conversations.new")}
      onClick={() => {
        window.location.hash = href("chat", NEW_ROUTE);
      }}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M3.5 8h9 M8 3.5v9" />
      </svg>
    </button>
  );
  const narrowDetail = Boolean(sub);
  const search = employeeQuery.trim().toLocaleLowerCase(i18n.language);
  const visibleEmployees = employees.filter((agent) => agent.name.toLocaleLowerCase(i18n.language).includes(search));
  const error = createError?.route === sub ? createError : undefined;
  const historyList = (
    <History
      rows={history}
      pending={direct.isPending || profiles.isPending}
      error={direct.isError ? conversationErrorText(t, direct.error) : undefined}
      renderRow={row}
      proofRows={proofRows}
      selectedProof={selectedProof}
    />
  );

  function compose(initialRecipient?: string) {
    return (
      <NewConversation
        session={session}
        agents={employees}
        agentsPending={profiles.isPending}
        agentsError={profiles.isError}
        initialRecipient={initialRecipient}
        focusComposer={newView}
        onCreated={(thread) => {
          if (window.location.hash === currentHash) window.location.hash = href("chat", thread.id);
        }}
      />
    );
  }

  function conversationActions(
    thread: ConversationSummary,
    remove: () => void,
    removeDisabled: boolean,
    triggerRef: Ref<HTMLButtonElement>,
  ) {
    const { claim_id: claimId, node_id: nodeId } = thread;
    return (
      <>
        {back}
        <Menu
          label={t("conversations.actions")}
          triggerRef={triggerRef}
          triggerClassName="icon-button chats-actions-trigger"
          className="chats-actions-menu"
          placement="bottom-end"
          trigger={
            <svg viewBox="0 0 18 18" aria-hidden="true">
              <path d="M4 9h.1 M9 9h.1 M14 9h.1" />
            </svg>
          }
          groups={[
            {
              options: claimId
                ? [
                    {
                      id: "open-workbench",
                      label: t("common.openInWorkbench"),
                      onSelect: () => onOpen(claimId, nodeId ?? undefined),
                    },
                  ]
                : [],
            },
            {
              options: [
                {
                  id: "remove-conversation",
                  label: t("conversations.remove"),
                  destructive: true,
                  disabled: removeDisabled,
                  onSelect: remove,
                },
              ],
            },
          ]}
        />
      </>
    );
  }

  function employeeHeader(profile: TeamAgent, extra: ReactNode) {
    return (
      <EmployeeChatHeader
        employee={profile}
        tasks={tasks}
        current={current}
        actions={extra}
        creating={creating}
        listUnavailable={direct.isPending || direct.isError}
        error={error?.named ? error.message : undefined}
        onShowCreate={() => setCreateError(undefined)}
        onCreate={(title) => create(profile, title)}
        switcherRef={taskSwitcher}
        onSwitch={(threadId) => {
          refocusTaskSwitcher.current = true;
          window.location.hash = href("chat", threadId);
        }}
      />
    );
  }

  return (
    <div className="chats-page" data-thread={String(narrowDetail)}>
      <nav className="chats-sidebar" aria-labelledby="section-title">
        <header className="chat-list-head">
          <h1 id="section-title">{t("rail.chat")}</h1>
          {employees.length > 0 ? (
            <a
              className="icon-button employee-chat-history-link"
              href={href("chat", HISTORY_ROUTE)}
              aria-current={historyView ? "page" : undefined}
              aria-label={t("employeeChats.history")}
              title={t("employeeChats.history")}
            >
              <Clock />
            </a>
          ) : (
            !profiles.isPending && newConversation
          )}
        </header>
        <div className="chat-list-scroll">
          {error && !error.named && (
            <p className="chat-list-note" role="alert">
              {error.message}
            </p>
          )}
          {employees.length ? (
            <>
              <label className="search-field employee-chat-search">
                <input
                  type="search"
                  value={employeeQuery}
                  placeholder={t("employeeChats.search")}
                  aria-label={t("employeeChats.search")}
                  onChange={(event) => setEmployeeQuery(event.currentTarget.value)}
                />
              </label>
              {visibleEmployees.length ? (
                <ul aria-label={t("employeeChats.employees")}>
                  {visibleEmployees.map((profile) => (
                    <li key={profile.id}>
                      <a
                        className="chat-list-item employee-chat-person"
                        href={openEmployee(profile)}
                        aria-current={profile.id === employee?.id ? "page" : undefined}
                      >
                        <TeamAgentAvatar avatar={profile.avatar} name={profile.name} />
                        <span className="employee-chat-person-label">
                          <strong title={profile.name}>{profile.name}</strong>
                          {direct.isSuccess && <EmployeeStatus tasks={tasksByEmployee.get(profile.id) ?? []} />}
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="chat-list-note">{t("employeeChats.noMatches")}</p>
              )}
            </>
          ) : (
            historyList
          )}
        </div>
      </nav>
      <main className="chats-main" tabIndex={-1}>
        {historyView ? (
          <>
            <PageHead title={t("employeeChats.history")} actions={back} />
            <div className="chat-history-content">{historyList}</div>
          </>
        ) : showProof && selectedProof ? (
          <ProofHistory key={proofKey(selectedProof)} thread={selectedProof} onOpen={onOpen} back={back} />
        ) : !showProof && current ? (
          <ConversationPanel
            key={session + "/" + current.id}
            session={session}
            thread={current}
            employee={employee}
            recipientChoice={{
              agents: employees,
              pending: profiles.isPending,
              onSelect: (id) => {
                if (id === (current.agent_id ?? DIRECT_MODEL)) return;
                const recipient = employees.find((profile) => profile.id === id);
                if (recipient) window.location.hash = openEmployee(recipient);
                else if (id === DIRECT_MODEL) window.location.hash = href("chat", DIRECT_ROUTE);
              },
            }}
            onRemove={() => {
              window.location.hash = employee
                ? href("chat", tasks.find((task) => task.id !== current.id)?.id ?? EMPLOYEE_ROUTE + employee.id)
                : href("chat", employees.length ? HISTORY_ROUTE : "");
            }}
            header={({ remove, removeDisabled, triggerRef }) =>
              employee ? (
                employeeHeader(employee, conversationActions(current, remove, removeDisabled, triggerRef))
              ) : (
                <PageHead
                  title={current.title}
                  note={t(
                    current.node_id
                      ? "conversations.nodeScope"
                      : current.claim_id
                        ? "conversations.claimScope"
                        : "conversations.projectScope",
                  )}
                  actions={conversationActions(current, remove, removeDisabled, triggerRef)}
                />
              )
            }
          />
        ) : employee ? (
          <>
            {employeeHeader(employee, back)}
            {direct.isError && <Empty>{conversationErrorText(t, direct.error)}</Empty>}
            {compose(employee.id)}
          </>
        ) : !sub || newView ? (
          <>
            <PageHead
              title={t(newView ? "agentDock.newConversation" : "rail.chat")}
              actions={newView ? back : undefined}
            />
            {direct.isError && <Empty>{conversationErrorText(t, direct.error)}</Empty>}
            {compose(sub === DIRECT_ROUTE ? DIRECT_MODEL : undefined)}
          </>
        ) : (
          <>
            <PageHead title={t("rail.chat")} actions={back} />
            <Empty>
              {direct.isError && !showProof
                ? conversationErrorText(t, direct.error)
                : proof.isError && showProof
                  ? conversationErrorText(t, proof.error)
                  : direct.isPending || (showProof && proof.isPending) || ((!sub || employeeId) && profiles.isPending)
                    ? t("common.loading")
                    : employeeId && profiles.isError
                      ? t("teamAgents.listFailed")
                      : sub
                        ? t("conversations.conversationMissing")
                        : t(employees.length ? "employeeChats.chooseEmployee" : "conversations.emptyProject")}
            </Empty>
          </>
        )}
      </main>
    </div>
  );
}

function History({
  rows,
  pending,
  error,
  renderRow,
  proofRows,
  selectedProof,
}: {
  rows: ConversationSummary[];
  pending: boolean;
  error?: string;
  renderRow: (thread: ConversationSummary) => ReactNode;
  proofRows: Thread[];
  selectedProof?: Thread;
}) {
  const { t, i18n } = useTranslation();
  return (
    <>
      {pending ? (
        <p className="chat-list-note" role="status">
          {t("common.loading")}
        </p>
      ) : error ? (
        <p className="chat-list-note" role="alert">
          {error}
        </p>
      ) : rows.length ? (
        <ul aria-label={t("conversations.threads")}>{rows.map(renderRow)}</ul>
      ) : (
        <p className="chat-list-note">{t("conversations.noConversations")}</p>
      )}
      {proofRows.length > 0 && (
        <section className="chat-proof-list" aria-label={t("conversations.proofActivity")}>
          <h2>{t("conversations.proofActivity")}</h2>
          <ul>
            {proofRows.map((thread) => (
              <li key={proofKey(thread)}>
                <a
                  className="chat-list-item"
                  href={href("chat", proofKey(thread))}
                  aria-current={thread === selectedProof ? "page" : undefined}
                >
                  <span className="chat-list-item-heading">
                    <strong>{nodeTitle(t, thread.node)}</strong>
                    {thread.last_at && (
                      <time dateTime={thread.last_at}>{timeText(i18n.language, thread.last_at, true)}</time>
                    )}
                  </span>
                  {thread.claim_title && <span className="chat-list-preview">{thread.claim_title}</span>}
                  {thread.live > 0 && (
                    <span className="chat-list-status" data-state="running">
                      {t("threads.running", { count: thread.live })}
                    </span>
                  )}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function ProofHistory({
  thread,
  onOpen,
  back,
}: {
  thread: Thread;
  onOpen: (claimId: string, nodeId?: string) => void;
  back: ReactNode;
}) {
  const { t } = useTranslation();
  const [model, setModel] = useState("all");
  const events = thread.events.filter(
    (event) => model === "all" || (model === "you" ? event.kind === "guidance" : event.model === model),
  );
  return (
    <div className="chats-proof-history">
      <PageHead
        title={nodeTitle(t, thread.node)}
        note={thread.claim_title ?? t("conversations.proofActivity")}
        actions={
          <>
            {back}
            <button
              type="button"
              className="conversation-text-action"
              onClick={() => onOpen(thread.claim_id, thread.node_id)}
            >
              {t("common.openInWorkbench")}
            </button>
          </>
        }
      />
      <div className="thread-filters" role="group" aria-label={t("threads.filter")}>
        {["all", ...thread.models, "you"].map((value) => (
          <button
            key={value}
            type="button"
            className="chip"
            aria-pressed={model === value}
            onClick={() => setModel(value)}
          >
            {value === "all" ? t("threads.allModels") : value === "you" ? t("threads.yourNotes") : value}
          </button>
        ))}
      </div>
      <ol className="chat-feed thread-feed" tabIndex={0} aria-label={t("chat.feed")}>
        {events.length ? (
          events.map((event) => <FeedEntry key={event.id} record={event} />)
        ) : (
          <li className="chat-empty">{t("threads.empty")}</li>
        )}
      </ol>
    </div>
  );
}
