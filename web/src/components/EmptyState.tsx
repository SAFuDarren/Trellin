import type { ReactNode } from "react";

export type EmptyStateAction = { label: string; href: string } | { label: string; onClick: () => void };

/**
 * #328: an empty or unavailable page says what is missing, why, and offers one next step.
 * The title is read out as a status; the reason and action follow it.
 */
export function EmptyState({
  title,
  reason,
  action,
}: {
  title: ReactNode;
  reason?: ReactNode;
  action?: EmptyStateAction;
}) {
  return (
    <div className="empty-state">
      <p className="empty-state-title" role="status">
        {title}
      </p>
      {reason && <p className="empty-state-reason">{reason}</p>}
      {action &&
        ("href" in action ? (
          <a className="button" href={action.href}>
            {action.label}
          </a>
        ) : (
          <button type="button" className="button" onClick={action.onClick}>
            {action.label}
          </button>
        ))}
    </div>
  );
}
