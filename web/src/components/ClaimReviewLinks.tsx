import { useTranslation } from "react-i18next";
import type { InboxItem } from "../api/sections";
import { reviewItemHref, reviewLinkLabel, reviewOptions } from "../shell/reviewItems";
import { Menu } from "./FloatingControls";
import { Chevron } from "./Icons";
import "../styles/claim-progress.css";

export function ClaimReviewLinks({ items }: { items: readonly InboxItem[] }) {
  const { t, i18n } = useTranslation();
  const [first] = items;
  if (!first) return null;
  const label = reviewLinkLabel(t, items);
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
      groups={[{ options: reviewOptions(t, i18n.language, items) }]}
    />
  );
}
