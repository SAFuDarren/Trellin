import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TeamAgent } from "../api/teamAgents";
import { reviewItemHref, reviewItemKindLabel } from "../shell/reviewItems";
import { href } from "../shell/route";
import {
  ACTIVITY_STATES,
  type ActivityRow,
  type ActivityState,
  type WorkspaceActivity,
} from "../shell/workspaceActivity";
import { harnessName, prettyModel, timeText } from "../workbench/text";
import { Mark } from "./Icons";

const FILTERS = ["all", ...ACTIVITY_STATES] as const;
const TONE = { running: "ok", queued: "planned", review: "review", attention: "bad", completed: "" } as const;

function relativeTime(language: string, at: string, now: number) {
  const seconds = Math.round((Date.parse(at) - now) / 1000);
  const format = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  if (Math.abs(seconds) < 60) return format.format(seconds, "second");
  if (Math.abs(seconds) < 3600) return format.format(Math.round(seconds / 60), "minute");
  if (Math.abs(seconds) < 86400) return format.format(Math.round(seconds / 3600), "hour");
  return format.format(Math.round(seconds / 86400), "day");
}

export function WorkspaceActivityList({
  activity,
  agents,
  loading,
  unavailable,
  onNavigate,
  onOpenProof,
  onOpenThread,
}: {
  activity: WorkspaceActivity;
  agents: ReadonlyMap<string, TeamAgent>;
  loading: boolean;
  unavailable: boolean;
  onNavigate: () => void;
  onOpenProof: (claimId: string, nodeId?: string) => void;
  onOpenThread: (thread: Extract<ActivityRow, { source: "chat" }>["thread"]) => void;
}) {
  const { t, i18n } = useTranslation();
  const [selectedFilter, setFilter] = useState<ActivityState | "all">("all");
  const hasActivity = activity.rows.length > 0;
  const filter = hasActivity ? selectedFilter : "all";
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(tick);
  }, []);
  const rows = filter === "all" ? activity.rows : activity.rows.filter((entry) => entry.state === filter);

  function providerLabel(provider?: string | null, model?: string | null) {
    return [
      provider && t(`team.providers.${provider}`, { defaultValue: harnessName(provider) || provider }),
      model && prettyModel(model),
    ]
      .filter(Boolean)
      .join(" · ");
  }

  function rowContent(row: ActivityRow) {
    switch (row.source) {
      case "job": {
        const { job } = row;
        const title = t(job.kind === "verify" ? "activityKind.lean" : "workspaceActivity.proofJob");
        const actor = providerLabel(job.provider, job.model) || (job.kind === "verify" ? t("team.verifier") : "");
        const node = row.nodeId?.match(/^obligation:[^:]+:(\d+)$/)?.[1];
        return {
          title: job.claim_title || title,
          status: t(`activityState.${job.state}`, { defaultValue: job.state }),
          details: [job.claim_title && title, node && t("workspaceActivity.obligation", { index: node }), actor],
          url: href("team", "jobs"),
        };
      }
      case "work":
        return {
          title: row.task.title,
          status: t(`agentWork.status.${row.task.status}`),
          details: [agents.get(row.task.agent_id ?? "")?.name || row.task.agent_name, t("agentWork.title")],
          url: href("claims", "agent-work/" + row.task.id),
        };
      case "chat": {
        const { thread } = row;
        const turn = thread.latest_turn;
        return {
          title: thread.title,
          status: t(
            row.state === "running"
              ? "conversations.working"
              : row.state === "completed"
                ? "conversations.replied"
                : "conversations.lastSendFailed",
          ),
          details: [
            agents.get(thread.agent_id ?? "")?.name ||
              providerLabel(turn?.provider, turn?.model || turn?.requested_model),
            thread.claim_id ? t(thread.node_id ? "conversations.nodeScope" : "conversations.claimScope") : "",
          ],
          url: href("chat", thread.id),
        };
      }
      case "review":
        return {
          title: row.item.claim_title || reviewItemKindLabel(t, row.item),
          status: t(`workspaceActivity.filter.${row.state}`),
          details: [row.item.claim_title && reviewItemKindLabel(t, row.item)],
          url: reviewItemHref(row.item),
        };
    }
  }

  return (
    <div className="agent-dock-activity">
      {hasActivity && (
        <div className="agent-dock-activity-filters" role="group" aria-label={t("workspaceActivity.filters")}>
          {FILTERS.map((state) => (
            <button key={state} type="button" aria-pressed={filter === state} onClick={() => setFilter(state)}>
              {t(`workspaceActivity.filter.${state}`)}
            </button>
          ))}
        </div>
      )}
      <div className="agent-dock-activity-body">
        {unavailable && <p role="status">{t("agentDock.activityUnavailable")}</p>}
        {rows.length ? (
          <ul>
            {rows.map((row) => {
              const { title, status, details, url } = rowContent(row);
              const summary = details.filter(Boolean).join(" · ");
              const caption =
                row.time &&
                t(`workspaceActivity.time.${row.time.kind}`, {
                  time: relativeTime(i18n.language, row.time.at, now),
                });
              const content = (
                <>
                  <strong>{title}</strong>
                  <span className="activity-row-state" data-tone={TONE[row.state]}>
                    <Mark tone={TONE[row.state]} />
                    {status}
                  </span>
                  {(summary || row.time) && (
                    <small>
                      {summary}
                      {summary && row.time && " · "}
                      {row.time && (
                        <time dateTime={row.time.at} title={timeText(i18n.language, row.time.at, true)}>
                          {caption}
                        </time>
                      )}
                    </small>
                  )}
                </>
              );
              const claimId = row.claimId;
              return (
                <li
                  key={row.id}
                  data-activity-source={row.source}
                  data-activity-state={row.state}
                  data-activity-id={row.id}
                >
                  {row.source === "job" && claimId ? (
                    <button
                      type="button"
                      onClick={() => {
                        onNavigate();
                        onOpenProof(claimId, row.nodeId ?? undefined);
                      }}
                    >
                      {content}
                    </button>
                  ) : (
                    <a
                      href={url}
                      onClick={(event) => {
                        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                        if (row.source === "chat") {
                          event.preventDefault();
                          onOpenThread(row.thread);
                        } else {
                          onNavigate();
                        }
                      }}
                    >
                      {content}
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        ) : loading ? (
          <p role="status">{t("common.loading")}</p>
        ) : (
          !unavailable && (
            <p className="agent-dock-activity-empty">
              <span>{t(filter === "all" ? "agentDock.noActivity" : "workspaceActivity.noMatches")}</span>{" "}
              {filter === "all" ? (
                <a href={href("claims", "agent-work")} onClick={onNavigate}>
                  {t("workspaceActivity.openWork")}
                </a>
              ) : (
                <button type="button" onClick={() => setFilter("all")}>
                  {t("workspaceActivity.showAll")}
                </button>
              )}
            </p>
          )
        )}
      </div>
    </div>
  );
}
