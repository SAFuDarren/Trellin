import { useTranslation } from "react-i18next";

export function CanvasZoom({
  scale,
  onZoom,
  onFit,
}: {
  scale: number;
  onZoom: (factor: number) => void;
  onFit: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="zoom" role="group" aria-label={t("stage.zoom")}>
      <button type="button" aria-label={t("stage.zoomOut")} onClick={() => onZoom(1 / 1.2)}>
        <svg viewBox="0 0 14 14" aria-hidden="true">
          <path d="M3 7h8" />
        </svg>
      </button>
      <button type="button" className="zoom-level" aria-label={t("stage.zoomReset")} onClick={() => onZoom(1 / scale)}>
        {Math.round(scale * 100)}%
      </button>
      <button type="button" aria-label={t("stage.zoomIn")} onClick={() => onZoom(1.2)}>
        <svg viewBox="0 0 14 14" aria-hidden="true">
          <path d="M3 7h8M7 3v8" />
        </svg>
      </button>
      <span className="zoom-rule" aria-hidden="true" />
      <button type="button" aria-label={t("stage.fit")} onClick={onFit}>
        <svg viewBox="0 0 14 14" aria-hidden="true">
          <path d="M2 5V2h3M9 2h3v3M12 9v3H9M5 12H2V9" />
        </svg>
      </button>
    </div>
  );
}
