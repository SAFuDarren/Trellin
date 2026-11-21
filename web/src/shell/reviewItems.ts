import type { TFunction } from "i18next";
import type { InboxItem } from "../api/sections";
import { href } from "./route";

export const GOAL_REVIEW_ROUTE = "goal:";

export function reviewItemHref(item: Pick<InboxItem, "item_id" | "kind">) {
  return href("review", (item.kind === "choose_conjecture" ? GOAL_REVIEW_ROUTE : "") + item.item_id);
}

export function reviewItemKindLabel(t: TFunction, item: InboxItem): string {
  if (item.kind === "review_evidence" && item.detail?.kind === "counterexample")
    return t("review.kinds.counterexample");
  return t(`review.kinds.${item.kind}`, { defaultValue: item.kind.replace(/_/g, " ") });
}

export function isReviewItem(item: Pick<InboxItem, "kind">) {
  return item.kind.startsWith("review_") || item.kind === "checker_hold";
}
