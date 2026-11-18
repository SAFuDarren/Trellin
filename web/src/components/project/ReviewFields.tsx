import { useTranslation } from "react-i18next";
import { savedReviewer } from "./review";

export function ReviewFields() {
  const { t } = useTranslation();
  return (
    <div className="project-entry-columns">
      <label>
        {t("review.reviewer")}
        <input name="actor" defaultValue={savedReviewer()} maxLength={120} required />
      </label>
      <label>
        {t("review.reason")}
        <input name="reason" maxLength={500} required />
      </label>
    </div>
  );
}
