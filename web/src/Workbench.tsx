import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api, type ClaimsResponse } from "./api/client";
import { useFallbackInterval } from "./api/liveUpdates";
import { useProjectCanvas } from "./api/projectCanvas";
import { ChatPanel } from "./components/ChatPanel";
import { GraphView } from "./components/GraphView";
import { ProjectCanvas } from "./components/ProjectCanvas";
import { ProjectActions } from "./components/project/ProjectActions";
import { queueEntries, ReviewPanel } from "./components/ReviewPanel";
import { Sidebar } from "./components/Sidebar";
import { useDesktopProjectList } from "./components/SidebarProjects";
import { Splitter } from "./components/Splitter";
import { desktopPosition, saveDesktopPosition, type DesktopPosition } from "./workbench/desktop";
import { buildGraph, liveWork, type View } from "./workbench/graph";
import { nodeTitle, statusText } from "./workbench/text";
import type { ActivitySnapshot, Atlas, GraphSnapshot, ImpactPreview } from "./workbench/types";
import { clampSize, useLayout } from "./workbench/useLayout";

const ACTIVITY_MS = 4000;
const NO_CARD_SIZES = {};

function errorText(t: TFunction, error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  return t(`errors.${error.message}`, { defaultValue: error.message });
}

export type Focus = { claimId: string; nodeId?: string; nonce: number };
export type WorkbenchPosition = DesktopPosition & {
  view: View | "project";
  mapClaimId?: string;
  assumption: string | null;
  referenced: boolean;
};

