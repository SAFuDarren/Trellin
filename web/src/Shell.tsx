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
import { useTeamAgents } from "./api/teamAgents";
import { workspaceEventCursor, workspaceId } from "./api/workspaceEvents";
import { AgentChatDock, type AgentChatRequest } from "./components/AgentChatDock";
import type { ConversationStarterScope } from "./components/ConversationRecipients";
import { CommandPalette } from "./components/CommandPalette";
import { Rail } from "./components/Rail";
import { SpaceCounts } from "./components/SectionLayout";
import { WorkPeek } from "./components/WorkPeek";
import { ReleaseUpdateNotice } from "./components/ReleaseUpdateNotice";
import { ShortcutSheet } from "./components/ShortcutSheet";
import { WorkspaceActivityList } from "./components/WorkspaceActivity";
import { WorkspaceInbox } from "./components/WorkspaceInbox";
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
import { href, useRoute, workbenchSub } from "./shell/route";
import { spaceOf } from "./shell/spaces";
import {
  usePinnedClaims,
  waitingItems,
  WorkContext,
  type PeekTarget,
  type WorkContextValue,
} from "./shell/workContext";
import { CLAIM_VIEWS, inClaimView } from "./workbench/claimBoard";
import { claimCode } from "./workbench/text";
import { WorkspaceEventFeed, type WorkspaceConnection } from "./shell/WorkspaceEventFeed";
import { buildWorkspaceActivity, type ActivityState } from "./shell/workspaceActivity";
import { useWorkspaceInbox, workspaceInboxScope } from "./shell/workspaceInbox";
import { refreshWorkspaceQuery, workspaceEventQueries } from "./shell/workspaceEventQueries";
import { SHORTCUTS } from "./shortcuts";
import { Workbench, type Focus, type WorkbenchPosition } from "./Workbench";
import { graphAction, type ProjectActionRequest } from "./workbench/actions";
import { buildClaimProgress } from "./workbench/claimProgress";
import { resetLayout } from "./workbench/useLayout";

