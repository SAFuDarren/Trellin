import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { ReviewFields } from "./ReviewFields";
import { reviewIdentity } from "./review";

type DraftTheorem = { id: string; sha256: string; title: string; wording: string; lean: string; source: string };
type AddedTheorem =
  | { kind: "library"; status: "draft" | "confirmed"; id: string; sha256: string }
  | { kind: "goal"; status: "open"; claim_id: string };

export function TheoremPane({ session, onRecorded }: { session: string; onRecorded: (claimId?: string) => void }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [kind, setKind] = useState<"goal" | "library">("goal");
  const [active, setActive] = useState<DraftTheorem | null>(null);
  const [message, setMessage] = useState("");
  const pending = useQuery({
    queryKey: ["manual-theorems", session],
    queryFn: ({ signal }) => api<{ drafts: DraftTheorem[] }>(session, "/api/theorems", { signal }),
    retry: false,
  });
  const sync = () => void queries.invalidateQueries({ queryKey: ["manual-theorems", session] });
  const add = useMutation({
    mutationFn: async (event: FormData) => {
      const fields = {
        title: String(event.get("title")).trim(),
        wording: String(event.get("wording")).trim(),
        lean: String(event.get("lean")).trim(),
        source: String(event.get("source") ?? "").trim(),
      };
      const data = {
        ...fields,
        kind,
        ...(kind === "goal" ? { confirmed: event.has("confirmed"), ...reviewIdentity(event) } : {}),
      };
      const result = await api<AddedTheorem>(session, "/api/theorems", { data });
      return { result, fields };
    },
    onSuccess: ({ result, fields }) => {
      sync();
      if (result.kind === "goal") {
        onRecorded(result.claim_id);
      } else {
        setMessage(t(result.status === "confirmed" ? "projectEntry.theorem.confirmed" : "projectEntry.theorem.saved"));
        if (result.status === "draft") setActive({ ...fields, id: result.id, sha256: result.sha256 });
        onRecorded();
      }
    },
  });
  const confirm = useMutation({
    mutationFn: (data: FormData) =>
      api<AddedTheorem>(session, "/api/theorems/confirm", {
        data: { id: active!.id, sha256: active!.sha256, ...reviewIdentity(data) },
      }),
    onSuccess: () => {
      setActive(null);
      setMessage(t("projectEntry.theorem.confirmed"));
      sync();
      onRecorded();
    },
  });
  function addTheorem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    add.mutate(new FormData(event.currentTarget));
  }
  function confirmTheorem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate(new FormData(event.currentTarget));
  }
  const failure = active ? confirm.error : add.error;
  return (
    <>
      {message && (
        <p className="project-entry-status" role="status">
          {message}
        </p>
      )}
      {failure && (
        <p className="project-entry-status" role="alert">
          {t(`errors.${failure.message}`, { defaultValue: t("projectEntry.failed") })}
        </p>
      )}
      {active ? (
        <form className="project-entry-form" onSubmit={confirmTheorem}>
          <h3>{active.title}</h3>
          <p>{active.wording}</p>
          <pre className="code-block">{active.lean}</pre>
          {active.source && <p className="project-entry-note">{active.source}</p>}
          <p className="project-entry-note">{t("projectEntry.theorem.libraryReview")}</p>
          <ReviewFields />
          <div className="project-entry-buttons">
            <button className="button" type="button" disabled={confirm.isPending} onClick={() => setActive(null)}>
              {t("projectEntry.theorem.back")}
            </button>
            <button className="button primary" type="submit" disabled={confirm.isPending}>
              {t(confirm.isPending ? "common.loading" : "projectEntry.theorem.confirm")}
            </button>
          </div>
        </form>
      ) : (
        <form className="project-entry-form" onSubmit={addTheorem}>
          <label>
            {t("projectEntry.theorem.kind")}
            <select value={kind} onChange={(event) => setKind(event.target.value === "library" ? "library" : "goal")}>
              <option value="goal">{t("projectEntry.theorem.goal")}</option>
              <option value="library">{t("projectEntry.theorem.library")}</option>
            </select>
          </label>
          <label>
            {t("projectEntry.title")}
            <input name="title" maxLength={300} required />
          </label>
          <label>
            {t("projectEntry.theorem.wording")}
            <textarea name="wording" rows={2} maxLength={4000} required />
          </label>
          <label>
            {t("projectEntry.lean")}
            <textarea className="project-entry-lean" name="lean" rows={3} maxLength={4000} required />
          </label>
          {kind === "library" ? (
            <label>
              {t("projectEntry.theorem.source")}
              <input name="source" maxLength={1000} />
            </label>
          ) : (
            <>
              <label className="project-entry-check">
                <input name="confirmed" type="checkbox" required />
                {t("projectEntry.theorem.goalReview")}
              </label>
              <ReviewFields />
            </>
          )}
          <div className="project-entry-buttons">
            <button className="button primary" type="submit" disabled={add.isPending}>
              {t(
                add.isPending
                  ? "common.loading"
                  : kind === "goal"
                    ? "projectEntry.theorem.saveGoal"
                    : "projectEntry.theorem.saveDraft",
              )}
            </button>
          </div>
        </form>
      )}
      {!active && (pending.data?.drafts?.length ?? 0) > 0 && (
        <section className="project-entry-recent">
          <h3>{t("projectEntry.theorem.pending")}</h3>
          <ul>
            {pending.data!.drafts.map((draft) => (
              <li key={draft.id}>
                <button
                  className="button"
                  type="button"
                  onClick={() => {
                    setMessage("");
                    setActive(draft);
                  }}
                >
                  {draft.title}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
