import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api, type ClaimsResponse } from "./api/client";
import { ChatPanel } from "./components/ChatPanel";
import { GraphView } from "./components/GraphView";
import { ProjectActions } from "./components/project/ProjectActions";
import { queueEntries, ReviewPanel } from "./components/ReviewPanel";
import { Sidebar } from "./components/Sidebar";
import { Splitter } from "./components/Splitter";
import { desktopPosition, saveDesktopPosition } from "./workbench/desktop";
import { buildGraph, liveWork, type View } from "./workbench/graph";
import { nodeTitle, statusText } from "./workbench/text";
import type { ActivitySnapshot, Atlas, GraphSnapshot, ImpactPreview } from "./workbench/types";
import { clampSize, useLayout } from "./workbench/useLayout";

const ACTIVITY_MS = 4000;

function errorText(t: TFunction, error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  return t(`errors.${error.message}`, { defaultValue: error.message });
}

export type Focus = { claimId: string; nodeId?: string; nonce: number };

export function Workbench({
  session,
  focus,
  onFocusHandled,
}: {
  session: string;
  focus?: Focus | null;
  onFocusHandled: (focus: null) => void;
}) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const layout = useLayout();
  const { sizes, clamped } = layout;
  const [initial] = useState(() => focus ?? desktopPosition());
  const [claimId, setClaimId] = useState<string | null>(initial?.claimId ?? null);
  const pendingNode = useRef(initial?.nodeId);
  const [view, setView] = useState<View>("proof");
  const [selected, setSelected] = useState<string | null>(null);
  const [assumption, setAssumption] = useState<string | null>(null);
  const [allNodes, setAllNodes] = useState(false);
  const [live, setLive] = useState<{ side?: number; right?: number; chat?: number }>({});

  const claims = useQuery({
    queryKey: ["claims"],
    queryFn: ({ signal }) => api<ClaimsResponse>(session, "/api/claims", { signal }),
  });
  const atlas = useQuery({
    queryKey: ["atlas"],
    queryFn: ({ signal }) => api<Atlas>(session, "/api/atlas", { signal }),
    retry: false,
  });

  // Choose the launch claim, or keep the current one if it still exists.
  useEffect(() => {
    const list = claims.data?.claims;
    if (!list) return;
    if (claimId && list.some((claim) => claim.id === claimId)) return;
    setClaimId(claims.data?.claim_id ?? list[0]?.id ?? null);
  }, [claims.data, claimId]);

  const snapshot = useQuery({
    queryKey: ["graph", claimId],
    enabled: Boolean(claims.data?.claims.some((claim) => claim.id === claimId)),
    queryFn: ({ signal }) =>
      api<GraphSnapshot>(session, "/api/graph?claim=" + encodeURIComponent(claimId!), { signal }),
  });
  const graph = useMemo(() => (snapshot.data ? buildGraph(t, snapshot.data) : null), [snapshot.data, t]);

  // Another page asked to open a claim (and maybe a node).
  useEffect(() => {
    if (!focus) return;
    setClaimId(focus.claimId);
    setView("proof");
    pendingNode.current = focus.nodeId;
    onFocusHandled(null);
  }, [focus, onFocusHandled]);

  // A newly loaded claim starts at its focus (or goal) and its first assumption; a reload keeps the selection.
  useEffect(() => {
    if (!graph || graph.claim.id !== claimId) return;
    const wanted = pendingNode.current;
    pendingNode.current = undefined;
    setSelected((current) => {
      if (wanted && graph.nodes.has(wanted)) return wanted;
      if (current && graph.nodes.has(current)) return current;
      const focus = graph.focusId && graph.nodes.has(graph.focusId) ? graph.focusId : null;
      return focus ?? [...graph.nodes.values()].find((node) => node.kind === "goal")?.id ?? graph.order[0] ?? null;
    });
    setAssumption((current) =>
      current && graph.hypotheses.some((node) => node.id === current) ? current : (graph.hypotheses[0]?.id ?? null),
    );
  }, [graph, claimId]);

  useEffect(() => {
    if (claimId && selected && graph?.claim.id === claimId && graph.nodes.has(selected)) {
      saveDesktopPosition({ claimId, nodeId: selected });
    }
  }, [claimId, graph, selected]);

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
    enabled: Boolean(graph),
    refetchInterval: ACTIVITY_MS,
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
    setClaimId(id);
    setSelected(null);
    setAssumption(null);
    setView("proof");
  };
  const selectNode = (id: string) => {
    const node = graph?.nodes.get(id);
    if (!node) return;
    setSelected(id);
    if (node.kind === "hypothesis" && node.reference?.basis_version_id) setAssumption(id);
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
    if (claimId) selectClaim(claimId);
  };

  const notice = !session
    ? t("session.missing")
    : claims.isError
      ? errorText(t, claims.error)
      : claims.isPending
        ? t("notice.loadingProject")
        : claims.data.claims.length === 0
          ? t("notice.noClaims")
          : snapshot.isError
            ? errorText(t, snapshot.error)
            : !graph
              ? t("notice.loadingClaim")
              : graph.order.length === 0
                ? t("notice.noNodes")
                : "";
  const noticeIsError = claims.isError || snapshot.isError;
  const workspaceState = claims.isError
    ? t("sidebar.readFailed")
    : claims.data
      ? t("sidebar.connected", { count: claims.data.claims.length })
      : t("sidebar.connecting");
  const reviewCount = view === "impact" ? 0 : queueEntries(t, graph, view, assumption, impact.data).length;
  const claimTitle =
    graph?.claim.title || claims.data?.claims.find((claim) => claim.id === claimId)?.title || t("toolbar.defaultTitle");
  const meta = graph ? [statusText(t, graph.claim.status), graph.claim.domain].filter(Boolean).join(" · ") : "";
  const showGraph = Boolean(graph && graph.order.length);

  const side = live.side ?? clamped.side;
  const right = live.right ?? clamped.right;
  const chat = live.chat ?? clamped.chat;
  const commit = (key: "side" | "right" | "chat", value: number) => {
    setLive((current) => ({ ...current, [key]: undefined }));
    layout.update({ [key]: clampSize(key, value, layout.windowHeight) });
  };
  const resize = (key: "side" | "right" | "chat", value: number) =>
    setLive((current) => ({ ...current, [key]: clampSize(key, value, layout.windowHeight) }));

  return (
    <div
      className="app"
      id="workbench"
      data-sidebar={layout.sidebarOpen ? "open" : "collapsed"}
      data-review={layout.reviewOpen ? "open" : "collapsed"}
      data-view={view}
      style={{ "--side-w": `${side}px`, "--right-w": `${right}px`, "--chat-h": `${chat}px` } as React.CSSProperties}
    >
      <Sidebar
        claims={claims.data?.claims ?? []}
        atlasClaims={atlas.data?.claims ?? []}
        claimId={claimId}
        open={layout.sidebarOpen}
        atlasOpen={sizes.atlasOpen}
        workspaceState={workspaceState}
        onSelect={selectClaim}
        onToggle={() => layout.update({ sidebarOpen: !layout.sidebarOpen })}
        onToggleAtlas={() => layout.update({ atlasOpen: !sizes.atlasOpen })}
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
            <h1>{claimTitle}</h1>
            {meta && <span className="claim-meta">{meta}</span>}
          </div>
          <div className="project-toolbar-actions">
            <ProjectActions
              session={session}
              claimId={graph?.claim.id === claimId ? claimId : null}
              onRecorded={openRecorded}
            />
            {!layout.reviewOpen && graph && (
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
        {showGraph && graph && claimId && (
          <section className="graph-area" aria-label={t("graph.label")}>
            <GraphView
              graph={graph}
              view={view}
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
              onSelect={selectNode}
              onView={changeView}
            />
          </section>
        )}
        <Splitter
          name="chat"
          value={chat}
          label={t("splitter.chat")}
          hidden={!sizes.chatOpen}
          onResize={(value) => resize("chat", value)}
          onCommit={(value) => commit("chat", value)}
        />
        <ChatPanel
          session={session}
          graph={graph}
          claimId={claimId}
          selected={selected}
          activity={activity.data}
          activityError={activityError}
          running={work.running}
          queued={work.queued}
          open={sizes.chatOpen}
          allNodes={allNodes}
          onToggle={() => layout.update({ chatOpen: !sizes.chatOpen })}
          onAllNodes={() => setAllNodes(!allNodes)}
          onSelect={selectNode}
          onSent={() => void queries.invalidateQueries({ queryKey: ["activity", claimId] })}
        />
      </main>
      <Splitter
        name="right"
        value={right}
        label={t("splitter.right")}
        onResize={(value) => resize("right", value)}
        onCommit={(value) => commit("right", value)}
      />
      <ReviewPanel
        graph={graph}
        claims={claims.data?.claims ?? []}
        claimId={claimId}
        view={view}
        selected={selected}
        assumption={assumption}
        impact={impact.data}
        impactLoading={impact.isFetching}
        impactError={impact.isError ? errorText(t, impact.error) : ""}
        onSelect={selectNode}
        onClose={() => layout.update({ reviewOpen: false })}
      />
    </div>
  );
}
