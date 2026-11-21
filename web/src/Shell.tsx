import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useAgentWork } from "./api/agentWork";
import { useConversations } from "./api/conversations";
import { LiveWorkspaceUpdates, WORKSPACE_WRITES } from "./api/liveUpdates";
import { useReleaseUpdates } from "./api/releaseUpdates";
import { useProjectCanvasClaims } from "./api/projectCanvas";
import { useProofTask } from "./api/proofTask";
import { useAtlas, useClaims, useInbox, useTeam } from "./api/sections";
import { workspaceEventCursor, workspaceId } from "./api/workspaceEvents";
import { AgentChatDock, type AgentChatRequest } from "./components/AgentChatDock";
import type { ConversationStarterScope } from "./components/ConversationRecipients";
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
import { buildWorkspaceActivity } from "./shell/workspaceActivity";
import { refreshWorkspaceQuery, workspaceEventQueries } from "./shell/workspaceEventQueries";
import { Workbench, type Focus, type WorkbenchPosition } from "./Workbench";
import { graphAction, type ProjectActionRequest } from "./workbench/actions";
import { buildClaimProgress } from "./workbench/claimProgress";
import { resetLayout } from "./workbench/useLayout";

/** The function rail on the left and the page it selects. */
export function Shell({ session }: { session: string }) {
  const { t } = useTranslation();
  const [route, go] = useRoute();
  const { section, sub } = route;
  const agentWork = sub === "agent-work" || sub.startsWith("agent-work/");
  const showsClaims = section === "claims" && !agentWork;
  const showsClaimProgress = section === "workbench" || showsClaims;
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
  const agentWorkList = useAgentWork(session, live);
  const conversations = useConversations(session, undefined, live);
  const activity = useMemo(
    () =>
      buildWorkspaceActivity({
        jobs: team.data?.jobs,
        tasks: agentWorkList.data?.tasks,
        threads: conversations.data?.threads,
        items: inbox.data?.items,
        followups: inbox.data?.clarification_review?.followups,
      }),
    [team.data, agentWorkList.data, conversations.data, inbox.data],
  );
  const atlas = useAtlas(session, showsClaims);
  const projectClaims = useProjectCanvasClaims(session, showsClaimProgress);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [chatRequest, setChatRequest] = useState<{ session: string; value: AgentChatRequest } | null>(null);
  const [chatCanvas, setChatCanvas] = useState<HTMLDivElement | null>(null);
  const [chatSelection, setChatSelection] = useState<{ session: string; scope: ConversationStarterScope } | null>(null);
  const saveChatStarterScope = useCallback(
    (scope: ConversationStarterScope) => setChatSelection({ session, scope }),
    [session],
  );
  const [palette, setPalette] = useState<{ session: string; query: string } | null>(null);
  const [projectAction, setProjectAction] = useState<{ session: string; request: ProjectActionRequest } | null>(null);
  const [lastWorkbench, setLastWorkbench] = useState<{ session: string; position: WorkbenchPosition } | null>(null);
  const position = lastWorkbench?.session === session ? lastWorkbench.position : undefined;
  // Only the selected claim needs the detailed proof-task read. The indexes cover the rest of the map.
  const proofTask = useProofTask(session, showsClaimProgress ? (position?.claimId ?? null) : null, live, 10000);
  const claimProgress = useMemo(
    () =>
      buildClaimProgress({
        claims: projectClaims.data?.claims,
        inbox: inbox.data?.items,
        jobs: team.data?.jobs,
        proofTask: proofTask.data,
      }),
    [projectClaims.data, inbox.data, team.data, proofTask.data],
  );
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
  const open = (claimId: string, nodeId?: string, proof?: boolean) => {
    setFocus({ claimId, nodeId, proof, nonce: Date.now() });
    go("workbench");
  };
  const runProjectAction = (request: ProjectActionRequest) => {
    setProjectAction({ session, request });
    go("workbench");
  };
  const claimMetadata = useClaims(session, paletteOpen);
  const claimId = position?.claimId;
  const paletteResults = usePaletteResults({
    session,
    workspaceId: workspace ?? inbox.data?.review_identity?.workspace_id,
    claimId,
    query: paletteOpen ? palette.query : "",
    enabled: paletteOpen,
    t,
    open,
    go,
  });
  const actionGroup = {
    label: t("palette.actions"),
    options: [
      graphAction(t, "add-theorem", () => runProjectAction({ id: "add-theorem" })),
      graphAction(t, "organize-research", () => runProjectAction({ id: "organize-research" })),
      ...(claimMetadata.data?.claim_codes
        ? [graphAction(t, "research-references", () => runProjectAction({ id: "research-references" }))]
        : []),
      ...(claimId
        ? [
            graphAction(t, "open-proof", () => open(claimId, undefined, true)),
            graphAction(t, "proof-task", () => runProjectAction({ id: "proof-task", claimId })),
          ]
        : []),
      graphAction(t, "start-chat", () => {
        if (section === "chat") go("chat", "new");
        else setChatRequest({ session, value: { newConversation: true } });
      }),
    ],
  };
  const pendingReviews = inbox.data ? activity.inboxCount : undefined;
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
            <Rail
              current={section}
              badges={{ team: { count: activity.counts.running, live: true } }}
              onSearch={openPalette}
            />
            {section === "review" ? (
              <ReviewPage session={session} sub={sub} onOpen={open} pendingReviews={pendingReviews} />
            ) : section === "claims" ? (
              agentWork ? (
                <AgentWorkPage session={session} sub={sub} />
              ) : (
                <ClaimsPage
                  session={session}
                  sub={sub}
                  onOpen={open}
                  claimProgress={claimProgress}
                  claimReviews={activity.reviewItemsByClaim}
                  atlasClaims={atlas.data?.claims}
                  running={activity.runningByClaim}
                />
              )
            ) : section === "library" ? (
              <LibraryPage session={session} sub={sub} onOpen={open} />
            ) : section === "chat" ? (
              <ConversationsPage
                session={session}
                sub={sub}
                onOpen={open}
                runningByProofNode={activity.runningByProofNode}
              />
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
                claimProgress={claimProgress}
                claimReviews={activity.reviewItemsByClaim}
                focus={focus}
                onFocusHandled={setFocus}
                pendingReviews={pendingReviews}
                returnTo={position}
                onPositionChange={saveWorkbench}
                onChatAnchor={(anchor, title, starterScope) =>
                  setChatRequest({ session, value: { anchor, title, starterScope } })
                }
                onChatCanvas={setChatCanvas}
                onChatStarterScope={saveChatStarterScope}
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
            canvas={section === "workbench" ? chatCanvas : null}
            starterScope={
              section === "workbench" && chatSelection?.session === session ? chatSelection.scope : undefined
            }
            activity={activity}
            activityLoading={team.isPending || agentWorkList.isPending || conversations.isPending || inbox.isPending}
            activityUnavailable={team.isError || agentWorkList.isError || conversations.isError || inbox.isError}
            conversations={conversations.data?.threads ?? []}
            conversationsLoading={conversations.isPending}
            onOpenProof={(id, nodeId) => open(id, nodeId, true)}
          />
          <CommandPalette
            open={paletteOpen}
            groups={
              paletteResults.scoped
                ? paletteResults.groups
                : paletteOpen && !palette.query.trim()
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
