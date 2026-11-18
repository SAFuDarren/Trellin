import { useTranslation } from "react-i18next";
import { Shell } from "./Shell";

export function App({ session }: { session: string | null }) {
  const { t } = useTranslation();
  if (!session) {
    return (
      <p className="notice" data-error="true" role="alert">
        {t("session.missing")}
      </p>
    );
  }
  return <Shell session={session} />;
}
