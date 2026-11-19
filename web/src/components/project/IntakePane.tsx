import { useState, type ChangeEvent, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { connectionLabel, dispatchableConnection, useModelConnections } from "../../api/modelConnections";
import { href } from "../../shell/route";
import { ReviewFields } from "./ReviewFields";
import { reviewIdentity } from "./review";

const MAX_NOTES_BYTES = 18000;

type Candidate = {
  id: string;
  kind: "definition" | "hypothesis" | "theorem";
  natural: string;
  formal: Record<string, unknown> | null;
  question?: string;
  status: string;
};
type IntakeDraft = {
  id: string;
  sha256: string;
  title: string;
  items: Candidate[];
  confirmed: boolean;
};
type IntakeInput = { title: string; domain: "lean-core"; objective: string; notes: string };
type PublicIntakeInput = IntakeInput & { connection_id: string };
type ReleasePreview = {
  token: string;
  provider: string;
  model: string;
  outgoing: { domain: string; objective: string; notes: string };
  limits: {
    max_calls: number;
    timeout_seconds: number;
    max_output_tokens: number;
    output_token_limit_enforced: boolean;
  };
};
type Decision = { action: "accept" | "omit"; reason: string; formal?: { lean: { proposition: string } } };

function formalText(value: Candidate["formal"]): string {
  if (!value) return "";
  if (Object.keys(value).length === 1) {
    const lean = value.lean as { proposition?: unknown } | undefined;
    if (typeof lean?.proposition === "string" && Object.keys(lean).length === 1) return lean.proposition;
  }
  return JSON.stringify(value, null, 2);
}

export function IntakePane({ session, onRecorded }: { session: string; onRecorded: () => void }) {
  const { t } = useTranslation();
  const [notes, setNotes] = useState("");
  const [active, setActive] = useState<IntakeDraft | null>(null);
  const [fileError, setFileError] = useState("");
  const [message, setMessage] = useState("");
  const [release, setRelease] = useState<{ input: PublicIntakeInput; preview: ReleasePreview } | null>(null);
  const connections = useModelConnections(session);
  const queries = useQueryClient();
  const models = useQuery({
    queryKey: ["project-intake-models", session],
    queryFn: ({ signal }) => api<{ models: string[] }>(session, "/api/project-intake/models", { signal }),
    retry: false,
  });
  const recent = useQuery({
    queryKey: ["project-intake-drafts", session],
    queryFn: ({ signal }) => api<{ drafts: IntakeDraft[] }>(session, "/api/project-intake", { signal }),
    retry: false,
  });
  const save = () => void queries.invalidateQueries({ queryKey: ["project-intake-drafts", session] });
  const draft = useMutation({
    mutationFn: (input: (IntakeInput & { model: string }) | (PublicIntakeInput & { token: string })) =>
      api<IntakeDraft>(session, "token" in input ? "/api/project-intake/public-draft" : "/api/project-intake", {
        data: input,
      }),
    onSuccess: (result) => {
      setRelease(null);
      setActive(result);
      save();
    },
    // A dispatched preview cannot be replayed, including after a provider error.
    onError: () => setRelease(null),
  });
  const preview = useMutation({
    mutationFn: (input: PublicIntakeInput) =>
      api<ReleasePreview>(session, "/api/project-intake/public-preview", { data: input }),
    onSuccess: (result, input) => setRelease({ input, preview: result }),
  });
  const confirm = useMutation({
    mutationFn: (form: FormData) => {
      if (!active) throw new Error("intake.invalid_input");
      const identity = reviewIdentity(form);
      const decisions: Record<string, Decision> = {};
      for (const item of active.items) {
        if (item.status === "reuse") continue;
        const action = form.get(`${item.id}-action`);
        if (action !== "accept" && action !== "omit") throw new Error("intake.invalid_input");
        const formal = String(form.get(`${item.id}-lean`) ?? "").trim();
        if (action === "accept" && !item.formal && !formal) throw new Error("intake.formalization_required");
        decisions[item.id] = {
          action,
          reason: identity.reason,
          ...(action === "accept" && formal ? { formal: { lean: { proposition: formal } } } : {}),
        };
      }
      return api<{ confirmed: boolean }>(session, "/api/project-intake/confirm", {
        data: { id: active.id, sha256: active.sha256, decisions, ...identity },
      });
    },
    onSuccess: () => {
      setActive(null);
      setMessage(t("projectEntry.intake.reviewed"));
      save();
      onRecorded();
    },
  });

  async function readFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setFileError("");
    if (file.size > MAX_NOTES_BYTES) {
      setFileError(t("projectEntry.intake.tooLarge"));
      return;
    }
    try {
      setNotes(await file.text());
    } catch {
      setFileError(t("projectEntry.intake.fileError"));
    }
  }
  function requestDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setFileError("");
    setRelease(null);
    draft.reset();
    preview.reset();
    const form = new FormData(event.currentTarget);
    const selection = String(form.get("model") ?? "");
    if (!selection) return;
    const input: IntakeInput = {
      title: String(form.get("title")).trim(),
      domain: "lean-core",
      objective: String(form.get("objective") ?? "").trim() || t("projectEntry.intake.objectiveDefault"),
      notes: notes.trim(),
    };
    if (selection.startsWith("connection:")) {
      preview.mutate({ ...input, connection_id: selection.slice("connection:".length) });
    } else {
      draft.mutate({ ...input, model: selection });
    }
  }
  function confirmReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate(new FormData(event.currentTarget));
  }

  const modelNames = models.data?.models ?? [];
  const available = (connections.data?.connections ?? []).filter(dispatchableConnection);
  const canChoose = modelNames.length > 0 || available.length > 0;
  const working = draft.isPending || preview.isPending;
  const tooLarge = new TextEncoder().encode(notes).length > MAX_NOTES_BYTES;
  const failure = active ? confirm.error : (draft.error ?? preview.error);
  const pending = recent.data?.drafts?.filter((item) => !item.confirmed) ?? [];
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
        <form key={active.id} className="project-entry-form" onSubmit={confirmReview}>
          <h3>{active.title}</h3>
          <p className="project-entry-note">{t("projectEntry.intake.reviewHint")}</p>
          {active.items.length ? (
            <ol className="project-entry-items">
              {active.items.map((item) => (
                <li key={item.id} className="project-entry-item">
                  <h4>{t(`projectEntry.intake.kinds.${item.kind}`)}</h4>
                  <blockquote>{item.natural}</blockquote>
                  {item.question && <p className="project-entry-note">{item.question}</p>}
                  {item.formal && <pre className="code-block">{formalText(item.formal)}</pre>}
                  {item.status === "reuse" ? (
                    <p className="project-entry-note">{t("projectEntry.intake.exactMatch")}</p>
                  ) : (
                    <>
                      <label>
                        {t("projectEntry.intake.decision")}
                        <select
                          name={`${item.id}-action`}
                          aria-label={t("projectEntry.intake.decisionFor", { id: item.id })}
                          required
                        >
                          <option value="">{t("projectEntry.intake.chooseDecision")}</option>
                          <option value="accept">{t("projectEntry.intake.accept")}</option>
                          <option value="omit">{t("projectEntry.intake.omit")}</option>
                        </select>
                      </label>
                      {!item.formal && (
                        <label>
                          {t("projectEntry.intake.supplyLean")}
                          <textarea className="project-entry-lean" name={`${item.id}-lean`} rows={2} maxLength={4000} />
                        </label>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <p className="project-entry-note">{t("projectEntry.intake.noCandidates")}</p>
          )}
          {active.items.length > 0 && <ReviewFields />}
          <div className="project-entry-buttons">
            <button className="button" type="button" disabled={confirm.isPending} onClick={() => setActive(null)}>
              {t("projectEntry.intake.back")}
            </button>
            {active.items.length > 0 && (
              <button className="button primary" type="submit" disabled={confirm.isPending}>
                {t(confirm.isPending ? "common.loading" : "projectEntry.intake.confirm")}
              </button>
            )}
          </div>
        </form>
      ) : (
        <form className="project-entry-form" onSubmit={requestDraft}>
          <p className="project-entry-note">{t("modelConnections.intakeHelp")}</p>
          <div className="project-entry-columns">
            <label>
              {t("projectEntry.title")}
              <input
                name="title"
                defaultValue={t("projectEntry.intake.defaultTitle")}
                maxLength={300}
                required
                disabled={working || Boolean(release)}
              />
            </label>
            <label>
              {t("projectEntry.intake.model")}
              <select name="model" required disabled={!canChoose || working || Boolean(release)}>
                {!modelNames.length && (
                  <option value="">
                    {t(canChoose ? "modelConnections.chooseConnection" : "projectEntry.intake.noModels")}
                  </option>
                )}
                {modelNames.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
                {available.map((connection) => (
                  <option key={connection.id} value={`connection:${connection.id}`}>
                    {t(`team.providers.${connection.provider}`, { defaultValue: connection.provider })}
                    {" · "}
                    {connectionLabel(connection)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!models.isPending && !connections.isPending && !canChoose && (
            <div className="project-entry-buttons">
              <a className="button" href={href("settings")}>
                {t("modelConnections.openSettings")}
              </a>
              <button
                className="button"
                type="button"
                onClick={() => void Promise.all([models.refetch(), connections.refetch()])}
              >
                {t("projectEntry.intake.refreshModels")}
              </button>
            </div>
          )}
          <label>
            {t("projectEntry.intake.objective")}
            <input name="objective" maxLength={4000} disabled={working || Boolean(release)} />
          </label>
          <label>
            {t("projectEntry.intake.notes")}
            <textarea
              rows={8}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              required
              disabled={working || Boolean(release)}
            />
          </label>
          <label className="project-entry-file">
            {t("projectEntry.intake.readFile")}
            <input
              type="file"
              disabled={working || Boolean(release)}
              accept=".txt,.md,.lean,text/plain,text/markdown"
              onChange={(event) => void readFile(event)}
            />
          </label>
          {(fileError || tooLarge) && (
            <p className="project-entry-status" role="alert">
              {fileError || t("projectEntry.intake.tooLarge")}
            </p>
          )}
          <div className="project-entry-buttons">
            <button
              className="button primary"
              type="submit"
              disabled={working || Boolean(release) || tooLarge || !notes.trim() || !canChoose}
            >
              {t(draft.isPending ? "projectEntry.intake.analyzing" : "projectEntry.intake.analyze")}
            </button>
          </div>
          {release && (
            <section className="project-entry-item" aria-labelledby="intake-release">
              <h3 id="intake-release">{t("modelConnections.publicPreview")}</h3>
              <p>
                {t(`team.providers.${release.preview.provider}`, { defaultValue: release.preview.provider })}
                {" · "}
                {release.preview.model}
              </p>
              <p>{t("modelConnections.previewLimits", { seconds: release.preview.limits.timeout_seconds })}</p>
              <pre className="code-block">{JSON.stringify(release.preview.outgoing, null, 2)}</pre>
              <div className="project-entry-buttons">
                <button type="button" className="button" disabled={draft.isPending} onClick={() => setRelease(null)}>
                  {t("modelConnections.editPreview")}
                </button>
                <button
                  type="button"
                  className="button primary"
                  disabled={draft.isPending}
                  onClick={() => draft.mutate({ ...release.input, token: release.preview.token })}
                >
                  {t("modelConnections.sendPublic")}
                </button>
              </div>
            </section>
          )}
          {working && (
            <p className="project-entry-note" role="status">
              {t("projectEntry.intake.running")}
            </p>
          )}
        </form>
      )}
      {!active && pending.length > 0 && (
        <section className="project-entry-recent">
          <h3>{t("projectEntry.intake.pending")}</h3>
          <ul>
            {pending.map((item) => (
              <li key={item.id}>
                <button
                  className="button"
                  type="button"
                  onClick={() => {
                    setMessage("");
                    confirm.reset();
                    setActive(item);
                  }}
                >
                  {item.title}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
