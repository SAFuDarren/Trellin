import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LiveWorkspaceUpdates, WORKSPACE_WRITES } from "./api/liveUpdates";
import { useReleaseUpdates } from "./api/releaseUpdates";
import { useInbox, useTeam } from "./api/sections";
import { workspaceEventCursor, workspaceId } from "./api/workspaceEvents";
import { AgentChatDock, type AgentChatAnchor } from "./components/AgentChatDock";
import { Rail } from "./components/Rail";
import { ReleaseUpdateNotice } from "./components/ReleaseUpdateNotice";
import { AgentWorkPage } from "./pages/AgentWorkPage";
import { ClaimsPage } from "./pages/ClaimsPage";
import { ConversationsPage } from "./pages/ConversationsPage";
import { HelpPage } from "./pages/HelpPage";
import { LibraryPage } from "./pages/LibraryPage";
import { ProgressPage } from "./pages/ProgressPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TeamPage } from "./pages/TeamPage";
import { ConversationEditors } from "./shell/ConversationEditors";
import { useRoute } from "./shell/route";
import { WorkspaceEventFeed, type WorkspaceConnection } from "./shell/WorkspaceEventFeed";
import { refreshWorkspaceQuery, workspaceEventQueries } from "./shell/workspaceEventQueries";
import { Workbench, type Focus, type WorkbenchPosition } from "./Workbench";
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
  const [chatAnchor, setChatAnchor] = useState<{ session: string; value: AgentChatAnchor } | null>(null);
  const [lastWorkbench, setLastWorkbench] = useState<{ session: string; position: WorkbenchPosition } | null>(null);
  const saveWorkbench = useCallback(
    (position: WorkbenchPosition) => {
      setLastWorkbench({ session, position });
    },
    [session],
  );
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
  const pendingReviews = inbox.data
    ? (inbox.data.items?.length ?? 0) + (inbox.data.clarification_review?.followups.length ?? 0)
    : undefined;
  const { section, sub } = route;
  return (
    <LiveWorkspaceUpdates.Provider value={live}>
      <ConversationEditors key={session} session={session}>
        <div className="workbench-frame">
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
            <Rail current={section} badges={{ team: { count: running, live: true } }} />
            {section === "review" ? (
              <ReviewPage session={session} sub={sub} onOpen={open} pendingReviews={pendingReviews} />
            ) : section === "claims" ? (
              sub === "agent-work" || sub.startsWith("agent-work/") ? (
                <AgentWorkPage session={session} sub={sub} />
              ) : (
                <ClaimsPage session={session} sub={sub} onOpen={open} />
              )
            ) : section === "library" ? (
              <LibraryPage session={session} sub={sub} onOpen={open} />
            ) : section === "chat" ? (
              <ConversationsPage session={session} sub={sub} onOpen={open} />
            ) : section === "team" ? (
              <TeamPage session={session} sub={sub} onOpen={open} />
            ) : section === "progress" ? (
              <ProgressPage session={session} sub={sub} onOpen={open} />
            ) : section === "settings" ? (
              <SettingsPage onResetLayout={resetLayout} updates={releaseUpdates} />
            ) : section === "help" ? (
              <HelpPage />
            ) : (
              <Workbench
                key={session}
                session={session}
                focus={focus}
                onFocusHandled={setFocus}
                pendingReviews={pendingReviews}
                returnTo={lastWorkbench?.session === session ? lastWorkbench.position : undefined}
                onPositionChange={saveWorkbench}
                onChatAnchor={(anchor, title) => setChatAnchor({ session, value: { anchor, title } })}
              />
            )}
            <ReleaseUpdateNotice updates={releaseUpdates} />
          </div>
          <AgentChatDock
            session={session}
            route={route}
            anchorRequest={chatAnchor?.session === session ? chatAnchor.value : null}
          />
        </div>
      </ConversationEditors>
    </LiveWorkspaceUpdates.Provider>
  );
}