/** The function rail on the left and the page it selects. */
export function Shell({ session }: { session: string }) {
  const { t } = useTranslation();
  const [route, go, setWorkbenchAddress] = useRoute();
  const { section, sub } = route;
  const showsClaims = section === "claims";
  const showsActivity = section === "agent-work" && sub === "activity";
  const showsInbox = section === "agent-work" && sub === "inbox";
  // #326: every Work page shows the Work panel, which counts claims and lists what waits for you.
  const inWork = spaceOf(section)?.id === "work";
  const showsClaimProgress = section === "workbench" || showsClaims || inWork;
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
  const activityAgents = useTeamAgents(session, showsActivity || showsInbox || inWork);
  const [activityFilter, setActivityFilter] = useState<ActivityState | "all">("all");
  const activityLoading = team.isPending || agentWorkList.isPending || conversations.isPending || inbox.isPending;
  const activityUnavailable = team.isError || agentWorkList.isError || conversations.isError || inbox.isError;
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
  const inboxScope = workspaceInboxScope(session, workspace, inbox.data?.review_identity?.workspace_id);
  const workspaceInbox = useWorkspaceInbox(inboxScope, activity.rows);
  const atlas = useAtlas(session, showsClaims || inWork);
  const workClaims = useClaims(session, inWork);
  const [pinnedIds, togglePin] = usePinnedClaims();
  const [peekState, setPeekState] = useState<{ session: string; target: PeekTarget } | null>(null);
  const projectClaims = useProjectCanvasClaims(session, showsClaimProgress);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [chatRequest, setChatRequest] = useState<{ session: string; value: AgentChatRequest } | null>(null);
  const [chatSelection, setChatSelection] = useState<{ session: string; scope: ConversationStarterScope } | null>(null);
  const saveChatStarterScope = useCallback(
    (scope: ConversationStarterScope) => setChatSelection({ session, scope }),
    [session],
  );
  const [palette, setPalette] = useState<{ session: string; query: string } | null>(null);
  const [shortcutSheetOpen, setShortcutSheetOpen] = useState(false);
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
      if (event.isComposing || event.defaultPrevented) return;
      if (event.key === SHORTCUTS.sheet) {
        if (
          event.repeat ||
          event.altKey ||
          event.ctrlKey ||
          event.metaKey ||
          document.querySelector("dialog[open]") ||
          (event.target instanceof HTMLElement &&
            event.target.closest(
              "input, textarea, select, [contenteditable]:not([contenteditable=false]), [role=textbox]",
            ))
        )
          return;
        event.preventDefault();
        setShortcutSheetOpen(true);
        return;
      }
      if (
        event.key.toUpperCase() !== SHORTCUTS.paletteKey ||
        (!event.metaKey && !event.ctrlKey) ||
        event.altKey ||
        event.shiftKey
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
    // #274: a proof opens at its own address, so Back returns to the page it was opened from.
    go("workbench", proof || nodeId ? workbenchSub({ claimId, nodeId }) : "");
  };
  const runProjectAction = (request: ProjectActionRequest) => {
    setProjectAction({ session, request });
    // Stay at the current workbench address: an action does not close the open proof.
    if (section !== "workbench") go("workbench");
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
  // #326: the Work panel and its side panel read one snapshot built here.
  const workAgents = new Map(activityAgents.data?.agents?.map((agent) => [agent.id, agent]));
  const archived = new Set(
    workspaceInbox.rows.filter(({ mark }) => mark === "archived").map(({ record }) => record.id),
  );
  const workClaimList = workClaims.data?.claims ?? [];
  const atlasById = new Map((atlas.data?.claims ?? []).map((claim) => [claim.id, claim]));
  const blockedByClaim = new Map([...atlasById].map(([id, claim]) => [id, claim.blocked ?? 0]));
  const peek = inWork && peekState?.session === session ? peekState.target : null;
  const work: WorkContextValue = {
    agents: workAgents,
    waiting: waitingItems(activity.rows, archived),
    views: CLAIM_VIEWS.map((key) => ({
      key,
      href: href("claims", key === "all" ? "" : key),
      count: workClaimList.filter((claim) =>
        inClaimView(key, claim.id, activity.reviewItemsByClaim, blockedByClaim, activity.runningByClaim),
      ).length,
    })),
    pinned: pinnedIds.flatMap((id) => {
      const claim = workClaimList.find((entry) => entry.id === id);
      return claim ? [{ id, title: claim.title, code: claimCode(claim) }] : [];
    }),
    isPinned: (id) => pinnedIds.includes(id),
    togglePin,
    peek,
    setPeek: (target) => setPeekState(target ? { session, target } : null),
    renderPeek: () => (
      <WorkPeek
        key={peek?.kind === "claim" ? "claims" : "waiting"}
        session={session}
        claims={workClaimList}
        atlas={atlasById}
        progress={claimProgress}
        reviews={activity.reviewItemsByClaim}
        running={activity.runningByClaim}
        tasks={agentWorkList.data?.tasks ?? []}
        reviewWorkspace={inbox.data?.review_identity?.workspace_id}
        signer={inbox.data?.review_identity?.display_name || undefined}
        clarificationWorkspace={inbox.data?.clarification_review?.workspace_id}
        onOpen={(claimId, nodeId) => open(claimId, nodeId, true)}
      />
    ),
  };

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
              badges={{
                team: { count: activity.counts.running, live: true },
                // The inbox already holds the review items (#270), so the larger count is the number waiting.
                work: { count: Math.max(pendingReviews ?? 0, workspaceInbox.unread) },
              }}
              onSearch={openPalette}
            />
            <WorkContext.Provider value={work}>
              <SpaceCounts.Provider value={{ inbox: workspaceInbox.unread, review: pendingReviews }}>
                {section === "review" ? (
                  <ReviewPage session={session} sub={sub} onOpen={open} />
                ) : section === "agent-work" ? (
                  <AgentWorkPage
                    session={session}
                    sub={sub}
                    inbox={
                      showsInbox && (
                        <WorkspaceInbox
                          key={inboxScope ?? session}
                          inbox={workspaceInbox}
                          agents={new Map(activityAgents.data?.agents.map((agent) => [agent.id, agent]))}
                          loading={inbox.isPending || team.isPending || agentWorkList.isPending}
                          unavailable={inbox.isError || team.isError || agentWorkList.isError}
                          onOpenProof={(id, nodeId) => open(id, nodeId, true)}
                        />
                      )
                    }
                    activity={
                      showsActivity && (
                        <WorkspaceActivityList
                          activity={activity}
                          agents={new Map(activityAgents.data?.agents.map((agent) => [agent.id, agent]))}
                          selectedFilter={activityFilter}
                          onFilter={setActivityFilter}
                          loading={activityLoading}
                          unavailable={activityUnavailable}
                          onOpenProof={(id, nodeId) => open(id, nodeId, true)}
                          onOpenThread={(thread) => go("chat", thread.id)}
                        />
                      )
                    }
                  />
                ) : section === "claims" ? (
                  <ClaimsPage
                    session={session}
                    sub={sub}
                    onOpen={open}
                    claimProgress={claimProgress}
                    claimReviews={activity.reviewItemsByClaim}
                    atlasClaims={atlas.data?.claims}
                    running={activity.runningByClaim}
                    onNewClaim={() => runProjectAction({ id: "add-theorem" })}
                  />
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
                  <SettingsPage
                    sub={sub}
                    onResetLayout={resetLayout}
                    onShowShortcuts={() => setShortcutSheetOpen(true)}
                    updates={releaseUpdates}
                  />
                ) : section === "help" ? (
                  <HelpPage sub={sub} onShowShortcuts={() => setShortcutSheetOpen(true)} />
                ) : (
                  <Workbench
                    key={session}
                    session={session}
                    claimProgress={claimProgress}
                    claimReviews={activity.reviewItemsByClaim}
                    focus={focus}
                    onFocusHandled={setFocus}
                    returnTo={position}
                    onPositionChange={saveWorkbench}
                    onChatAnchor={(anchor, title, starterScope) =>
                      setChatRequest({ session, value: { anchor, title, starterScope } })
                    }
                    onChatStarterScope={saveChatStarterScope}
                    actionRequest={projectAction?.session === session ? projectAction.request : undefined}
                    onActionHandled={clearProjectAction}
                    address={sub}
                    onAddress={setWorkbenchAddress}
                  />
                )}
              </SpaceCounts.Provider>
            </WorkContext.Provider>
            <ReleaseUpdateNotice updates={releaseUpdates} />
          </div>
          <AgentChatDock
            session={session}
            route={route}
            request={chatRequest?.session === session ? chatRequest.value : null}
            starterScope={
              section === "workbench" && chatSelection?.session === session ? chatSelection.scope : undefined
            }
            activity={activity}
            activityLoading={activityLoading}
            activityUnavailable={activityUnavailable}
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
          <ShortcutSheet open={shortcutSheetOpen} onClose={() => setShortcutSheetOpen(false)} />
        </div>
      </ConversationEditors>
    </LiveWorkspaceUpdates.Provider>
  );
}
