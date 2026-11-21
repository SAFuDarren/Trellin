import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "../../api/client";

export type LemmaPlanScope = {
  claimId: string;
  title: string;
  statementId: string;
  version: number;
  checksum: string;
  statement: string;
};

export function LemmaPlanPane({ session, scope }: { session: string; scope: LemmaPlanScope }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [draft, setDraft] = useState("");
  const [inputError, setInputError] = useState(false);
  const verification = useMutation({
    mutationFn: (plan: unknown) =>
      api<{ verified: number }>(session, "/api/lemma-plans", {
        data: { claim_id: scope.claimId, statement_id: scope.statementId, statement_sha256: scope.checksum, plan },
      }),
    onSuccess: () => setDraft(""),
    onSettled: () => {
      void queries.invalidateQueries({ queryKey: ["graph", scope.claimId] });
      void queries.invalidateQueries({ queryKey: ["atlas"] });
    },
  });
  const error = verification.error instanceof ApiError ? verification.error.message : "lemmaPlan.unavailable";

  return (
    <form
      className="project-entry-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (verification.isPending) return;
        try {
          verification.mutate(JSON.parse(draft));
        } catch {
          verification.reset();
          setInputError(true);
        }
      }}
    >
      <div className="project-entry-item">
        <strong>{scope.title}</strong>
        <span className="project-entry-note">{t("lemmaPlan.destination", { version: scope.version })}</span>
        <pre className="project-entry-lean lemma-plan-statement">{scope.statement}</pre>
      </div>
      <label>
        {t("lemmaPlan.input")}
        <textarea
          className="project-entry-lean"
          rows={7}
          maxLength={64_000}
          spellCheck={false}
          disabled={verification.isPending}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setInputError(false);
            verification.reset();
          }}
        />
      </label>
      <p className="project-entry-note">{t("lemmaPlan.hint")}</p>
      <div className="project-entry-buttons">
        <button className="button primary" disabled={!draft.trim() || verification.isPending} type="submit">
          {t("lemmaPlan.verify")}
        </button>
      </div>
      {verification.isPending && <p role="status">{t("lemmaPlan.checking")}</p>}
      {verification.data && (
        <p role="status" className="project-entry-status">
          {t("lemmaPlan.verified", { count: verification.data.verified })}
        </p>
      )}
      {(inputError || verification.isError) && (
        <p role="alert" className="project-entry-status">
          {inputError ? t("lemmaPlan.invalid") : t(error, { defaultValue: t("lemmaPlan.unavailable") })}
        </p>
      )}
    </form>
  );
}
