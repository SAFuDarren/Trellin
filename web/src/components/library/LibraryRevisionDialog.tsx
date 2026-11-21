import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";

type Text = { natural: string; lean: string };
type LibraryRevisionItem = {
  id: string;
  version: number;
  sha256: string;
  natural: string | null;
  lean: string | null;
  scope?: Record<string, unknown> | null;
  mode: "basis" | "goal" | "successor" | "unsupported";
  available: boolean;
};
type Preview = {
  base: LibraryRevisionItem;
  after: Text;
  affected: { checks: number; receipts: number; claims: number };
  token: string;
};
type Revision = { id: string; version: number };

function RevisionEditor({
  session,
  item,
  onCancel,
  onSaved,
  onPending,
}: {
  session: string;
  item: LibraryRevisionItem;
  onCancel: () => void;
  onSaved: (revision: Revision) => Promise<void>;
  onPending: (pending: boolean) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Text>({ natural: item.natural ?? "", lean: item.lean ?? "" });
  const [comparison, setComparison] = useState<Preview>();
  const data = { id: item.id, sha256: item.sha256, ...draft };
  const review = useMutation({
    mutationFn: () => api<Preview>(session, "/api/library/preview", { data }),
    onSuccess: setComparison,
  });
  const confirm = useMutation({
    mutationFn: ({ preview, reason }: { preview: Preview; reason: string }) =>
      api<Revision>(session, "/api/library/revise", {
        data: { id: preview.base.id, sha256: preview.base.sha256, ...preview.after, token: preview.token, reason },
      }),
    onMutate: () => onPending(true),
    onSuccess: onSaved,
    onSettled: () => onPending(false),
  });
  const error = comparison ? confirm.error : review.error;
  const changed = draft.natural !== item.natural || draft.lean !== item.lean;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (confirm.isPending || review.isPending) return;
    if (!comparison) {
      if (changed) review.mutate();
      return;
    }
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "");
    if (reason.trim()) confirm.mutate({ preview: comparison, reason });
  }

  return (
    <form className="project-entry-form" onSubmit={submit}>
      <p className="project-entry-note">{t("libraryEdit.baseVersion", { version: item.version })}</p>
      {item.scope && Object.keys(item.scope).length > 0 && (
        <details>
          <summary>{t("libraryEdit.fixedScope")}</summary>
          <pre className="code-block">{JSON.stringify(item.scope, null, 2)}</pre>
        </details>
      )}
      {error && (
        <p className="project-entry-status" role="alert">
          {t(error.message, { defaultValue: t("libraryEdit.unavailable") })}
        </p>
      )}
      {comparison ? (
        <>
          <div className="project-entry-columns">
            <section className="project-entry-item">
              <h3>{t("libraryEdit.before")}</h3>
              <blockquote>{comparison.base.natural}</blockquote>
              <pre className="code-block">{comparison.base.lean}</pre>
            </section>
            <section className="project-entry-item">
              <h3>{t("libraryEdit.after")}</h3>
              <blockquote>{comparison.after.natural}</blockquote>
              <pre className="code-block">{comparison.after.lean}</pre>
            </section>
          </div>
          <p className="project-entry-note">
            {t(`libraryEdit.impact.${comparison.base.mode}`, { ...comparison.affected })}
          </p>
          <label>
            {t("libraryEdit.reason")}
            <input name="reason" required maxLength={2000} disabled={confirm.isPending} />
          </label>
        </>
      ) : (
        <>
          <label>
            {t("detail.statement")}
            <textarea
              value={draft.natural}
              onChange={(event) => setDraft({ ...draft, natural: event.target.value })}
              required
              maxLength={4000}
              disabled={review.isPending}
            />
          </label>
          <label>
            {t("detail.lean")}
            <textarea
              className="project-entry-lean"
              value={draft.lean}
              onChange={(event) => setDraft({ ...draft, lean: event.target.value })}
              required
              maxLength={16000}
              disabled={review.isPending}
            />
          </label>
        </>
      )}
      <div className="project-entry-buttons">
        <button className="button" type="button" disabled={confirm.isPending} onClick={onCancel}>
          {t("libraryEdit.discard")}
        </button>
        {comparison && (
          <button
            className="button"
            type="button"
            disabled={confirm.isPending}
            onClick={() => {
              confirm.reset();
              setComparison(undefined);
            }}
          >
            {t("libraryEdit.back")}
          </button>
        )}
        <button
          className="button primary-button"
          type="submit"
          disabled={!changed || review.isPending || confirm.isPending}
        >
          {t(comparison ? "libraryEdit.confirm" : "libraryEdit.review")}
        </button>
      </div>
    </form>
  );
}

export function LibraryRevisionDialog({
  session,
  id,
  onCancel,
  onSaved,
}: {
  session: string;
  id: string;
  onCancel: () => void;
  onSaved: (revision: Revision) => Promise<void>;
}) {
  const { t } = useTranslation();
  const title = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  const item = useQuery({
    queryKey: ["library-revision-item", session, id],
    queryFn: ({ signal }) =>
      api<LibraryRevisionItem>(session, "/api/library/item?" + new URLSearchParams({ version: id }), { signal }),
    retry: false,
    gcTime: 0,
  });
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  function cancel() {
    dialog.current?.close();
    onCancel();
  }

  return (
    <dialog
      ref={dialog}
      className="project-entry"
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) cancel();
      }}
    >
      <header className="project-entry-head">
        <h2 id={title}>{t("libraryEdit.title")}</h2>
        <button className="button" type="button" disabled={pending} onClick={cancel}>
          {t("libraryEdit.close")}
        </button>
      </header>
      <div className="project-entry-body">
        {item.isPending ? (
          <p role="status">{t("common.loading")}</p>
        ) : item.isError ? (
          <p role="alert">{t(item.error.message, { defaultValue: t("libraryEdit.unavailable") })}</p>
        ) : !item.data.available ? (
          <p role="status">{t("libraryEdit.stale")}</p>
        ) : item.data.mode === "unsupported" || item.data.lean === null ? (
          <p role="status">{t("libraryEdit.unsupported")}</p>
        ) : (
          <RevisionEditor
            item={item.data}
            session={session}
            onCancel={cancel}
            onSaved={onSaved}
            onPending={setPending}
          />
        )}
      </div>
    </dialog>
  );
}
