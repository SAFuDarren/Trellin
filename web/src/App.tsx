import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ClaimsResponse } from "./api/client";
import { LANGUAGES, setLanguage, type Language } from "./i18n";
import "./App.css";

export function App({ session }: { session: string | null }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="shell">
      <header className="topbar">
        <h1>
          {t("app.name")}
          <span className="note">{t("app.preview")}</span>
        </h1>
        <label>
          <span className="sr-only">{t("app.language")}</span>
          <select
            aria-label={t("app.language")}
            value={i18n.language}
            onChange={(event) => setLanguage(event.target.value as Language)}
          >
            {Object.entries(LANGUAGES).map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </header>
      <main className="content">
        {session ? <Claims session={session} /> : <p className="message">{t("session.missing")}</p>}
      </main>
    </div>
  );
}

function Claims({ session }: { session: string }) {
  const { t } = useTranslation();
  const claims = useQuery({
    queryKey: ["claims"],
    queryFn: ({ signal }) => api<ClaimsResponse>(session, "/api/claims", { signal }),
  });
  return (
    <section aria-labelledby="claims-title">
      <h2 id="claims-title">{t("claims.title")}</h2>
      {claims.isPending && <p className="message">{t("claims.loading")}</p>}
      {claims.isError && (
        <p className="message" data-error="true" role="alert">
          {t("claims.error", { message: claims.error.message })}
        </p>
      )}
      {claims.isSuccess && claims.data.claims.length === 0 && <p className="message">{t("claims.empty")}</p>}
      {claims.isSuccess && claims.data.claims.length > 0 && (
        <ul className="claims">
          {claims.data.claims.map((claim) => (
            <li key={claim.id}>
              <span>{claim.title || t("claims.untitled")}</span>
              <span className="meta">
                {[claim.domain, t(`status.${claim.status}`, { defaultValue: claim.status })]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
