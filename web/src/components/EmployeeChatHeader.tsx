import { useId, useRef, useState, type ReactNode, type Ref } from "react";
import { useTranslation } from "react-i18next";
import type { ConversationSummary } from "../api/conversations";
import type { TeamAgent } from "../api/teamAgents";
import { Chevron } from "./Icons";
import { TeamAgentAvatar } from "./TeamAgentAvatar";
import "../styles/project-entry.css";

function taskState(thread: ConversationSummary) {
  if (thread.running) return "running";
  if (thread.latest_turn?.status === "failed") return "failed";
  return undefined;
}

export function EmployeeStatus({ tasks, announce }: { tasks: ConversationSummary[]; announce?: boolean }) {
  const { t } = useTranslation();
  const running = tasks.filter((task) => taskState(task) === "running").length;
  const failed = tasks.filter((task) => taskState(task) === "failed").length;
  const status = running ? t("employeeChats.running", { count: running }) : t("employeeChats.idle");
  return (
    <span className="employee-chat-status" role={announce ? "status" : undefined}>
      {failed
        ? t("employeeChats.statusWithFailures", { status, failed: t("employeeChats.failed", { count: failed }) })
        : status}
    </span>
  );
}

export function EmployeeChatHeader({
  employee,
  tasks,
  current,
  actions,
  creating,
  listUnavailable,
  error,
  onShowCreate,
  onCreate,
  onSwitch,
  selectRef,
}: {
  employee: TeamAgent;
  tasks: ConversationSummary[];
  current?: ConversationSummary;
  actions: ReactNode;
  creating: boolean;
  listUnavailable: boolean;
  error?: string;
  onShowCreate: () => void;
  onCreate: (title: string) => Promise<boolean>;
  onSwitch: (threadId: string) => void;
  selectRef: Ref<HTMLSelectElement>;
}) {
  const { t } = useTranslation();
  const [title, setTitle] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const submitting = useRef(false);
  const dialogId = useId();
  return (
    <header className="page-head employee-chat-head">
      <div className="employee-chat-heading">
        <div className="employee-chat-path">
          <TeamAgentAvatar avatar={employee.avatar} name={employee.name} />
          <h2 title={employee.name}>{employee.name}</h2>
          {tasks.length > 0 && (
            <>
              <Chevron />
              <select
                className="employee-chat-task-select"
                aria-label={t("employeeChats.switchTask")}
                title={current?.title}
                value={current?.id ?? ""}
                ref={selectRef}
                onChange={(event) => onSwitch(event.currentTarget.value)}
              >
                {!current && (
                  <option value="" disabled>
                    {t("employeeChats.switchTask")}
                  </option>
                )}
                {tasks.map((task) => {
                  const state = taskState(task);
                  return (
                    <option value={task.id} key={task.id}>
                      {state
                        ? t("employeeChats.taskWithStatus", {
                            title: task.title,
                            status: t(state === "running" ? "conversations.working" : "conversations.lastSendFailed"),
                          })
                        : task.title}
                    </option>
                  );
                })}
              </select>
            </>
          )}
        </div>
        {!listUnavailable && <EmployeeStatus tasks={tasks} announce />}
      </div>
      <div className="page-actions employee-chat-actions">
        <button
          type="button"
          className="button"
          disabled={creating || listUnavailable}
          onClick={() => {
            onShowCreate();
            setTitle("");
            dialog.current?.showModal();
          }}
        >
          {t("employeeChats.newTask")}
        </button>
        {actions}
      </div>
      <dialog
        className="project-entry employee-chat-dialog"
        ref={dialog}
        aria-labelledby={dialogId}
        onCancel={(event) => {
          if (submitting.current) event.preventDefault();
        }}
      >
        <header className="project-entry-head">
          <h2 id={dialogId}>{t("employeeChats.newTask")}</h2>
          <span className="project-entry-note">{employee.name}</span>
        </header>
        <form
          className="project-entry-body project-entry-form"
          onSubmit={(event) => {
            event.preventDefault();
            const taskName = title.trim();
            if (!taskName || submitting.current || creating) return;
            submitting.current = true;
            void onCreate(taskName).then((success) => {
              submitting.current = false;
              if (success) dialog.current?.close();
            });
          }}
        >
          <label>
            {t("employeeChats.taskName")}
            <input
              required
              autoFocus
              maxLength={200}
              value={title}
              onChange={(event) => setTitle(event.currentTarget.value)}
            />
          </label>
          {error && (
            <p className="project-entry-status" role="alert">
              {error}
            </p>
          )}
          <div className="project-entry-buttons">
            <button type="button" className="button" disabled={creating} onClick={() => dialog.current?.close()}>
              {t("common.cancel")}
            </button>
            <button type="submit" className="button primary" disabled={!title.trim() || creating}>
              {t(creating ? "employeeChats.creatingTask" : "employeeChats.createTask")}
            </button>
          </div>
        </form>
      </dialog>
    </header>
  );
}
