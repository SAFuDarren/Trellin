import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LiveWorkspaceUpdates, WORKSPACE_WRITES } from "./api/liveUpdates";
import { useReleaseUpdates } from "./api/releaseUpdates";
import { useInbox, useTeam } from "./api/sections";
import { workspaceEventCursor, workspaceId } from "./api/workspaceEvents";
import { Rail } from "./components/Rail";
import { ReleaseUpdateNotice } from "./components/ReleaseUpdateNotice";
import { ClaimsPage } from "./pages/ClaimsPage";
import { ConversationsPage } from "./pages/ConversationsPage";
import { HelpPage } from "./pages/HelpPage";
import { LibraryPage } from "./pages/LibraryPage";
import { ProgressPage } from "./pages/ProgressPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TeamPage } from "./pages/TeamPage";
import { useRoute } from "./shell/route";
import { WorkspaceEventFeed, type WorkspaceConnection } from "./shell/WorkspaceEventFeed";
import { refreshWorkspaceQuery, workspaceEventQueries } from "./shell/workspaceEventQueries";
import { Workbench, type Focus } from "./Workbench";
import { resetLayout } from "./workbench/useLayout";

/** The function rail on the left and the page it selects. */
export function Shell({ session }: { session: string }) {
  const [route, go] = useRoute();
  const releaseUpdates = useReleaseUpdates(session);
  const queries = useQueryClient();
  const [connection, setConnection] = useState<WorkspaceConnection | null>(null);
  const sessionConnection = connection?.session === session ? connection : null;
  const inbox = useInbox(session, sessionConnection?.live);
  const eventFeed = inbox.data?.workspace_events;
  const workspace = eventFeed?.workspace_id;
  const head = eventFeed?.cursor;
  const comprehensive = eventFeed?.coverage === WORKSPACE_WRITES;
  const live = comprehensive && sessionConnection?.workspace === workspace && Boolean(sessionConnection?.live);
  const team = useTeam(session, live);
  const [focus, setFocus] = useState<Focus | null>(null);
  const previousHead = useRef<{ session: string; workspace: string; cursor: string } | null>(null);
  useEffect(() => {
    if (!comprehensive || !workspaceId(workspace) || !workspaceEventCursor(head)) {
      previousHead.current = null;
      return;
    }
    const previous = previousHead.current;
    previousHead.current = { session, workspace, cursor: head };
    // When disconnected, use the inbox's committed cursor to notice other changed views.
    if (
      !live &&
      previous?.session === session &&
      previous.workspace === workspace &&
      BigInt(head) > BigInt(previous.cursor) &&
      (sessionConnection?.workspace !== workspace || BigInt(head) > BigInt(sessionConnection.cursor))
    ) {
      for (const queryKey of workspaceEventQueries([{ kind: "change", area: "workspace" }], session)) {
        if (queryKey[0] !== "inbox") refreshWorkspaceQuery(queries, queryKey);
      }
    }
  }, [session, workspace, head, comprehensive, live, sessionConnection, queries]);
  const open = (claimId: string, nodeId?: string) => {
    setFocus({ claimId, nodeId, nonce: Date.now() });
    go("workbench");
  };
  const running = team.data?.jobs?.filter((job) => job.state === "running").length ?? 0;
  const badges = {
    review: { count: (inbox.data?.items?.length ?? 0) + (inbox.data?.clarification_review?.followups.length ?? 0) },
    team: { count: running, live: true },
  };
  const { section, sub } = route;
  return (
    <LiveWorkspaceUpdates.Provider value={live}>
      <div className="shell">
        {eventFeed && (
          <WorkspaceEventFeed
            key={`${session}:${eventFeed.workspace_id}:${comprehensive}`}
            session={session}
            workspace={eventFeed.workspace_id}
            bootstrapCursor={eventFeed.cursor}
            comprehensive={comprehensive}
            onConnection={setConnection}
          />
        )}
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
          <SettingsPage session={session} onResetLayout={resetLayout} updates={releaseUpdates} />
        ) : section === "help" ? (
          <HelpPage />
        ) : (
          <Workbench session={session} focus={focus} onFocusHandled={setFocus} />
        )}
        <ReleaseUpdateNotice updates={releaseUpdates} />
      </div>
    </LiveWorkspaceUpdates.Provider>
  );
}
