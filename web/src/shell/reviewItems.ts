import type { TFunction } from "i18next";
import type { InboxItem } from "../api/sections";
import { timeText } from "../workbench/text";
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

export function reviewResultName(item: InboxItem): string | undefined {
  if (item.kind !== "review_evidence") return;
  const title = item.detail?.title;
  if (typeof title === "string" && title.trim()) return title.trim();
  const declaration = item.detail?.declaration;
  if (typeof declaration === "string") return declaration.trim().split(".").pop() || undefined;
}

export function isReviewItem(item: Pick<InboxItem, "kind">) {
  return item.kind.startsWith("review_") || item.kind === "checker_hold";
}

export function reviewLinkLabel(t: TFunction, items: readonly InboxItem[]) {
  return t(
    items.every((item) => item.kind === "review_evidence")
      ? "claimProgress.resultsToReview"
      : "claimProgress.itemsToReview",
    { count: items.length },
  );
}

export function reviewOptions(t: TFunction, language: string, items: readonly InboxItem[]) {
  return items.map((item) => {
    const identity =
      typeof item.detail?.sha256 === "string" ? item.detail.sha256.slice(0, 12) : item.item_id.slice(0, 12);
    return {
      id: item.kind + ":" + item.item_id,
      label: reviewResultName(item) || reviewItemKindLabel(t, item),
      detail: [timeText(language, item.waiting_since, true), identity].filter(Boolean).join(" · "),
      href: reviewItemHref(item),
    };
  });
}
