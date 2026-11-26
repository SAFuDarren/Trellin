import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useDecisionRecords, type DecisionRecord } from "../api/sections";
import { timeText } from "../workbench/text";
import { Empty, PageHead } from "./SectionLayout";
import "../styles/decision-records.css";

// #345: the decisions people recorded, newest first. What still waits is listed in Needs you.

const FILTERS = ["all", "evidence", "claims", "intake"] as const;
type Filter = (typeof FILTERS)[number];

function matches(record: DecisionRecord, filter: Filter) {
  if (filter === "evidence") return Boolean(record.evidence_id);
  if (filter === "claims") return record.source === "claim" && !record.evidence_id;
  if (filter === "intake") return record.source === "intake";
  return true;
}

export function DecisionRecords({ session, onOpen }: { session: string; onOpen: (claimId: string) => void }) {
  const { t, i18n } = useTranslation();
  const records = useDecisionRecords(session);
  const [filter, setFilter] = useState<Filter>("all");
  const list = records.data?.records ?? [];
  const shown = list.filter((record) => matches(record, filter));
  const present = FILTERS.filter((key) => key === "all" || list.some((record) => matches(record, key)));

  return (
    <>
      <PageHead title={t("decisionRecords.title")} note={t("decisionRecords.count", { count: list.length })} />
      <div className="page-body decision-records">
        <p className="page-note">{t("decisionRecords.explain")}</p>
        {present.length > 2 && (
          <div className="decision-records-filter" role="group" aria-label={t("decisionRecords.filterLabel")}>
            {present.map((key) => (
              <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>
                {t(`decisionRecords.filters.${key}`)}
              </button>
            ))}
          </div>
        )}
        {records.isPending ? (
          <Empty>{t("common.loading")}</Empty>
        ) : records.isError ? (
          <Empty>{t("decisionRecords.unavailable")}</Empty>
        ) : shown.length === 0 ? (
          <Empty>{t("decisionRecords.none")}</Empty>
        ) : (
          <ol className="decision-records-list" aria-label={t("decisionRecords.title")}>
            {shown.map((record) => {
              const subject =
                record.title ||
                t(
                  record.source === "intake"
                    ? "decisionRecords.subjects.intake"
                    : record.evidence_id
                      ? "decisionRecords.subjects.evidence"
                      : "decisionRecords.subjects.claim",
                );
              return (
                <li key={`${record.source}:${record.id}`} className="decision-record" data-action={record.action}>
                  <span className="decision-record-action">
                    {t(`decisionRecords.actions.${record.action}`, { defaultValue: record.action })}
                  </span>
                  <div className="decision-record-main">
                    <strong>{subject}</strong>
                    {record.claim_id && record.claim_title && (
                      <button type="button" className="decision-record-claim" onClick={() => onOpen(record.claim_id!)}>
                        {record.claim_title}
                      </button>
                    )}
                    <q>{record.reason}</q>
                  </div>
                  <span className="decision-record-meta">
                    <span>{record.actor}</span>
                    <time dateTime={record.decided_at}>{timeText(i18n.language, record.decided_at, true)}</time>
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </>
  );
}
