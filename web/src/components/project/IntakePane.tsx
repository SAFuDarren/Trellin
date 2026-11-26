import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { useLocalTextModels } from "../../api/localModels";
import { useIntakeGrantChange, useIntakeGrants } from "../../api/intakeGrants";
import { connectionChoiceLabel, dispatchableConnection, useModelConnections } from "../../api/modelConnections";
import { useInbox } from "../../api/sections";
import { draggingFiles } from "./MapDrop";
import { Picker } from "../FloatingControls";
import { href } from "../../shell/route";
import { ReviewFields } from "./ReviewFields";
import { formalText, reviewIdentity, useReviewSigner } from "./review";

const MAX_NOTES_BYTES = 18000;
/** Text formats the import reads; PDF and other binaries wait for the material reader. */
const TEXT_FILES = /\.(txt|md|markdown|tex|lean)$/i;
const TEXT_ACCEPT = ".txt,.md,.markdown,.tex,.lean,text/plain,text/markdown";

/** Whether a dropped or chosen file is one the import can read as text. */
export function readableNotesFile(file: File) {
  return TEXT_FILES.test(file.name) || file.type === "text/plain" || file.type === "text/markdown";
}

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
type Decision = { action: "accept" | "omit"; reason: string; formal?: { lean: { proposition: string } } };

