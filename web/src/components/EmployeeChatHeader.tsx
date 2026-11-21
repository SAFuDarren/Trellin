import { useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import type { ConversationSummary } from "../api/conversations";
import type { TeamAgent } from "../api/teamAgents";
import { Chevron } from "./Icons";
import { Picker } from "./FloatingControls";
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

function TaskSwitcher({
  tasks,
  current,
  onSwitch,
  trigger,
}: {
  tasks: ConversationSummary[];
  current?: ConversationSummary;
  onSwitch: (threadId: string) => void;
  trigger: RefObject<HTMLButtonElement | null>;
}) {
  const { t } = useTranslation();
  return (
    <div className="employee-chat-switcher">
      <Picker
        label={current ? t("employeeChats.switchTaskTitle", { title: current.title }) : t("employeeChats.switchTask")}
        searchLabel={t("employeeChats.searchTasks")}
        emptyLabel={t("picker.noMatches")}
        triggerClassName="employee-chat-task-trigger"
        className="employee-chat-task-picker"
        triggerRef={trigger}
        value={current?.id}
        trigger={
          <>
            <span>{current?.title ?? t("employeeChats.switchTask")}</span>
            <Chevron />
          </>
        }
        groups={[
          {
            options: tasks.map((task) => {
              const state = taskState(task);
              return {
                id: task.id,
                label: task.title,
                detail: state
                  ? t(state === "running" ? "conversations.working" : "conversations.lastSendFailed")
                  : undefined,
                current: task.id === current?.id,
                onSelect: () => {
                  if (task.id !== current?.id) onSwitch(task.id);
                },
              };
            }),
          },
        ]}
      />
    </div>
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
  switcherRef,
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
  switcherRef: RefObject<HTMLButtonElement | null>;
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
              <TaskSwitcher tasks={tasks} current={current} onSwitch={onSwitch} trigger={switcherRef} />
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
