import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { normalizeReferenceCode } from "../../workbench/text";

export function ClaimCodesPane({
  session,
  claim,
  onSaved,
  onOpened,
}: {
  session: string;
  claim: { id: string; code: string } | null;
  onSaved: () => void | Promise<void>;
  onOpened: (id: string, nodeId: string | null) => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState(claim?.code ?? "");
  const [message, setMessage] = useState("");
  const save = useMutation({
    mutationFn: (data: { claim_id: string; code: string }) =>
      api<{ code: string }>(session, "/api/claim-code", { data }),
    onSuccess: ({ code }) => {
      setCode(code);
      setMessage(t("claimCodes.saved"));
      return onSaved();
    },
  });
  const open = useMutation({
    mutationFn: (reference: string) =>
      api<{ claim_id: string; node_id: string | null }>(
        session,
        "/api/claim-code?" + new URLSearchParams({ code: reference }),
      ),
    onSuccess: ({ claim_id, node_id }) => onOpened(claim_id, node_id),
  });
  const pending = save.isPending || open.isPending;
  const normalized = normalizeReferenceCode(code);
  function saveCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!claim || pending || !normalized || normalized === claim.code) return;
    setMessage("");
    save.mutate({ claim_id: claim.id, code: normalized });
  }
  function openCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const reference = normalizeReferenceCode(String(new FormData(event.currentTarget).get("reference") ?? ""));
    if (reference) open.mutate(reference);
  }
  return (
    <div className="project-entry-form">
      {claim && (
        <form className="project-entry-form" onSubmit={saveCode}>
          <label>
            {t("claimCodes.claim")}
            <input
              name="code"
              maxLength={12}
              autoCapitalize="characters"
              spellCheck={false}
              required
              value={code}
              onChange={(event) => {
                setMessage("");
                setCode(event.target.value);
              }}
            />
          </label>
          <p className="project-entry-note">{t("claimCodes.format")}</p>
          <p className="project-entry-note">{t("claimCodes.aliases")}</p>
          <div className="project-entry-buttons">
            <button
              className="button primary"
              type="submit"
              disabled={pending || !normalized || normalized === claim.code}
            >
              {t(save.isPending ? "common.loading" : "claimCodes.save")}
            </button>
          </div>
          {message && (
            <p className="project-entry-status" role="status">
              {message}
            </p>
          )}
          {save.error && (
            <p className="project-entry-status" role="alert">
              {t(`errors.${save.error.message}`, { defaultValue: t("projectEntry.failed") })}
            </p>
          )}
        </form>
      )}
      <form className={claim ? "project-entry-form project-entry-recent" : "project-entry-form"} onSubmit={openCode}>
        <label>
          {t("claimCodes.reference")}
          <input name="reference" maxLength={32} autoCapitalize="characters" spellCheck={false} required />
        </label>
        <p className="project-entry-note">{t("claimCodes.lookupHint")}</p>
        <div className="project-entry-buttons">
          <button className={claim ? "button" : "button primary"} type="submit" disabled={pending}>
            {t(open.isPending ? "common.loading" : "claimCodes.open")}
          </button>
        </div>
        {open.error && (
          <p className="project-entry-status" role="alert">
            {t(`errors.${open.error.message}`, { defaultValue: t("projectEntry.failed") })}
          </p>
        )}
      </form>
    </div>
  );
}
