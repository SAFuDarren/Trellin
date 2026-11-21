import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Menu, type FloatingOption } from "./FloatingControls";

export function InspectorActions({
  primary,
  primaryOptions,
  secondary,
}: {
  primary: FloatingOption;
  primaryOptions?: FloatingOption[];
  secondary: FloatingOption[];
}) {
  const { t } = useTranslation();
  const row = useRef<HTMLDivElement>(null);
  const measure = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(secondary.length);

  useLayoutEffect(() => {
    const container = row.current;
    const sizing = measure.current;
    if (!container || !sizing) return;
    const update = () => {
      const available = container.clientWidth;
      if (!available) return;
      const gap = Number.parseFloat(getComputedStyle(container).columnGap) || 0;
      const widths = Array.from(sizing.children, (control) => control.getBoundingClientRect().width);
      const overflowWidth = widths.pop() ?? 0;
      let count = widths.length - 1;
      let required = widths.reduce((total, width) => total + width, 0) + count * gap;
      if (required > available) {
        required += overflowWidth + gap;
        while (count > 0 && required > available) required -= (widths[count--] ?? 0) + gap;
      }
      setVisible(count);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(container);
    observer.observe(sizing);
    return () => observer.disconnect();
  }, [secondary.length]);

  const menuLabel = t("review.moreInspectorActions");
  const more = (
    <svg aria-hidden="true" viewBox="0 0 16 16" width="16" height="16">
      <path d="M3 8h.01m5 0h.01m5 0h.01" strokeWidth="2.5" />
    </svg>
  );

  return (
    <div ref={row} className="inspector-actions">
      {primary.href ? (
        <a className="button primary" href={primary.href}>
          {primary.label}
        </a>
      ) : primaryOptions ? (
        <Menu
          label={primary.label}
          trigger={primary.label}
          triggerClassName="button primary"
          placement="bottom-start"
          groups={[{ options: primaryOptions }]}
        />
      ) : (
        <button type="button" className="button primary" onClick={primary.onSelect}>
          {primary.label}
        </button>
      )}
      {secondary.slice(0, visible).map((action) => (
        <button key={action.id} type="button" className="button" onClick={action.onSelect}>
          {action.label}
        </button>
      ))}
      {visible < secondary.length && (
        <Menu
          label={menuLabel}
          trigger={more}
          triggerClassName="button inspector-action-more"
          placement="bottom-end"
          groups={[{ options: secondary.slice(visible) }]}
        />
      )}
      <div className="inspector-actions-measure" aria-hidden="true">
        <div ref={measure}>
          <span className="button primary" data-label={primary.label} />
          {secondary.map((action) => (
            <span key={action.id} className="button" data-label={action.label} />
          ))}
          <span className="button inspector-action-more">{more}</span>
        </div>
      </div>
    </div>
  );
}
