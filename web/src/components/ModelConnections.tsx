import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import {
  API_MODEL_PROVIDERS,
  connectionLabel,
  connectionModelLabel,
  modelConnectionsKey,
  NATIVE_DEFAULT_MODEL,
  useModelConnections,
  type DiscoveredModelProvider,
  type ModelConnection,
  type NativeModelProvider,
} from "../api/modelConnections";

const LOGIN_COMMANDS: Record<NativeModelProvider, string> = {
  "codex-cli": "codex login",
  "claude-code": "claude auth login",
};

const CUSTOM_MODEL = "_custom";

function NativeModelChoice({
  discovery,
  list,
  disabled,
  connect,
}: {
  discovery: DiscoveredModelProvider;
  list: ModelConnection[];
  disabled: boolean;
  connect: (provider: NativeModelProvider, model: string) => void;
}) {
  const { t } = useTranslation();
  const [choice, setChoice] = useState(NATIVE_DEFAULT_MODEL);
  const [customModel, setCustomModel] = useState("");
  const { provider, status, models = [] } = discovery;
  const name = t(`team.providers.${provider}`);
  const signedIn = status === "signed_in";
  const model = choice === CUSTOM_MODEL ? customModel.trim() : choice;
  const added = list.some((item) => item.provider === provider && item.model === model);
  const message = signedIn
    ? t("modelConnections.signedIn")
    : status === "connection.authentication"
      ? t("modelConnections.signIn", { command: LOGIN_COMMANDS[provider] })
      : t(`errors.${status}`, { defaultValue: t("modelConnections.failed") });
  return (
    <li className="project-entry-item">
      <strong>{name}</strong>
      <p>{message}</p>
      {signedIn && (
        <>
          <label>
            {t("modelConnections.nativeModel", { name })}
            <select value={choice} onChange={(event) => setChoice(event.target.value)} disabled={disabled}>
              <option value={NATIVE_DEFAULT_MODEL}>{t("modelConnections.defaultModel")}</option>
              {models.map(({ id, label }) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
              {choice !== NATIVE_DEFAULT_MODEL &&
                choice !== CUSTOM_MODEL &&
                !models.some(({ id }) => id === choice) && <option value={choice}>{choice}</option>}
              <option value={CUSTOM_MODEL}>{t("modelConnections.customModel")}</option>
            </select>
          </label>
          {choice === CUSTOM_MODEL && (
            <label>
              {t("modelConnections.model")}
              <input
                value={customModel}
                onChange={(event) => setCustomModel(event.target.value)}
                maxLength={255}
                disabled={disabled}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
          )}
          {provider === "codex-cli" && !models.length && <p>{t("modelConnections.catalogUnavailable")}</p>}
          {provider === "claude-code" && <p>{t("modelConnections.aliasAvailability")}</p>}
        </>
      )}
      <div className="choices">
        <button
          type="button"
          className="button"
          disabled={disabled || !signedIn || !model || added}
          onClick={() => connect(provider, model)}
        >
          {t(added ? "modelConnections.nativeAdded" : "modelConnections.nativeConnect", { name })}
        </button>
      </div>
    </li>
  );
}

export function ModelConnections({ session }: { session: string }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const connections = useModelConnections(session);
  const [adding, setAdding] = useState<"native" | "api" | null>(null);
  const [pending, setPending] = useState(false);
  const [removing, setRemoving] = useState("");
  const [status, setStatus] = useState("");
  const [failure, setFailure] = useState("");
  const native = useQuery({
    queryKey: ["model-connections-native", session],
    queryFn: ({ signal }) =>
      api<{ providers: DiscoveredModelProvider[] }>(session, "/api/model-connections/native", { signal }),
    enabled: adding === "native",
    retry: false,
  });

  function errorMessage(code: string) {
    return t(`errors.${code}`, { defaultValue: t("modelConnections.failed") });
  }

  async function change(action: "add" | "add-native" | "remove" | "test", data: Record<string, string>) {
    setFailure("");
    setStatus("");
    setPending(true);
    try {
      const result = await api<{ status?: string; reported_model?: string | null }>(
        session,
        `/api/model-connections/${action}`,
        { data },
      );
      if (action === "test") {
        if (result.status === "connected") {
          const model = result.reported_model;
          setStatus(
            model
              ? t("modelConnections.testReported", { model })
              : t(model === null ? "modelConnections.testUnreported" : "modelConnections.status.connected"),
          );
        } else {
          setFailure(errorMessage(result.status ?? ""));
        }
      } else {
        setAdding(null);
        setRemoving("");
      }
      await queries.invalidateQueries({ queryKey: modelConnectionsKey(session) });
    } catch (error) {
      setFailure(errorMessage(error instanceof Error ? error.message : ""));
    } finally {
      setPending(false);
    }
  }

  function cancel() {
    setAdding(null);
    setFailure("");
  }

  function addApi(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const label = String(fields.get("label") ?? "").trim();
    const data = {
      provider: String(fields.get("provider")),
      model: String(fields.get("model")).trim(),
      api_key: String(fields.get("api_key") ?? ""),
      ...(label && { label }),
    };
    const key = form.elements.namedItem("api_key");
    if (key instanceof HTMLInputElement) key.value = "";
    void change("add", data);
  }

  const list = connections.data?.connections ?? [];
  const discovered = native.data?.providers ?? [];
  return (
    <section id="model-connections" className="team-connections" aria-labelledby="model-connections-title">
      <div className="team-heading">
        <h3 id="model-connections-title">{t("modelConnections.title")}</h3>
        {!adding && (
          <button className="button" type="button" disabled={pending} onClick={() => setAdding("native")}>
            {t("modelConnections.add")}
          </button>
        )}
      </div>
      {connections.isPending && <p role="status">{t("common.loading")}</p>}
      {connections.isError && <p role="alert">{t("modelConnections.failed")}</p>}
      {!connections.isPending && !connections.isError && !list.length && <p>{t("modelConnections.empty")}</p>}
      {list.length > 0 && (
        <div className="table-wrap">
          <table className="table team-table">
            <thead>
              <tr>
                <th scope="col">{t("team.name")}</th>
                <th scope="col">{t("modelConnections.model")}</th>
                <th scope="col">{t("team.connectionStatus")}</th>
                <th scope="col">{t("team.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {list.map((connection) => {
                const provider = t(`team.providers.${connection.provider}`, { defaultValue: connection.provider });
                const label = connectionLabel(connection);
                return (
                  <tr key={connection.id}>
                    <th scope="row">
                      {label}
                      {label !== provider && <span className="team-secondary">{provider}</span>}
                    </th>
                    <td>{connectionModelLabel(t, connection)}</td>
                    <td>
                      {t(`modelConnections.status.${connection.status}`, {
                        defaultValue: errorMessage(connection.status),
                      })}
                    </td>
                    <td>
                      <div className="choices team-model-actions">
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
                        <div className="choices team-model-actions">
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
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {adding === "native" && (
        <div className="project-entry-form">
          <h4>{t("modelConnections.nativeTitle")}</h4>
          {native.isFetching && <p role="status">{t("modelConnections.discovering")}</p>}
          {native.isError && <p role="alert">{t("modelConnections.discoveryFailed")}</p>}
          {!native.isFetching && !native.isError && !discovered.length && <p>{t("modelConnections.nativeEmpty")}</p>}
          {discovered.length > 0 && (
            <ul className="project-entry-items">
              {discovered.map((discovery) => (
                <NativeModelChoice
                  key={discovery.provider}
                  discovery={discovery}
                  list={list}
                  disabled={pending || native.isFetching}
                  connect={(provider, model) =>
                    void change("add-native", model === NATIVE_DEFAULT_MODEL ? { provider } : { provider, model })
                  }
                />
              ))}
            </ul>
          )}
          <div className="choices">
            <button
              type="button"
              className="button"
              disabled={pending || native.isFetching}
              onClick={() => void native.refetch()}
            >
              {t("modelConnections.refreshNative")}
            </button>
            <button type="button" className="button" disabled={pending} onClick={() => setAdding("api")}>
              {t("modelConnections.addApi")}
            </button>
            <button type="button" className="button" disabled={pending} onClick={cancel}>
              {t("modelConnections.cancel")}
            </button>
          </div>
        </div>
      )}
      {adding === "api" && (
        <form className="project-entry-form" onSubmit={addApi} autoComplete="off">
          <h4>{t("modelConnections.addApi")}</h4>
          <label>
            {t("modelConnections.provider")}
            <select name="provider" required defaultValue="" disabled={pending}>
              <option value="">{t("modelConnections.chooseProvider")}</option>
              {API_MODEL_PROVIDERS.map((provider) => (
                <option key={provider} value={provider}>
                  {t(`team.providers.${provider}`)}
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
          <div className="choices">
            <button className="button primary" type="submit" disabled={pending}>
              {t("modelConnections.save")}
            </button>
            <button className="button" type="button" disabled={pending} onClick={cancel}>
              {t("modelConnections.cancel")}
            </button>
          </div>
        </form>
      )}
      {status && <p role="status">{status}</p>}
      {pending && <p role="status">{t("common.loading")}</p>}
      {failure && <p role="alert">{failure}</p>}
    </section>
  );
}
