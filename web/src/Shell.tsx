import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { LiveWorkspaceUpdates, WORKSPACE_WRITES } from "./api/liveUpdates";
import { useReleaseUpdates } from "./api/releaseUpdates";
import { useClaims, useInbox, useTeam } from "./api/sections";
import { workspaceEventCursor, workspaceId } from "./api/workspaceEvents";
import { AgentChatDock, type AgentChatRequest } from "./components/AgentChatDock";
import { CommandPalette } from "./components/CommandPalette";
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
import { usePaletteResults } from "./shell/paletteResults";
import { useRoute } from "./shell/route";
import { WorkspaceEventFeed, type WorkspaceConnection } from "./shell/WorkspaceEventFeed";
import { refreshWorkspaceQuery, workspaceEventQueries } from "./shell/workspaceEventQueries";
import { Workbench, type Focus, type WorkbenchPosition } from "./Workbench";
import { graphAction, type ProjectActionRequest } from "./workbench/actions";
import { resetLayout } from "./workbench/useLayout";

/** The function rail on the left and the page it selects. */
export function Shell({ session }: { session: string }) {
  const { t } = useTranslation();
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
  const [chatRequest, setChatRequest] = useState<{ session: string; value: AgentChatRequest } | null>(null);
  const [palette, setPalette] = useState<{ session: string; query: string } | null>(null);
  const [projectAction, setProjectAction] = useState<{ session: string; request: ProjectActionRequest } | null>(null);
  const [lastWorkbench, setLastWorkbench] = useState<{ session: string; position: WorkbenchPosition } | null>(null);
  const position = lastWorkbench?.session === session ? lastWorkbench.position : undefined;
  const paletteOpen = palette?.session === session;
  const openPalette = useCallback(() => setPalette({ session, query: "" }), [session]);
  const clearProjectAction = useCallback(() => setProjectAction(null), []);

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (
        event.key.toLowerCase() !== "k" ||
        (!event.metaKey && !event.ctrlKey) ||
        event.altKey ||
        event.shiftKey ||
        event.isComposing ||
        event.defaultPrevented
      )
        return;
      if (document.querySelector("dialog[open]") && !paletteOpen) return;
      event.preventDefault();
      if (!event.repeat && !paletteOpen) openPalette();
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [openPalette, paletteOpen]);
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
  const runProjectAction = (request: ProjectActionRequest) => {
    setProjectAction({ session, request });
    go("workbench");
  };
  const claimMetadata = useClaims(session, paletteOpen);
  const paletteResults = usePaletteResults({
    session,
    workspaceId: workspace ?? inbox.data?.review_identity?.workspace_id,
    query: paletteOpen ? palette.query : "",
    enabled: paletteOpen,
    t,
    open,
    go,
  });
  const claimId = position?.claimId;
  const actionGroup = {
    label: t("palette.actions"),
    options: [
      graphAction(t, "add-theorem", () => runProjectAction({ id: "add-theorem" })),
      graphAction(t, "organize-research", () => runProjectAction({ id: "organize-research" })),
      ...(claimMetadata.data?.claim_codes
        ? [graphAction(t, "research-references", () => runProjectAction({ id: "research-references" }))]
        : []),
      ...(claimId ? [graphAction(t, "proof-task", () => runProjectAction({ id: "proof-task", claimId }))] : []),
      {
        id: "start-chat",
        label: t("palette.startChat"),
        onSelect: () => setChatRequest({ session, value: { newConversation: true } }),
      },
    ],
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
            <Rail current={section} badges={{ team: { count: running, live: true } }} onSearch={openPalette} />
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
                returnTo={position}
                onPositionChange={saveWorkbench}
                onChatAnchor={(anchor, title) => setChatRequest({ session, value: { anchor, title } })}
                actionRequest={projectAction?.session === session ? projectAction.request : undefined}
                onActionHandled={clearProjectAction}
              />
            )}
            <ReleaseUpdateNotice updates={releaseUpdates} />
          </div>
          <AgentChatDock
            session={session}
            route={route}
            request={chatRequest?.session === session ? chatRequest.value : null}
          />
          <CommandPalette
            open={paletteOpen}
            groups={
              paletteOpen && !palette.query.trim()
                ? [...paletteResults.groups.slice(0, 1), actionGroup, ...paletteResults.groups.slice(1)]
                : [...paletteResults.groups, actionGroup]
            }
            loading={paletteResults.isLoading}
            onClose={() => setPalette(null)}
            onQueryChange={(query) =>
              setPalette((current) => (current?.session === session ? { session, query } : current))
            }
          />
        </div>
      </ConversationEditors>
    </LiveWorkspaceUpdates.Provider>
  );
}
