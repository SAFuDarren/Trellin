import { useTranslation } from "react-i18next";
import { href } from "../shell/route";
import { NavigationMenu } from "./NavigationMenu";

export function WorkbenchViewMenu({
  current,
  pendingReviews,
}: {
  current: "workbench" | "review";
  pendingReviews: number | undefined;
}) {
  const { t } = useTranslation();
  const count = pendingReviews ?? 0;
  return (
    <NavigationMenu
      className="icon-button workbench-view-trigger"
      label={t("navigation.switchView")}
      indicator={current === "review"}
      items={[
        { href: href("workbench"), label: t("rail.workbench"), current: current === "workbench" },
        { href: href("review"), label: t("rail.review"), current: current === "review", badge: pendingReviews },
      ]}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 8h13m-3-3 3 3-3 3M18 16H5m3-3-3 3 3 3" />
      </svg>
      {count > 0 && (
        <span className="workbench-view-badge" aria-hidden="true">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </NavigationMenu>
  );
}
