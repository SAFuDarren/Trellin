import { useTranslation } from "react-i18next";
import { savedReviewer } from "./review";

export function ReviewSigner({ name }: { name: string }) {
  const { t } = useTranslation();
  return <p className="review-signer">{t("review.signedAs", { name })}</p>;
}

/** The signer and reason for a confirmation. A known local researcher signs without typing a name (#328). */
export function ReviewFields({ signer }: { signer?: string }) {
  const { t } = useTranslation();
  return (
    <div className="project-entry-columns">
      {signer ? (
        <div>
          <input type="hidden" name="actor" value={signer} />
          <ReviewSigner name={signer} />
        </div>
      ) : (
        <label>
          {t("review.reviewer")}
          <input name="actor" defaultValue={savedReviewer()} maxLength={120} required />
        </label>
      )}
      <label>
        {t("review.reason")}
        <input name="reason" maxLength={500} required />
      </label>
    </div>
  );
}
