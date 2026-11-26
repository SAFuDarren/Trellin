import { useId, useRef } from "react";
import { useTranslation } from "react-i18next";

/**
 * #353: an empty project map's starting point. Researchers drop or choose the notes and
 * Lean code they already have, and the import reads them; a theorem can still be typed in.
 */
export function MapDrop({ onFile, onNewTheorem }: { onFile: (file: File) => void; onNewTheorem: () => void }) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const title = useId();
  return (
    <section className="map-drop" aria-labelledby={title}>
      <svg className="map-drop-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 18.5h-.5A4.5 4.5 0 0 1 6 9.6a6 6 0 0 1 11.6 1.4A3.75 3.75 0 0 1 17.25 18.5H17" />
        <path d="M12 12v8M8.8 15.2 12 12l3.2 3.2" />
      </svg>
      <h2 id={title} className="map-drop-title">
        {t("mapDrop.title")}
      </h2>
      <p className="map-drop-hint">{t("mapDrop.hint")}</p>
      <div className="map-drop-actions">
        <button type="button" className="button" onClick={() => input.current?.click()}>
          {t("mapDrop.choose")}
        </button>
        <button type="button" className="conversation-text-action" onClick={onNewTheorem}>
          {t("mapDrop.newTheorem")}
        </button>
      </div>
      <input
        ref={input}
        className="map-drop-input"
        type="file"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (file) onFile(file);
        }}
      />
    </section>
  );
}

/** Whether a drag carries files from the system, as opposed to a card or text. */
export function draggingFiles(event: { dataTransfer: DataTransfer | null }) {
  return Boolean(event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files"));
}
