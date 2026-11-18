import { useState } from "react";
import { useInbox, useTeam } from "./api/sections";
import { Rail } from "./components/Rail";
import { ClaimsPage } from "./pages/ClaimsPage";
import { ConversationsPage } from "./pages/ConversationsPage";
import { HelpPage } from "./pages/HelpPage";
import { LibraryPage } from "./pages/LibraryPage";
import { ProgressPage } from "./pages/ProgressPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TeamPage } from "./pages/TeamPage";
import { useRoute } from "./shell/route";
import { Workbench, type Focus } from "./Workbench";
import { resetLayout } from "./workbench/useLayout";

/** The function rail on the left and the page it selects. */
export function Shell({ session }: { session: string }) {
  const [route, go] = useRoute();
  const inbox = useInbox(session);
  const team = useTeam(session);
  const [focus, setFocus] = useState<Focus | null>(null);
  const open = (claimId: string, nodeId?: string) => {
    setFocus({ claimId, nodeId, nonce: Date.now() });
    go("workbench");
  };
  const running = team.data?.jobs?.filter((job) => job.state === "running").length ?? 0;
  const badges = {
    review: { count: inbox.data?.items?.length ?? 0 },
    team: { count: running, live: true },
  };
  const { section, sub } = route;
  return (
    <div className="shell">
      <Rail current={section} badges={badges} />
      {section === "review" ? (
        <ReviewPage session={session} sub={sub} onOpen={open} />
      ) : section === "claims" ? (
        <ClaimsPage session={session} sub={sub} onOpen={open} />
      ) : section === "library" ? (
        <LibraryPage session={session} sub={sub} onOpen={open} />
      ) : section === "chat" ? (
        <ConversationsPage session={session} sub={sub} onOpen={open} />
      ) : section === "team" ? (
        <TeamPage session={session} sub={sub} onOpen={open} />
      ) : section === "progress" ? (
        <ProgressPage session={session} sub={sub} onOpen={open} />
      ) : section === "settings" ? (
        <SettingsPage onResetLayout={resetLayout} />
      ) : section === "help" ? (
        <HelpPage />
      ) : (
        <Workbench session={session} focus={focus} onFocusHandled={setFocus} />
      )}
    </div>
  );
}