export function Workbench({
  session,
  focus,
  onFocusHandled,
  pendingReviews,
  returnTo,
  onPositionChange,
}: {
  session: string;
  focus?: Focus | null;
  onFocusHandled: (focus: null) => void;
  pendingReviews: number | undefined;
  returnTo?: WorkbenchPosition;
  onPositionChange: (position: WorkbenchPosition) => void;
}) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const layout = useLayout();
  const { sizes, clamped } = layout;
  const [initial] = useState(() => ({
    position: focus ?? returnTo ?? desktopPosition(),
    restore: focus ? undefined : returnTo,
  }));
  const [claimId, setClaimId] = useState<string | null>(initial.position?.claimId ?? null);
  const pendingLaunch = useRef(!initial.position);
  const pendingNode = useRef(initial.position?.nodeId);
  const [referencedClaim, setReferencedClaim] = useState<string | null>(
    initial.restore?.referenced ? initial.restore.claimId : null,
  );
  const [missingReferenceNode, setMissingReferenceNode] = useState(false);
  const [view, setView] = useState<View | "project">(focus ? "proof" : (initial.restore?.view ?? "project"));
  const [mapClaimId, setMapClaimId] = useState(initial.restore?.mapClaimId ?? initial.position?.claimId ?? null);
  const [mapAtlasOpen, setMapAtlasOpen] = useState(false);
  const [mapDetailsOpen, setMapDetailsOpen] = useState(false);
  const [mapDetailNode, setMapDetailNode] = useState<{ claimId: string; nodeId: string } | null>(null);
  const [recordedGoal, setRecordedGoal] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [assumption, setAssumption] = useState<string | null>(initial.restore?.assumption ?? null);
  const [allNodes, setAllNodes] = useState(false);
  const [premiseDialog, setPremiseDialog] = useState<{ id?: string; mapClaimId?: string } | null>(null);
  const [premiseChatAnchor, setPremiseChatAnchor] = useState<string | null>(null);
  const [live, setLive] = useState<{ side?: number; right?: number; chat?: number }>({});
  const inMap = view === "project";
  const canvas = useProjectCanvas(session, inMap);
  const projects = useDesktopProjectList();
  const projectName =
    projects.data?.projects.find(({ id }) => id.toLowerCase() === projects.data?.activeId)?.name ||
    t("projects.current");

  const claims = useQuery({
    queryKey: ["claims"],
    queryFn: ({ signal }) => api<ClaimsResponse>(session, "/api/claims", { signal }),
  });
  const atlas = useQuery({
    queryKey: ["atlas"],
    queryFn: ({ signal }) => api<Atlas>(session, "/api/atlas", { signal }),
    enabled: inMap ? mapAtlasOpen : sizes.atlasOpen,
    retry: false,
  });

  // Choose the launch claim, or keep the current one if it still exists.
  useEffect(() => {
    const list = claims.data?.claims;
    if (!list) return;
    if (pendingLaunch.current) {
      pendingLaunch.current = false;
      // The CLI may name an exact claim when starting this workbench.
      const requested = claims.data?.claim_id;
      if (requested) {
        setClaimId(requested);
        setMapClaimId(requested);
        setReferencedClaim(requested);
        setView("proof");
        return;
      }
    }
    if (claimId && (claimId === referencedClaim || list.some((claim) => claim.id === claimId))) return;
    setClaimId(list[0]?.id ?? null);
  }, [claims.data, claimId, referencedClaim]);

  const mapClaims = canvas.claims ?? claims.data?.claims ?? [];
  const mapSelection = mapClaims.some(({ id }) => id === mapClaimId) ? mapClaimId : (mapClaims[0]?.id ?? null);
  const graphClaimId = inMap ? (mapDetailsOpen ? mapSelection : null) : claimId;

  const snapshot = useQuery({
    queryKey: ["graph", graphClaimId],
    enabled: Boolean(
      graphClaimId &&
        (inMap || graphClaimId === referencedClaim || claims.data?.claims.some((claim) => claim.id === graphClaimId)),
    ),
    queryFn: ({ signal }) =>
      api<GraphSnapshot>(session, "/api/graph?claim=" + encodeURIComponent(graphClaimId!), { signal }),
  });
  const selectedGraph = useMemo(
    () => (snapshot.data && snapshot.data.claim.id === graphClaimId ? buildGraph(t, snapshot.data) : null),
    [graphClaimId, snapshot.data, t],
  );
  const graph = inMap ? null : selectedGraph;
  const mapGraph = inMap ? selectedGraph : null;
  const mapNode =
    mapDetailNode?.claimId === mapSelection && mapGraph?.nodes.has(mapDetailNode.nodeId)
      ? mapDetailNode.nodeId
      : (mapGraph?.order.find((id) => mapGraph.nodes.get(id)?.kind === "goal") ?? mapGraph?.order[0] ?? null);

  // Another page asked to open a claim (and maybe a node).
  useEffect(() => {
    if (!focus) return;
    pendingLaunch.current = false;
    setClaimId(focus.claimId);
    setMapClaimId(focus.claimId);
    setView("proof");
    pendingNode.current = focus.nodeId;
    setReferencedClaim(focus.claimId);
    setPremiseChatAnchor(null);
    setMissingReferenceNode(false);
    onFocusHandled(null);
  }, [focus, onFocusHandled]);

  // A newly loaded claim starts at its focus (or goal) and its first assumption; a reload keeps the selection.
  useEffect(() => {
    if (!graph || graph.claim.id !== claimId) return;
    const wanted = pendingNode.current;
    pendingNode.current = undefined;
    if (wanted && claimId === referencedClaim) setMissingReferenceNode(!graph.nodes.has(wanted));
    setSelected((current) => {
      if (wanted && graph.nodes.has(wanted)) return wanted;
      if (current && graph.nodes.has(current)) return current;
      const focus = graph.focusId && graph.nodes.has(graph.focusId) ? graph.focusId : null;
      return focus ?? [...graph.nodes.values()].find((node) => node.kind === "goal")?.id ?? graph.order[0] ?? null;
    });
    setAssumption((current) =>
      current && graph.hypotheses.some((node) => node.id === current) ? current : (graph.hypotheses[0]?.id ?? null),
    );
  }, [graph, claimId, referencedClaim]);

  useEffect(() => {
    if (view === "project") {
      if (mapSelection) {
        const id = claimId ?? mapSelection;
        onPositionChange({
          claimId: id,
          nodeId: selected ?? pendingNode.current ?? `goal:${id}`,
          view,
          mapClaimId: mapSelection,
          assumption,
          referenced: id === referencedClaim,
        });
      }
    } else if (claimId && selected && graph?.claim.id === claimId && graph.nodes.has(selected)) {
      const position = { claimId, nodeId: selected };
      onPositionChange({
        ...position,
        view,
        mapClaimId: mapSelection ?? undefined,
        assumption,
        referenced: claimId === referencedClaim,
      });
      saveDesktopPosition(position);
    }
  }, [claimId, graph, selected, view, mapSelection, assumption, referencedClaim, onPositionChange]);

  const basis = assumption ? graph?.nodes.get(assumption)?.reference?.basis_version_id : undefined;
  const impact = useQuery({
    queryKey: ["impact", claimId, basis],
    enabled: view === "impact" && Boolean(claimId && basis),
    queryFn: ({ signal }) =>
      api<ImpactPreview>(session, "/api/impact?" + new URLSearchParams({ claim: claimId!, basis: String(basis) }), {
        signal,
      }),
  });

  const activity = useQuery({
    queryKey: ["activity", claimId],
    enabled: !inMap && Boolean(graph),
    refetchInterval: useFallbackInterval(ACTIVITY_MS),
    queryFn: async ({ signal }) => {
      const reply = await api<ActivitySnapshot>(session, "/api/activity?claim=" + encodeURIComponent(claimId!), {
        signal,
      });
      if (reply.claim_id !== claimId) throw new Error(t("chat.mismatch"));
      return reply;
    },
  });
  const activityError = activity.isError ? errorText(t, activity.error) : "";
  const work = useMemo(
    () => liveWork(t, graph, activity.data, activity.isError),
    [t, graph, activity.data, activity.isError],
  );

  const selectClaim = (id: string) => {
    setRecordedGoal(null);
    setMapClaimId(id);
    setView("proof");
    if (inMap && id === claimId) return;
    setClaimId(id);
    setSelected(null);
    setPremiseChatAnchor(null);
    setAssumption(null);
    setReferencedClaim(claims.data?.claims.some((claim) => claim.id === id) ? null : id);
    setMissingReferenceNode(false);
    pendingNode.current = undefined;
  };
  const inspectClaim = (id: string) => {
    setMapClaimId(id);
    setMapDetailNode(null);
    setMapDetailsOpen(true);
  };
  const selectNode = (id: string) => {
    const node = graph?.nodes.get(id);
    if (!node) return;
    if (node.kind === "proposed_premise" && selected && graph?.nodes.get(selected)?.kind !== "proposed_premise")
      setPremiseChatAnchor(selected);
    setSelected(id);
    setMissingReferenceNode(false);
    if (node.kind === "hypothesis" && node.reference?.basis_version_id) setAssumption(id);
  };
  const openReference = (id: string, nodeId: string | null) => {
    setMapClaimId(id);
    setView("proof");
    if (graph?.claim.id === id && claimId === id) {
      if (nodeId && graph.nodes.has(nodeId)) {
        selectNode(nodeId);
      } else {
        setSelected(graph.order.find((id) => graph.nodes.get(id)?.kind === "goal") ?? null);
        setMissingReferenceNode(Boolean(nodeId));
      }
    } else {
      selectClaim(id);
      setReferencedClaim(id);
      pendingNode.current = nodeId ?? undefined;
    }
  };
  const changeView = (next: View) => {
    if (next === "impact" && graph) {
      const current = graph.hypotheses.find(({ id }) => id === selected);
      const chosen = current?.id ?? assumption;
      if (chosen) {
        setAssumption(chosen);
        setSelected(chosen);
      }
    }
    setView(next);
  };
  const refresh = () => {
    void queries.invalidateQueries();
  };
  const openRecorded = async (claimId?: string) => {
    await queries.invalidateQueries();
    if (claimId) {
      if (inMap) setMapClaimId(claimId);
      else selectClaim(claimId);
    }
  };
  const openPremises = (id?: string) => {
    setReferencedClaim(claimId);
    setPremiseDialog({ id });
  };
  const reviewMapPremise = (id: string) => {
    if (mapGraph) setPremiseDialog({ id, mapClaimId: mapGraph.claim.id });
  };
  const showRecordedGoal = (id: string) => {
    setMapClaimId(id);
    if (!inMap) {
      setReferencedClaim(claimId);
      setRecordedGoal(id);
    }
    return queries.invalidateQueries();
  };

  const notice = !session
    ? t("session.missing")
    : claims.isError
      ? errorText(t, claims.error)
      : claims.isPending
        ? t("notice.loadingProject")
        : inMap
          ? canvas.loadingClaims
            ? t("notice.loadingProject")
            : ""
          : claims.data.claims.length === 0 && !referencedClaim
            ? t("notice.noClaims")
            : snapshot.isError
              ? errorText(t, snapshot.error)
              : !graph
                ? t("notice.loadingClaim")
                : graph.order.length === 0
                  ? t("notice.noNodes")
                  : "";
  const noticeIsError = claims.isError || (!inMap && snapshot.isError);
  const mapDetailsNotice = snapshot.isError
    ? errorText(t, snapshot.error)
    : !mapGraph
      ? t("notice.loadingClaim")
      : !mapNode
        ? t("notice.noNodes")
        : "";
  const workspaceState = claims.isError
    ? t("sidebar.readFailed")
    : claims.data
      ? t("sidebar.connected", { count: mapClaims.length })
      : t("sidebar.connecting");
  const reviewCount = inMap || view === "impact" ? 0 : queueEntries(t, graph, view, assumption, impact.data).length;
  const goal = graph?.claim.id === claimId && claimId ? graph.nodes.get(`goal:${claimId}`) : undefined;
  // A premise under review can keep the previous conversation, but is not a proof-note target.
  const reviewingPremise = Boolean(selected && graph?.nodes.get(selected)?.kind === "proposed_premise");
  const chatSelected = reviewingPremise
    ? premiseChatAnchor && graph?.nodes.has(premiseChatAnchor)
      ? premiseChatAnchor
      : (goal?.id ?? null)
    : selected;
  const claimTitle =
    graph?.claim.title || claims.data?.claims.find((claim) => claim.id === claimId)?.title || t("toolbar.defaultTitle");
  const meta = graph
    ? [graph.claim.code, statusText(t, graph.claim.status), graph.claim.domain].filter(Boolean).join(" · ")
    : "";
  const showGraph = !inMap && Boolean(graph && graph.order.length);
  const showReview = inMap ? mapDetailsOpen && Boolean(mapSelection) : layout.reviewOpen;
  const mapPremiseClaim = premiseDialog?.mapClaimId;
  const actionsClaim = mapPremiseClaim && mapGraph?.claim.id === mapPremiseClaim ? mapGraph.claim : graph?.claim;

  const side = live.side ?? clamped.side;
  const right = live.right ?? clamped.right;
  const chat = live.chat ?? clamped.chat;
  const commit = (key: "side" | "right" | "chat", value: number) => {
    setLive((current) => ({ ...current, [key]: undefined }));
    layout.update({ [key]: clampSize(key, value, layout.windowHeight) });
  };
  const resize = (key: "side" | "right" | "chat", value: number) =>
    setLive((current) => ({ ...current, [key]: clampSize(key, value, layout.windowHeight) }));

  const lean = goal?.reference;
  const lemmaPlanScope =
    claimId &&
    graph?.claim.lemma_plan_ready &&
    goal?.formalization &&
    typeof lean?.statement_id === "string" &&
    typeof lean.statement_version === "number" &&
    typeof lean.statement_sha256 === "string"
      ? {
          claimId,
          title: claimTitle,
          statementId: lean.statement_id,
          version: lean.statement_version,
          checksum: lean.statement_sha256,
          statement: goal.formalization,
        }
      : null;

  return (
    <div
      className="app"
      id="workbench"
      data-sidebar={layout.sidebarOpen ? "open" : "collapsed"}
      data-review={showReview ? "open" : "collapsed"}
      data-view={view}
      style={{ "--side-w": `${side}px`, "--right-w": `${right}px`, "--chat-h": `${chat}px` } as React.CSSProperties}
    >
      <Sidebar
        pendingReviews={pendingReviews}
        open={layout.sidebarOpen}
        workspaceState={workspaceState}
        onToggle={() => layout.update({ sidebarOpen: !layout.sidebarOpen })}
        onRefresh={refresh}
        refreshing={claims.isFetching}
      />
      <Splitter
        name="side"
        value={side}
        label={t("splitter.side")}
        onResize={(value) => resize("side", value)}
        onCommit={(value) => commit("side", value)}
      />
      <main className="main">
        <header className="toolbar">
          <div className="claim-heading">
            {!inMap && (
              <button type="button" className="workbench-map-return" onClick={() => setView("project")}>
                {t("canvasShell.map")}
              </button>
            )}
            <h1 title={inMap ? projectName : claimTitle}>{inMap ? projectName : claimTitle}</h1>
            {inMap && <span className="claim-meta">{t("canvasShell.map")}</span>}
            {!inMap && meta && <span className="claim-meta">{meta}</span>}
          </div>
          <div className="project-toolbar-actions">
            <ProjectActions
              session={session}
              claimId={actionsClaim?.id ?? null}
              claimCode={actionsClaim?.code}
              claimCodesAvailable={Boolean(claims.data?.claim_codes)}
              lemmaPlanScope={inMap ? null : lemmaPlanScope}
              claimTitle={actionsClaim?.title || claimTitle}
              premiseReview={actionsClaim?.premise_review}
              premiseDialog={premiseDialog}
              overview={inMap ? undefined : sizes.atlasOpen}
              onToggleOverview={() => layout.update({ atlasOpen: !sizes.atlasOpen })}
              onOpenPremises={openPremises}
              onClosePremises={() => setPremiseDialog(null)}
              onPremiseRecorded={openRecorded}
              onRecorded={openRecorded}
              onGoalRecorded={showRecordedGoal}
              onOpenReference={openReference}
            />
            {!inMap && !layout.reviewOpen && graph && (
              <button
                type="button"
                className="chip"
                aria-controls="review"
                onClick={() => layout.update({ reviewOpen: true })}
              >
                {reviewCount ? t("toolbar.openReviewCount", { count: reviewCount }) : t("toolbar.openReview")}
              </button>
            )}
          </div>
        </header>
        {view === "impact" && graph && (
          <div className="impact-bar">
            {graph.hypotheses.length > 0 && (
              <>
                <label htmlFor="premise-select">{t("impact.preview")}</label>
                <select
                  id="premise-select"
                  value={assumption ?? ""}
                  onChange={(event) => selectNode(event.target.value)}
                >
                  {graph.hypotheses.map((node) => (
                    <option key={node.id} value={node.id}>
                      {nodeTitle(t, node)}
                    </option>
                  ))}
                </select>
              </>
            )}
            <span role="status">
              {impact.isFetching
                ? t("impact.loading")
                : impact.isError
                  ? errorText(t, impact.error)
                  : graph.hypotheses.length
                    ? t("impact.readOnly")
                    : t("impact.noPremise")}
            </span>
          </div>
        )}
        {notice && (
          <p className="notice" role="status" data-error={String(noticeIsError)}>
            {notice}
          </p>
        )}
        {!inMap && recordedGoal && (
          <div className="workbench-map-status" role="status">
            {t("canvasShell.goalAdded")}
            <button type="button" className="conversation-text-action" onClick={() => setView("project")}>
              {t("canvasShell.viewOnMap")}
            </button>
          </div>
        )}
        {inMap && claims.data && !canvas.loadingClaims && (
          <>
            {(canvas.loadError || canvas.saveError || canvas.indexError) && (
              <div className="workbench-map-status" role="alert">
                {canvas.indexError
                  ? t("canvasShell.claimsUnavailable")
                  : errorText(t, canvas.loadError || canvas.saveError)}
                <button type="button" className="conversation-text-action" onClick={canvas.reload}>
                  {t("canvasShell.reload")}
                </button>
              </div>
            )}
            <section className="graph-area project-map-area">
              <ProjectCanvas
                claims={mapClaims}
                selected={mapSelection}
                onSelect={inspectClaim}
                onOpenClaim={selectClaim}
                detailsOpen={showReview}
                onToggleDetails={() => setMapDetailsOpen((open) => !open)}
                atlasClaims={atlas.data?.claims}
                atlasOpen={mapAtlasOpen}
                atlasBox={sizes.atlas}
                atlasError={atlas.isError ? errorText(t, atlas.error) : undefined}
                onToggleAtlas={() => setMapAtlasOpen((open) => !open)}
                onAtlasBox={(box, persist) => layout.update({ atlas: box }, persist)}
                layout={canvas.layout}
                onLayout={canvas.apply}
                layoutUnavailable={canvas.loading || Boolean(canvas.loadError)}
                saving={canvas.saving}
              />
            </section>
          </>
        )}
        {!inMap && missingReferenceNode && (
          <p className="notice" role="status">
            {t("claimCodes.nodeAbsent")}
          </p>
        )}
        {showGraph && graph && claimId && (
          <section className="graph-area" aria-label={t("graph.label")}>
            <GraphView
              graph={graph}
              view={inMap ? "proof" : view}
              selected={selected}
              assumption={assumption}
              impact={impact.data}
              work={work.byNode}
              running={work.running}
              queued={work.queued}
              atlasClaims={atlas.data?.claims ?? []}
              claimId={claimId}
              atlasOpen={sizes.atlasOpen}
              atlasBox={sizes.atlas}
              onAtlasBox={(box, persist) => layout.update({ atlas: box }, persist)}
              cardSizes={sizes.cards[`${claimId}:${view}`] ?? NO_CARD_SIZES}
              onResizeCard={(id, size) => layout.resizeCard(`${claimId}:${view}`, id, size)}
              onSelect={selectNode}
              onView={changeView}
            />
          </section>
        )}
        {!inMap && (
          <Splitter
            name="chat"
            value={chat}
            label={t("splitter.chat")}
            hidden={!sizes.chatOpen}
            onResize={(value) => resize("chat", value)}
            onCommit={(value) => commit("chat", value)}
          />
        )}
        {!inMap && (
          <ChatPanel
            session={session}
            graph={graph}
            claimId={claimId}
            selected={chatSelected}
            proofSelected={reviewingPremise ? null : selected}
            activity={activity.data}
            activityError={activityError}
            scopeReady={
              claimId
                ? graph?.claim.id === claimId &&
                  (allNodes || Boolean(chatSelected && graph.nodes.has(chatSelected)) || !graph.nodes.size)
                : claims.data?.claims.length === 0
            }
            running={work.running}
            queued={work.queued}
            open={sizes.chatOpen}
            allNodes={allNodes}
            onToggle={() => layout.update({ chatOpen: !sizes.chatOpen })}
            onAllNodes={() => setAllNodes(!allNodes)}
            onSelect={selectNode}
            onSent={() => void queries.invalidateQueries({ queryKey: ["activity", claimId] })}
          />
        )}
      </main>
      {showReview && (
        <Splitter
          name="right"
          value={right}
          label={t("splitter.right")}
          onResize={(value) => resize("right", value)}
          onCommit={(value) => commit("right", value)}
        />
      )}
      {(!inMap || showReview) && (
        <ReviewPanel
          graph={selectedGraph}
          claims={inMap ? mapClaims : (claims.data?.claims ?? [])}
          claimId={inMap ? mapSelection : claimId}
          view={inMap ? "proof" : view}
          selected={inMap ? mapNode : selected}
          assumption={inMap ? null : assumption}
          impact={inMap ? undefined : impact.data}
          impactLoading={!inMap && impact.isFetching}
          impactError={!inMap && impact.isError ? errorText(t, impact.error) : ""}
          notice={inMap && mapDetailsNotice ? { text: mapDetailsNotice, error: snapshot.isError } : undefined}
          onSelect={
            inMap ? (nodeId) => mapSelection && setMapDetailNode({ claimId: mapSelection, nodeId }) : selectNode
          }
          onReviewPremise={inMap ? reviewMapPremise : openPremises}
          onClose={
            inMap
              ? () => {
                  setMapDetailsOpen(false);
                  document.getElementById("map-details-toggle")?.focus();
                }
              : () => layout.update({ reviewOpen: false })
          }
        />
      )}
    </div>
  );
}
