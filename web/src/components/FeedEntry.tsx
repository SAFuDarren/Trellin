import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { modelName, statusText, timeText } from "../workbench/text";
import type { ActivityRecord } from "../workbench/types";
import { Chevron, Mark, Spin } from "./Icons";

/** One line of a model conversation: your note, a live run, a model or Lean reply, or a recorded step. */
export function FeedEntry({ record, link }: { record: ActivityRecord & { kind: string }; link?: ReactNode }) {
  const { t, i18n } = useTranslation();
  const at = record.at || record.created_at;
  if (record.kind === "guidance") {
    const towardsCodex = record.audience === "codex";
    const progress = record.submitted_at
      ? t(towardsCodex ? "chat.submittedCodex" : "chat.submittedLocal")
      : record.current_scope === false
        ? t("chat.staleGuidance")
        : t(towardsCodex ? "chat.waitingCodex" : "chat.waitingLocal");
    const audience = t(`audience.${record.audience ?? "local"}`);
    return (
      <li data-from="you">
        <p className="bubble">{record.text ?? ""}</p>
        <span className="speaker">
          {[t("chat.you"), timeText(i18n.language, at, true), t("chat.sentTo", { audience }), progress]
            .filter(Boolean)
            .join(" · ")}
        </span>
        {link}
      </li>
    );
  }
  if (record.state === "running" && ["model", "worker", "lean", "task"].includes(record.kind)) {
    const who = record.kind === "model" ? modelName(t, record) : t(`activityKind.${record.kind}`);
    return (
      <li>
        <span className="live">
          <Spin />
          <b>{who}</b>
          {record.kind === "worker" ? t("chat.workerRunning") : t("chat.taskRunning")}
        </span>
        {link}
      </li>
    );
  }
  if (record.kind === "agent_feedback" || record.kind === "verifier_feedback") {
    return (
      <li>
        <span className="speaker">
          <b>{record.kind === "agent_feedback" ? modelName(t, record) : t("chat.lean")}</b>
          {timeText(i18n.language, at, true)}
        </span>
        <p className="message">{record.text ?? ""}</p>
        {link}
      </li>
    );
  }
  const label =
    record.kind === "model"
      ? `${t("activityKind.model")} · ${modelName(t, record)}`
      : t(`activityKind.${record.kind}`, { defaultValue: t("chat.record") });
  const tone = ["failed", "blocked", "lease_expired"].includes(record.state)
    ? "bad"
    : ["queued", "waiting_retry", "waiting_dependency"].includes(record.state)
      ? "planned"
      : "ok";
  return (
    <li>
      <span className="tool" title={record.text ?? ""}>
        <Chevron />
        {label}
        <span className="result">
          <Mark tone={tone} />
          {t(`activityState.${record.state}`, { defaultValue: statusText(t, record.state) })}
        </span>
        {timeText(i18n.language, at, true)}
      </span>
      {link}
    </li>
  );
}
