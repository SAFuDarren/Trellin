import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
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
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const root = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const startAtEnd = useRef(false);

  useEffect(() => {
    if (!open) return;
    const list = menu.current;
    const initial = startAtEnd.current
      ? list?.querySelector<HTMLButtonElement>("button:last-child")
      : (list?.querySelector<HTMLButtonElement>('button[aria-checked="true"]') ?? list?.querySelector("button"));
    initial?.focus();
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <div
      className="employee-chat-switcher"
      ref={root}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        type="button"
        className="employee-chat-task-trigger"
        aria-label={
          current ? t("employeeChats.switchTaskTitle", { title: current.title }) : t("employeeChats.switchTask")
        }
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title={current?.title}
        ref={trigger}
        onClick={() => {
          startAtEnd.current = false;
          setOpen(!open);
        }}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          startAtEnd.current = event.key === "ArrowUp";
          setOpen(true);
        }}
      >
        <span>{current?.title ?? t("employeeChats.switchTask")}</span>
        <Chevron />
      </button>
      {open && (
        <div
          className="employee-chat-task-menu"
          id={menuId}
          ref={menu}
          role="menu"
          aria-label={t("employeeChats.switchTask")}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              close();
              return;
            }
            const items = Array.from(event.currentTarget.querySelectorAll("button"));
            const index = items.indexOf(document.activeElement as HTMLButtonElement);
            const last = items.length - 1;
            let next: number;
            switch (event.key) {
              case "ArrowDown":
                next = index === last ? 0 : index + 1;
                break;
              case "ArrowUp":
                next = index <= 0 ? last : index - 1;
                break;
              case "Home":
                next = 0;
                break;
              case "End":
                next = last;
                break;
              default:
                return;
            }
            event.preventDefault();
            items[next]?.focus();
          }}
        >
          {tasks.map((task) => {
            const state = taskState(task);
            return (
              <button
                type="button"
                role="menuitemradio"
                aria-checked={task.id === current?.id}
                tabIndex={-1}
                key={task.id}
                onClick={() => {
                  close();
                  if (task.id !== current?.id) onSwitch(task.id);
                }}
              >
                <span className="employee-chat-task-label" title={task.title}>
                  {task.title}
                </span>
                {state && (
                  <span className="chat-list-status" data-state={state}>
                    {t(state === "running" ? "conversations.working" : "conversations.lastSendFailed")}
                  </span>
                )}
                {task.id === current?.id && (
                  <svg className="employee-chat-task-check" viewBox="0 0 16 16" aria-hidden="true">
                    <path d="m3.5 8 3 3 6-6.5" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
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
