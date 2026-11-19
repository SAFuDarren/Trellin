import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { connectionLabel, MODEL_PROVIDERS, modelConnectionsKey, useModelConnections } from "../api/modelConnections";

export function ModelConnections({ session }: { session: string }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const connections = useModelConnections(session);
  const [adding, setAdding] = useState(false);
  const [provider, setProvider] = useState("");
  const [pending, setPending] = useState(false);
  const [removing, setRemoving] = useState("");
  const [status, setStatus] = useState("");
  const [failure, setFailure] = useState("");
  const cli = provider === "codex-cli" || provider === "claude-code";

  async function change(action: "add" | "remove" | "test", data: Record<string, string>) {
    setFailure("");
    setStatus("");
    setPending(true);
    try {
      const result = await api<{ status?: string }>(session, `/api/model-connections/${action}`, { data });
      if (action === "test") {
        if (result.status === "connected") {
          setStatus(t("modelConnections.status.connected"));
        } else {
          setFailure(t(`errors.${result.status}`, { defaultValue: t("modelConnections.failed") }));
        }
        await queries.invalidateQueries({ queryKey: modelConnectionsKey(session) });
      } else {
        setAdding(false);
        setRemoving("");
        setProvider("");
        await queries.invalidateQueries({ queryKey: modelConnectionsKey(session) });
      }
    } catch (error) {
      const code = error instanceof Error ? error.message : "";
      setFailure(t(`errors.${code}`, { defaultValue: t("modelConnections.failed") }));
    } finally {
      setPending(false);
    }
  }

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const data: Record<string, string> = { provider, model: String(fields.get("model")).trim() };
    const label = String(fields.get("label") ?? "").trim();
    if (label) data.label = label;
    const binary = String(fields.get("binary") ?? "").trim();
    if (binary) data.binary = binary;
    if (!cli) {
      data.api_key = String(fields.get("api_key") ?? "");
      const key = form.elements.namedItem("api_key");
      if (key instanceof HTMLInputElement) key.value = "";
    }
    void change("add", data);
  }

  const list = connections.data?.connections ?? [];
  return (
    <section id="model-connections" aria-labelledby="model-connections-title">
      <h3 id="model-connections-title">{t("modelConnections.title")}</h3>
      {connections.isPending && <p role="status">{t("common.loading")}</p>}
      {connections.isError && <p role="alert">{t("modelConnections.failed")}</p>}
      {!connections.isPending && !connections.isError && !list.length && <p>{t("modelConnections.empty")}</p>}
      {list.length > 0 && (
        <ul className="project-entry-items">
          {list.map((connection) => (
            <li className="project-entry-item" key={connection.id}>
              <strong>{connectionLabel(connection)}</strong>
              <p>
                {t(`team.providers.${connection.provider}`, { defaultValue: connection.provider })}
                {" · "}
                {connection.model}
                {" · "}
                {t(`modelConnections.status.${connection.status}`, {
                  defaultValue: t(`errors.${connection.status}`, { defaultValue: t("modelConnections.failed") }),
                })}
              </p>
              <div className="choices">
                <button
                  type="button"
                  className="button"
                  disabled={pending}
                  onClick={() => void change("test", { id: connection.id })}
                >
                  {t("modelConnections.test")}
                </button>
                <button
                  type="button"
                  className="button"
                  disabled={pending}
                  onClick={() => {
                    setRemoving(connection.id);
                    setStatus("");
                    setFailure("");
                  }}
                >
                  {t("modelConnections.remove")}
                </button>
              </div>
              {removing === connection.id && (
                <div className="choices">
                  <p>{t("modelConnections.removeHint")}</p>
                  <button
                    type="button"
                    className="button"
                    disabled={pending}
                    onClick={() => void change("remove", { id: connection.id })}
                  >
                    {t("modelConnections.removeConfirm")}
                  </button>
                  <button type="button" className="button" disabled={pending} onClick={() => setRemoving("")}>
                    {t("modelConnections.cancel")}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form className="project-entry-form" onSubmit={add} autoComplete="off">
          <label>
            {t("modelConnections.provider")}
            <select required value={provider} disabled={pending} onChange={(event) => setProvider(event.target.value)}>
              <option value="">{t("modelConnections.chooseProvider")}</option>
              {MODEL_PROVIDERS.map((name) => (
                <option key={name} value={name}>
                  {t(`team.providers.${name}`, { defaultValue: name })}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("modelConnections.model")}
            <input name="model" required maxLength={160} disabled={pending} />
          </label>
          <label>
            {t("modelConnections.label")}
            <input name="label" maxLength={100} disabled={pending} />
          </label>
          {provider &&
            (cli ? (
              <>
                <p className="project-entry-note">{t("modelConnections.nativeLogin")}</p>
                <label>
                  {t("modelConnections.binary")}
                  <input name="binary" maxLength={1000} disabled={pending} />
                </label>
              </>
            ) : (
              <label>
                {t("modelConnections.apiKey")}
                <input
                  name="api_key"
                  type="password"
                  autoComplete="new-password"
                  required
                  maxLength={4096}
                  disabled={pending}
                />
              </label>
            ))}
          <div className="choices">
            <button className="button primary" type="submit" disabled={!provider || pending}>
              {t("modelConnections.save")}
            </button>
            <button
              className="button"
              type="button"
              disabled={pending}
              onClick={() => {
                setAdding(false);
                setProvider("");
                setFailure("");
              }}
            >
              {t("modelConnections.cancel")}
            </button>
          </div>
        </form>
      ) : (
        <div className="choices">
          <button className="button" type="button" disabled={pending} onClick={() => setAdding(true)}>
            {t("modelConnections.add")}
          </button>
        </div>
      )}
      {status && <p role="status">{status}</p>}
      {pending && <p role="status">{t("common.loading")}</p>}
      {failure && <p role="alert">{failure}</p>}
    </section>
  );
}
