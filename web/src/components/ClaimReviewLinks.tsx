import { useTranslation } from "react-i18next";
import type { InboxItem } from "../api/sections";
import { reviewItemHref, reviewItemKindLabel } from "../shell/reviewItems";
import { timeText } from "../workbench/text";
import { Menu } from "./FloatingControls";
import { Chevron } from "./Icons";
import "../styles/claim-progress.css";

export function ClaimReviewLinks({ items }: { items: readonly InboxItem[] }) {
  const { t, i18n } = useTranslation();
  const [first] = items;
  if (!first) return null;
  const label = t(
    items.every((item) => item.kind === "review_evidence")
      ? "claimProgress.resultsToReview"
      : "claimProgress.itemsToReview",
    { count: items.length },
  );
  const className = "tag claim-review-link";

  if (items.length === 1) {
    return (
      <a className={className} data-tone="review" href={reviewItemHref(first)}>
        {label}
      </a>
    );
  }

  return (
    <Menu
      label={label}
      triggerClassName={className}
      placement="bottom-start"
      trigger={
        <>
          {label}
          <Chevron />
        </>
      }
      groups={[
        {
          options: items.map((item) => {
            const name = [item.detail?.title, item.detail?.declaration].find(
              (value): value is string => typeof value === "string" && Boolean(value.trim()),
            );
            const identity =
              typeof item.detail?.sha256 === "string" ? item.detail.sha256.slice(0, 12) : item.item_id.slice(0, 12);
            return {
              id: item.kind + ":" + item.item_id,
              label: name || reviewItemKindLabel(t, item),
              detail: [timeText(i18n.language, item.waiting_since, true), identity].filter(Boolean).join(" · "),
              href: reviewItemHref(item),
            };
          }),
        },
      ]}
    />
  );
}