export function IntakePane({
  session,
  file,
  onRecorded,
}: {
  session: string;
  /** #353: a file dropped on the map; its text fills the notes and its name the title. */
  file?: File | null;
  onRecorded: () => void;
}) {
  const { t } = useTranslation();
  const signer = useReviewSigner(session);
  // Databases without a local identity expose no workspace ID; their choices share one key.
  const project = useInbox(session).data?.review_identity?.workspace_id ?? "default";
  const [notes, setNotes] = useState("");
  const [source, setSource] = useState<{ name: string; size: number } | null>(null);
  const [pasting, setPasting] = useState(false);
  const [objective, setObjective] = useState("");
  const [modelChoice, setModelChoice] = useState<string | null>(null);
  const [advanced, setAdvanced] = useStoredFlag(`trellin-import-advanced:${project}`);
  const [askGrant, setAskGrant] = useState(false);
  const [active, setActive] = useState<IntakeDraft | null>(null);
  const [fileError, setFileError] = useState("");
  const [message, setMessage] = useState("");
  const chooser = useRef<HTMLInputElement>(null);
  const connections = useModelConnections(session);
  const grants = useIntakeGrants(session);
  const grantChange = useIntakeGrantChange(session);
  const queries = useQueryClient();
  const models = useLocalTextModels(session);
  const recent = useQuery({
    queryKey: ["project-intake-drafts", session],
    queryFn: ({ signal }) => api<{ drafts: IntakeDraft[] }>(session, "/api/project-intake", { signal }),
    retry: false,
  });
  const save = () => void queries.invalidateQueries({ queryKey: ["project-intake-drafts", session] });
  const draft = useMutation({
    mutationFn: (input: (IntakeInput & { model: string }) | PublicIntakeInput) =>
      api<IntakeDraft>(session, "connection_id" in input ? "/api/project-intake/public-draft" : "/api/project-intake", {
        data: input,
      }),
    onSuccess: (result, input) => {
      rememberModel(project, "connection_id" in input ? `connection:${input.connection_id}` : input.model);
      setAskGrant(false);
      setActive(result);
      save();
    },
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

  const [title, setTitle] = useState(() => (file ? fileTitle(file) : t("projectEntry.intake.defaultTitle")));
  async function load(source: File) {
    setMessage("");
    setFileError("");
    if (!readableNotesFile(source)) {
      setFileError(t("projectEntry.intake.unsupported"));
      return;
    }
    if (source.size > MAX_NOTES_BYTES) {
      setFileError(t("projectEntry.intake.tooLarge"));
      return;
    }
    try {
      const text = await source.text();
      if (text.includes("\u0000")) throw new Error("binary");
      setNotes(text);
      setSource({ name: source.name, size: source.size });
      setPasting(false);
      setTitle(fileTitle(source));
    } catch {
      setFileError(t("projectEntry.intake.fileError"));
    }
  }
  useEffect(() => {
    if (file) void load(file);
    // Read the dropped file once; the parent remounts this pane for another file.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);
  async function readFile(event: ChangeEvent<HTMLInputElement>) {
    const source = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (source) await load(source);
  }
  function organize(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setMessage("");
    setFileError("");
    draft.reset();
    grantChange.reset();
    if (!selectionValid || !notes.trim()) return;
    const input: IntakeInput = {
      title: title.trim() || t("projectEntry.intake.defaultTitle"),
      domain: "lean-core",
      objective: objective.trim() || t("projectEntry.intake.objectiveDefault"),
      notes: notes.trim(),
    };
    if (selectedConnection) {
      if (!granted) {
        setAskGrant(true);
        return;
      }
      draft.mutate({ ...input, connection_id: selectedConnection.id });
    } else {
      draft.mutate({ ...input, model: selectedModel });
    }
  }
  async function allowAndOrganize() {
    if (!selectedConnection) return;
    try {
      await grantChange.mutateAsync({ action: "grant", connectionId: selectedConnection.id });
    } catch {
      return;
    }
    setAskGrant(false);
    draft.mutate({
      title: title.trim() || t("projectEntry.intake.defaultTitle"),
      domain: "lean-core",
      objective: objective.trim() || t("projectEntry.intake.objectiveDefault"),
      notes: notes.trim(),
      connection_id: selectedConnection.id,
    });
  }
  function confirmReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate(new FormData(event.currentTarget));
  }

  const modelNames = models.data?.models ?? [];
  const available = (connections.data?.connections ?? []).filter(dispatchableConnection);
  const canChoose = modelNames.length > 0 || available.length > 0;
  const choices = [...modelNames, ...available.map((connection) => `connection:${connection.id}`)];
  const remembered = rememberedModel(project);
  const selectedModel =
    [modelChoice, remembered].find((choice): choice is string => !!choice && choices.includes(choice)) ??
    choices[0] ??
    "";
  const selectedConnection = available.find((connection) => selectedModel === `connection:${connection.id}`);
  const selectionValid = Boolean(selectedConnection || modelNames.includes(selectedModel));
  const granted = Boolean(
    selectedConnection && grants.data?.grants.some((grant) => grant.connection_id === selectedConnection.id),
  );
  const modelName = selectedConnection ? connectionChoiceLabel(t, selectedConnection) : selectedModel;
  const working = draft.isPending || grantChange.isPending;
  const tooLarge = new TextEncoder().encode(notes).length > MAX_NOTES_BYTES;
  const hasMaterial = Boolean(notes.trim()) || pasting;
  const checking = models.isPending || connections.isPending;
  const failure = active ? confirm.error : (draft.error ?? grantChange.error);
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
          {active.items.length > 0 && <ReviewFields signer={signer} />}
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
        <form
          className="project-entry-form import-form"
          onSubmit={organize}
          onDragOver={(event) => {
            if (!draggingFiles(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
          }}
          onDrop={(event) => {
            if (!draggingFiles(event)) return;
            event.preventDefault();
            const dropped = event.dataTransfer.files[0];
            if (dropped) void load(dropped);
          }}
        >
          <input
            ref={chooser}
            className="import-file-input"
            type="file"
            tabIndex={-1}
            aria-hidden="true"
            accept={TEXT_ACCEPT}
            onChange={(event) => void readFile(event)}
          />
          {!hasMaterial ? (
            <section className="import-drop" aria-label={t("projectEntry.intake.dropTitle")}>
              <p className="import-drop-title">{t("projectEntry.intake.dropTitle")}</p>
              <div className="import-drop-actions">
                <button type="button" className="button" onClick={() => chooser.current?.click()}>
                  {t("projectEntry.intake.choose")}
                </button>
                <button type="button" className="conversation-text-action" onClick={() => setPasting(true)}>
                  {t("projectEntry.intake.paste")}
                </button>
              </div>
            </section>
          ) : source ? (
            <div className="import-source">
              <span className="import-source-name">{source.name}</span>
              <span className="import-source-size">{formatSize(source.size)}</span>
              <button
                type="button"
                className="conversation-text-action"
                disabled={working}
                onClick={() => chooser.current?.click()}
              >
                {t("projectEntry.intake.replace")}
              </button>
            </div>
          ) : (
            <label>
              {t("projectEntry.intake.notes")}
              <textarea rows={8} value={notes} onChange={(event) => setNotes(event.target.value)} disabled={working} />
            </label>
          )}
          {(fileError || tooLarge) && (
            <p className="project-entry-status" role="alert">
              {fileError || t("projectEntry.intake.tooLarge")}
            </p>
          )}
          {connections.isError && (
            <p className="project-entry-status" role="alert">
              {t("errors.connection.unavailable")}
            </p>
          )}
          {!checking && !canChoose ? (
            <div className="import-model">
              <span>{t("projectEntry.intake.noModel")}</span>
              <a className="conversation-text-action" href={href("team", "runtimes")}>
                {t("projectEntry.intake.connectModel")}
              </a>
              <button
                className="conversation-text-action"
                type="button"
                onClick={() => void Promise.all([models.refetch(), connections.refetch()])}
              >
                {t("projectEntry.intake.refreshModels")}
              </button>
            </div>
          ) : (
            selectionValid && <p className="import-model">{t("projectEntry.intake.readBy", { model: modelName })}</p>
          )}
          {askGrant && selectedConnection && !granted && (
            <div className="import-grant" role="group" aria-label={t("projectEntry.intake.allowLabel")}>
              <p>
                {t("projectEntry.intake.allowAsk", {
                  model: modelName,
                  provider: t(`team.providers.${selectedConnection.provider}`, {
                    defaultValue: selectedConnection.provider,
                  }),
                })}
              </p>
              <div className="project-entry-buttons">
                <button
                  type="button"
                  className="button primary"
                  disabled={working}
                  onClick={() => void allowAndOrganize()}
                >
                  {t("projectEntry.intake.allow")}
                </button>
                <button
                  type="button"
                  className="button"
                  disabled={working}
                  onClick={() => {
                    setAskGrant(false);
                    setAdvanced(true);
                  }}
                >
                  {t("projectEntry.intake.otherModel")}
                </button>
              </div>
            </div>
          )}
          {hasMaterial && !askGrant && (
            <div className="project-entry-buttons">
              <button
                className="button primary"
                type="submit"
                disabled={working || tooLarge || !notes.trim() || !selectionValid}
              >
                {t(draft.isPending ? "projectEntry.intake.analyzing" : "projectEntry.intake.analyze")}
              </button>
            </div>
          )}
          {working && (
            <p className="project-entry-note" role="status">
              {t("projectEntry.intake.running")}
            </p>
          )}
          <button
            type="button"
            className="conversation-text-action import-advanced-toggle"
            aria-expanded={advanced}
            aria-controls="import-advanced"
            onClick={() => setAdvanced(!advanced)}
          >
            {t("projectEntry.intake.advanced")}
          </button>
          {advanced && (
            <section id="import-advanced" className="import-advanced" aria-label={t("projectEntry.intake.advanced")}>
              <p className="project-entry-note">{t("modelConnections.intakeHelp")}</p>
              <div className="project-entry-columns">
                <label>
                  {t("projectEntry.title")}
                  <input
                    name="title"
                    value={title}
                    onChange={(event) => setTitle(event.currentTarget.value)}
                    maxLength={300}
                    disabled={working}
                  />
                </label>
                <div className="project-entry-field">
                  <label htmlFor="intake-model-picker">{t("projectEntry.intake.model")}</label>
                  <Picker
                    label={t("projectEntry.intake.model")}
                    triggerId="intake-model-picker"
                    triggerClassName="project-entry-picker"
                    trigger={
                      selectionValid
                        ? modelName
                        : t(canChoose ? "modelConnections.chooseConnection" : "projectEntry.intake.noModels")
                    }
                    value={selectionValid ? selectedModel : ""}
                    disabled={!canChoose || working}
                    groups={[
                      {
                        label: t("conversations.onDevice"),
                        options: modelNames.map((model) => ({ id: model, label: model })),
                      },
                      {
                        label: t("conversations.connectedModels"),
                        options: available.map((connection) => ({
                          id: `connection:${connection.id}`,
                          label: connectionChoiceLabel(t, connection),
                        })),
                      },
                    ]}
                    searchLabel={t("conversations.searchModels")}
                    emptyLabel={t("picker.noMatches")}
                    onSelect={(choice) => {
                      setModelChoice(choice);
                      setAskGrant(false);
                    }}
                  />
                </div>
              </div>
              <label>
                {t("projectEntry.intake.objective")}
                <input
                  name="objective"
                  value={objective}
                  onChange={(event) => setObjective(event.currentTarget.value)}
                  maxLength={4000}
                  disabled={working}
                />
              </label>
              {source && (
                <label>
                  {t("projectEntry.intake.notes")}
                  <textarea
                    rows={8}
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                    disabled={working}
                  />
                </label>
              )}
            </section>
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

function fileTitle(file: File) {
  return file.name.replace(/\.[^.]+$/, "").slice(0, 300) || file.name.slice(0, 300);
}

function formatSize(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
}

const MODEL_KEY = "trellin-import-model:";

function rememberedModel(project: string): string | null {
  try {
    return window.localStorage.getItem(MODEL_KEY + project);
  } catch {
    return null;
  }
}

/** #355: the model last used to import into this project reads the next file by default. */
function rememberModel(project: string, choice: string) {
  try {
    window.localStorage.setItem(MODEL_KEY + project, choice);
  } catch {
    // Private browsing or a full store keeps the default choice.
  }
}

/** A per-project switch kept across visits. */
function useStoredFlag(key: string) {
  const [local, setLocal] = useState(false);
  const stored = (() => {
    try {
      return window.localStorage.getItem(key) === "1";
    } catch {
      return null;
    }
  })();
  const value = stored ?? local;
  const change = (next: boolean) => {
    setLocal(next);
    try {
      window.localStorage.setItem(key, next ? "1" : "0");
    } catch {
      // Keep the in-memory value.
    }
  };
  return [value, change] as const;
}
