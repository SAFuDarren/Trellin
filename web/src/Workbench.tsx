import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api, ApiError } from "./api/client";
import type { ConversationAnchor } from "./api/conversations";
import { useFallbackInterval } from "./api/liveUpdates";
import {
  sameResearchLink,
  useProjectCanvas,
  type ProjectCanvasOperation,
  type ResearchLink,
} from "./api/projectCanvas";
import { ActionFeedback } from "./components/ActionFeedback";
import { useAtlas, useClaimGraph, useClaims, useLibrary } from "./api/sections";
import { ProofActivity } from "./components/ChatPanel";
import type { ConversationStarterScope } from "./components/ConversationRecipients";
import { GraphView } from "./components/GraphView";
import { Chevron, WorkbenchIcon } from "./components/Icons";
import type { FloatingGroup, FloatingOption } from "./components/FloatingControls";
import { ProjectCanvas } from "./components/ProjectCanvas";
import { ProjectActions } from "./components/project/ProjectActions";
import { ReviewPanel } from "./components/ReviewPanel";
import { PageHead } from "./components/SectionLayout";
import { Splitter } from "./components/Splitter";
import { href, parseWorkbenchAddress, workbenchSub, type AddressMode, type WorkbenchAddress } from "./shell/route";
import {
  graphAction,
  graphCopyActions,
  type ProjectAction,
  type ProjectActionRequest,
  type ProjectDialog,
} from "./workbench/actions";
import { MapDrop, draggingFiles } from "./components/project/MapDrop";
import type { InboxItem } from "./api/sections";
import type { ClaimProgress } from "./workbench/claimProgress";
import { desktopPosition, saveDesktopPosition, type DesktopPosition } from "./workbench/desktop";
import { buildGraph, liveWork, type Graph, type View } from "./workbench/graph";
import { nodeTitle } from "./workbench/text";
import type { ActivitySnapshot, GraphNode, ImpactPreview } from "./workbench/types";
import { clampSize, useLayout } from "./workbench/useLayout";

const ACTIVITY_MS = 4000;
const NO_CARD_SIZES = {};

function errorText(t: TFunction, error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  return t(`errors.${error.message}`, { defaultValue: error.message });
}

function chatStarterScope(graph: Graph | null, node?: GraphNode): ConversationStarterScope {
  if (!node || node.kind === "goal") return "claim";
  if (node.kind === "obligation" && (node.status === "blocked" || graph?.blockers.get(node.id)?.length))
    return "blockedObligation";
  return "node";
}

export type Focus = { claimId: string; nodeId?: string; proof?: boolean; nonce: number };
type Selection = { claimId: string; nodeId?: string; referenced?: boolean };
type MapReturn = { detailsOpen: boolean; selection: Selection | undefined };
export type WorkbenchPosition = DesktopPosition & {
  view: View;
  detailsOpen: boolean;
  proofOpen: boolean;
  assumption: string | null;
  referenced: boolean;
  mapReturn?: MapReturn;
};

export function Workbench({
  session,
  claimProgress,
  claimReviews,
  focus,
  onFocusHandled,
  returnTo,
  onPositionChange,
  onChatAnchor,
  onChatStarterScope,
  actionRequest,
  onActionHandled,
  address = "",
  onAddress,
}: {
  session: string;
  claimProgress: ReadonlyMap<string, ClaimProgress>;
  claimReviews: ReadonlyMap<string, readonly InboxItem[]>;
  focus?: Focus | null;
  onFocusHandled: (focus: null) => void;
  returnTo?: WorkbenchPosition;
  onPositionChange: (position: WorkbenchPosition) => void;
  onChatAnchor: (anchor: ConversationAnchor, title: string, starterScope: ConversationStarterScope) => void;
  onChatStarterScope: (scope: ConversationStarterScope) => void;
  actionRequest?: ProjectActionRequest;
  onActionHandled: () => void;
  /** #274: the workbench part of the URL hash, and the call that records the open proof there. */
  address?: string;
  onAddress?: (address: WorkbenchAddress | null, mode: AddressMode) => void;
}) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const layout = useLayout();
  const { sizes, clamped } = layout;
  const [desktop] = useState(() => desktopPosition());
  // A claim address opens that proof, like a focus request from another page.
  const [launch] = useState<Focus | null | undefined>(() => {
    const addressed = parseWorkbenchAddress(address);
    return focus ?? (addressed && { ...addressed, proof: true, nonce: 0 });
  });
  const [selection, setSelection] = useState<Selection | undefined>(
    launch ? { ...launch, referenced: true } : (returnTo ?? (desktop && { ...desktop, referenced: true })),
  );
  const pendingLaunch = useRef(!launch && !returnTo && !desktop);
  const [detailsOpen, setDetailsOpen] = useState(Boolean(launch || (returnTo?.detailsOpen ?? desktop)));
  const [proofOpen, setProofOpen] = useState(
    Boolean(
      launch
        ? launch.proof || launch.nodeId
        : (returnTo?.proofOpen ?? (desktop?.nodeId && desktop.nodeId !== `goal:${desktop.claimId}`)),
    ),
  );
  const proofReturn = useRef<HTMLButtonElement>(null);
  const beforeProof = useRef<MapReturn | null>(
    returnTo?.mapReturn ??
      (returnTo && !returnTo.proofOpen && (launch?.proof || launch?.nodeId)
        ? { detailsOpen: returnTo.detailsOpen, selection: returnTo }
        : null),
  );
  const [activityOpen, setActivityOpen] = useState(false);
  const [view, setView] = useState<View>(returnTo?.view ?? "proof");
  const [assumptionId, setAssumption] = useState<string | null>(returnTo?.assumption ?? null);
  const [mapAtlasOpen, setMapAtlasOpen] = useState(false);
  const [recordedGoal, setRecordedGoal] = useState<string | null>(null);
  const [allNodes, setAllNodes] = useState(false);
  const [premiseDialog, setPremiseDialog] = useState<{ id?: string } | null>(null);
  const [projectAction, setProjectAction] = useState<ProjectDialog | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [droppingFile, setDroppingFile] = useState(false);
  const [contextClaim, setContextClaim] = useState<string | null>(null);
  const [copyNotice, setCopyNotice] = useState<{ id: number; copied: boolean }>();
  const [linkFeedback, setLinkFeedback] = useState<{ session: string; removed: ResearchLink | null }>();
  const copySequence = useRef(0);
  const [liveRight, setLiveRight] = useState<number>();
  const canvas = useProjectCanvas(session, true);
  const claims = useClaims(session);
  const atlas = useAtlas(session, mapAtlasOpen);

  useEffect(() => {
    if (!claims.data || !pendingLaunch.current) return;
    pendingLaunch.current = false;
    if (claims.data.claim_id) {
      setSelection({ claimId: claims.data.claim_id, referenced: true });
      setDetailsOpen(true);
      setProofOpen(true);
    }
  }, [claims.data]);

  const mapClaims = canvas.claims ?? claims.data?.claims ?? [];
  const requestedClaim = selection?.claimId;
  const mapSelection = mapClaims.some(({ id }) => id === requestedClaim)
    ? requestedClaim!
    : selection?.referenced
      ? null
      : (mapClaims[0]?.id ?? null);
  const claimId = selection?.referenced ? selection.claimId : mapSelection;
  const showReview = detailsOpen && Boolean(claimId);
  const library = useLibrary(session, Boolean(contextClaim));
  const snapshot = useClaimGraph(session, claimId, proofOpen || showReview || claimId === contextClaim);
  const graph = useMemo(
    () => (snapshot.data && snapshot.data.claim.id === claimId ? buildGraph(snapshot.data) : null),
    [claimId, snapshot.data],
  );
  const goal =
    graph?.nodes.get(`goal:${claimId}`) ??
    graph?.nodes.get(graph.order.find((id) => graph.nodes.get(id)?.kind === "goal") ?? "");
  const target = selection?.claimId === claimId ? selection?.nodeId : undefined;
  const selected = target && graph?.nodes.has(target) ? target : (goal?.id ?? graph?.order[0] ?? null);
  const node = selected ? graph?.nodes.get(selected) : undefined;
  const proofPathNode = node && goal && node.id !== goal.id ? node : null;
  const starterScope = claimId ? chatStarterScope(graph, node) : "project";
  useEffect(() => onChatStarterScope(starterScope), [onChatStarterScope, starterScope]);
  const assumption = graph?.hypotheses.find(({ id }) => id === assumptionId) ?? graph?.hypotheses[0];
  const basis = assumption?.reference?.basis_version_id;

  const rememberMap = useCallback(() => {
    if (!proofOpen) {
      beforeProof.current = {
        detailsOpen,
        selection: claimId ? { claimId, nodeId: target, referenced: selection?.referenced } : undefined,
      };
    }
  }, [claimId, detailsOpen, proofOpen, selection?.referenced, target]);

  useEffect(() => {
    if (!focus) return;
    pendingLaunch.current = false;
    const opensProof = Boolean(focus.proof || focus.nodeId);
    if (opensProof) rememberMap();
    else beforeProof.current = null;
    setSelection({ claimId: focus.claimId, nodeId: focus.nodeId, referenced: true });
    setDetailsOpen(true);
    setProofOpen(opensProof);
    setView("proof");
    setAssumption(null);
    setRecordedGoal(null);
    onFocusHandled(null);
  }, [focus, onFocusHandled, rememberMap]);

  const openProjectAction = useCallback((id: ProjectAction) => {
    if (id === "proof-task") setDetailsOpen(true);
    if (id === "organize-research") setImportFile(null);
    setProjectAction(id);
  }, []);
  /** #353: a file dropped on the map or chosen from its empty state opens the import with it. */
  const importDropped = useCallback((file: File) => {
    setImportFile(file);
    setProjectAction("organize-research");
  }, []);

  useEffect(() => {
    if (!actionRequest) return;
    if (actionRequest.id === "proof-task" && claimId !== actionRequest.claimId) {
      setSelection({ claimId: actionRequest.claimId, referenced: true });
    }
    openProjectAction(actionRequest.id);
    onActionHandled();
  }, [actionRequest, claimId, onActionHandled, openProjectAction]);

  useEffect(() => {
    if (!claimId) return;
    onPositionChange({
      claimId,
      nodeId: selected ?? target ?? `goal:${claimId}`,
      view,
      detailsOpen,
      proofOpen,
      assumption: assumption?.id ?? null,
      referenced: Boolean(selection?.referenced),
      mapReturn: proofOpen ? (beforeProof.current ?? undefined) : undefined,
    });
  }, [
    claimId,
    selected,
    target,
    view,
    detailsOpen,
    proofOpen,
    assumption?.id,
    selection?.referenced,
    onPositionChange,
  ]);

  useEffect(() => {
    if ((showReview || proofOpen) && claimId && selected) saveDesktopPosition({ claimId, nodeId: selected });
  }, [showReview, proofOpen, claimId, selected]);

  const impact = useQuery({
    queryKey: ["impact", claimId, basis],
    enabled: proofOpen && view === "impact" && Boolean(basis),
    queryFn: ({ signal }) =>
      api<ImpactPreview>(session, "/api/impact?" + new URLSearchParams({ claim: claimId!, basis: String(basis) }), {
        signal,
      }),
  });
  const impactPreview = impact.isError ? undefined : impact.data;
  const activity = useQuery({
    queryKey: ["activity", claimId],
    enabled: (proofOpen || activityOpen) && Boolean(graph),
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

  useEffect(() => {
    if (!copyNotice) return;
    const timer = window.setTimeout(() => setCopyNotice(undefined), 4200);
    return () => window.clearTimeout(timer);
  }, [copyNotice]);
  useEffect(() => {
    if (!linkFeedback || canvas.saving) return;
    const timer = window.setTimeout(() => setLinkFeedback(undefined), linkFeedback.removed ? 6500 : 4200);
    return () => window.clearTimeout(timer);
  }, [linkFeedback, canvas.saving]);

  const removedLink = linkFeedback?.session === session ? linkFeedback.removed : null;
  const canRestoreLink =
    removedLink &&
    !canvas.saving &&
    !canvas.loading &&
    !canvas.loadError &&
    [removedLink.source, removedLink.target].every((id) => canvas.claims?.some((claim) => claim.id === id)) &&
    !canvas.layout.links.some((link) => sameResearchLink(link, removedLink));

  const applyCanvas = (operation: ProjectCanvasOperation) => {
    if (operation.action === "move") return canvas.apply(operation);
    canvas.apply(operation, (layout) => {
      const exists = layout.links.some((link) => sameResearchLink(link, operation));
      if (operation.action === "unlink" && !exists) setLinkFeedback({ session, removed: operation });
      if (operation.action === "link" && exists) {
        setLinkFeedback((current) =>
          current?.session === session && sameResearchLink(current.removed, operation) ? undefined : current,
        );
      }
    });
  };

  const restoreLink = () => {
    if (!removedLink || !canRestoreLink) return;
    canvas.apply({ ...removedLink, action: "link" }, (layout) => {
      if (layout.links.some((link) => sameResearchLink(link, removedLink))) {
        setLinkFeedback({ session, removed: null });
      }
    });
  };

  const inspectClaim = (id: string, openDetails = true) => {
    if (claimId !== id) {
      if (proofOpen) beforeProof.current = null;
      setProofOpen(false);
      setActivityOpen(false);
      setView("proof");
      setAssumption(null);
    }
    setSelection({ claimId: id });
    setRecordedGoal(null);
    if (openDetails) setDetailsOpen(true);
  };
  const selectNode = (id: string) => {
    const next = graph?.nodes.get(id);
    if (!claimId || !next) return;
    setSelection({ claimId, nodeId: id, referenced: selection?.referenced });
    if (next.kind === "hypothesis" && next.reference?.basis_version_id) setAssumption(id);
  };
  const openReference = (id: string, nodeId: string | null) => {
    if (nodeId) rememberMap();
    else beforeProof.current = null;
    setSelection({ claimId: id, nodeId: nodeId ?? undefined, referenced: true });
    setDetailsOpen(true);
    setProofOpen(Boolean(nodeId));
    setView("proof");
    setAssumption(null);
    setRecordedGoal(null);
  };
  const handleContextClaim = (id: string | null) => {
    setContextClaim(id);
    if (id) inspectClaim(id, false);
  };

  const openProof = (id: string) => {
    rememberMap();
    inspectClaim(id);
    setProofOpen(true);
  };
  const closeProof = useCallback(() => {
    const previous = beforeProof.current;
    beforeProof.current = null;
    setProofOpen(false);
    setView("proof");
    if (previous) {
      setDetailsOpen(previous.detailsOpen);
      setSelection(previous.selection);
    }
    window.requestAnimationFrame(() => {
      const id = previous ? previous.selection?.claimId : claimId;
      const card = id && document.querySelector<HTMLButtonElement>(`[data-claim-id="${CSS.escape(id)}"]`);
      (card || document.getElementById("map-details-toggle"))?.focus({ preventScroll: true });
    });
  }, [claimId]);
  const closeInspector = useCallback(() => {
    setDetailsOpen(false);
    document.getElementById(proofOpen ? "proof-details-toggle" : "map-details-toggle")?.focus({ preventScroll: true });
  }, [proofOpen]);
  useEffect(() => {
    if (proofOpen) proofReturn.current?.focus({ preventScroll: true });
  }, [proofOpen]);

  // #274: the open proof lives in the address. Opening a claim, or a node after the claim's goal, adds a
  // history entry; choosing another node in the same proof replaces it. Back and forward reopen the address.
  const [addressNotice, setAddressNotice] = useState<"missingClaim" | "missingNode" | null>(null);
  const addressed = parseWorkbenchAddress(address);
  const addressKey = workbenchSub(addressed);
  // Archived claims can still be opened by reference, so the graph request, not the map's list, decides.
  const knownClaim = !(
    snapshot.isError &&
    snapshot.error instanceof ApiError &&
    (snapshot.error.status === 404 || snapshot.error.status === 400)
  );
  const shownNode = graph ? (selected && selected !== goal?.id ? selected : undefined) : target;
  const stateKey = workbenchSub(
    proofOpen && claimId && knownClaim
      ? { claimId, nodeId: shownNode && shownNode !== `goal:${claimId}` ? shownNode : undefined }
      : null,
  );
  const writtenState = useRef<string | null>(null);
  useEffect(() => {
    const previous = writtenState.current;
    if (previous === stateKey) return;
    writtenState.current = stateKey;
    if (!onAddress || stateKey === addressKey) return;
    const from = parseWorkbenchAddress(addressKey);
    const to = parseWorkbenchAddress(stateKey);
    const opens = !from !== !to || from?.claimId !== to?.claimId || (!from?.nodeId && Boolean(to?.nodeId));
    onAddress(to, previous !== null && knownClaim && opens ? "push" : "replace");
  }, [stateKey, addressKey, knownClaim, onAddress]);
  const readAddress = useRef(addressKey);
  useEffect(() => {
    if (readAddress.current === addressKey) return;
    readAddress.current = addressKey;
    if (addressKey === stateKey) return;
    setAddressNotice(null);
    if (!addressed) {
      closeProof();
      return;
    }
    if (!proofOpen) rememberMap();
    if (addressed.claimId !== claimId) {
      setActivityOpen(false);
      setView("proof");
      setAssumption(null);
      setRecordedGoal(null);
    }
    setSelection({ ...addressed, referenced: true });
    setDetailsOpen(true);
    setProofOpen(true);
  }, [addressKey, addressed, stateKey, claimId, proofOpen, closeProof, rememberMap]);
  // An address can outlive its claim or node: show what still exists and say why.
  useEffect(() => {
    if (!proofOpen || knownClaim) return;
    beforeProof.current = null;
    setProofOpen(false);
    setDetailsOpen(false);
    setSelection(undefined);
    setAddressNotice("missingClaim");
  }, [proofOpen, knownClaim]);
  const addressedNode = addressed?.nodeId;
  useEffect(() => {
    if (graph && target && target === addressedNode && !graph.nodes.has(target)) setAddressNotice("missingNode");
  }, [graph, target, addressedNode]);
  useEffect(() => {
    if (!proofOpen && !showReview) return;
    function escape(event: KeyboardEvent) {
      if (
        event.key !== "Escape" ||
        event.defaultPrevented ||
        event.isComposing ||
        document.querySelector("dialog[open]")
      )
        return;
      if (showReview && window.matchMedia("(width < 900px)").matches) {
        event.preventDefault();
        closeInspector();
      } else if (proofOpen) {
        event.preventDefault();
        closeProof();
      }
    }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [proofOpen, showReview, closeInspector, closeProof]);
  const openProofTask = (id: string) => {
    if (id !== claimId) inspectClaim(id);
    openProjectAction("proof-task");
  };

  const askAgent = (scope: Graph, target: GraphNode) => {
    onChatAnchor(
      {
        claim_id: scope.claim.id,
        statement_id: scope.statementId ?? undefined,
        node_id: target.kind === "goal" ? undefined : target.id,
      },
      target.kind === "goal" && scope.claim.title ? scope.claim.title : nodeTitle(t, target, { full: true }),
      chatStarterScope(scope, target),
    );
  };
  const writeNote = (id: string) => {
    selectNode(id);
    setDetailsOpen(true);
    setAllNodes(false);
    setActivityOpen(true);
    window.requestAnimationFrame(() => document.getElementById("guidance-text")?.focus());
  };
  const openNodeDetails = (id: string) => {
    selectNode(id);
    setDetailsOpen(true);
    window.requestAnimationFrame(() => document.getElementById("node-detail")?.focus());
  };
  const copy = async (text: string) => {
    const id = ++copySequence.current;
    let copied = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch {
      // Keep the menu action local and report browsers' clipboard denials.
    }
    if (id === copySequence.current) setCopyNotice({ id, copied });
  };
  const copyActions = (target?: GraphNode, unavailable?: string) =>
    graphCopyActions(t, { reference: target?.code, lean: target?.formalization, unavailable }, (text) => {
      void copy(text);
    });

  /** #353: claim-scoped research work, moved here from the map header's former Research actions menu. */
  function researchActions(id: string, scope: Graph | null, unavailable?: string): FloatingOption[] {
    const review = scope?.claim.premise_review;
    const premises = Boolean(review && (review.available || review.proposals.length > 0));
    const lemmas = Boolean(scope && lemmaPlanScope?.claimId === id);
    return [
      graphAction(
        t,
        "lemma-plan",
        lemmas ? () => setProjectAction("lemma-plan") : undefined,
        lemmas ? undefined : scope ? t("lemmaPlan.unavailable") : unavailable,
      ),
      graphAction(
        t,
        "premises",
        premises ? () => setPremiseDialog({}) : undefined,
        premises ? undefined : scope ? t("premise.unavailable") : unavailable,
      ),
      // Short codes need a database that records them, as in the palette.
      ...(scope?.claim.code || claims.data?.claim_codes
        ? [graphAction(t, "research-references", () => openProjectAction("research-references"))]
        : []),
    ];
  }

  function claimActions(id: string, link: FloatingOption, move: FloatingOption): FloatingGroup[] {
    const scope = graph?.claim.id === id ? graph : null;
    const target = scope ? goal : undefined;
    const unavailable = target
      ? undefined
      : t(scope ? "notice.noNodes" : snapshot.isError ? "graphActions.claimUnavailable" : "notice.loadingClaim");
    const hasLibrarySources = library.data?.items.some((item) => item.used_by.some((claim) => claim.id === id));
    const libraryLabel = hasLibrarySources
      ? t("graphActions.librarySources")
      : t(
          library.isError
            ? "graphActions.libraryUnavailable"
            : library.isFetching
              ? "common.loading"
              : "graphActions.noLibrary",
        );
    return [
      {
        options: [
          graphAction(t, "open-proof", () => openProof(id)),
          graphAction(t, "proof-task", scope && target ? () => openProofTask(id) : undefined, unavailable),
          graphAction(t, "ask-agent", scope && target ? () => askAgent(scope, target) : undefined, unavailable),
          link,
          move,
        ],
      },
      { options: researchActions(id, scope, unavailable) },
      { options: copyActions(target, unavailable) },
      {
        options: [
          graphAction(
            t,
            "show-library",
            hasLibrarySources ? () => (window.location.hash = href("library", "claim:" + id)) : undefined,
            libraryLabel,
          ),
        ],
      },
    ];
  }

  function nodeActions(id: string): FloatingGroup[] {
    const target = graph?.nodes.get(id);
    if (!target) return [];
    const canWriteNote = target.kind !== "proposed_premise";
    return [
      {
        options: [
          graphAction(t, "open-details", () => openNodeDetails(id)),
          graphAction(
            t,
            "write-note",
            canWriteNote ? () => writeNote(id) : undefined,
            canWriteNote ? undefined : t("graphActions.noNote"),
          ),
        ],
      },
      { options: copyActions(target) },
    ];
  }

  const changeView = (next: View) => {
    if (next === "impact" && assumption) selectNode(assumption.id);
    setView(next);
  };
  const openRecorded = async (id?: string) => {
    await queries.invalidateQueries();
    if (id) inspectClaim(id);
  };
  const showRecordedGoal = async (id: string) => {
    await queries.invalidateQueries();
    if (!proofOpen && !showReview) inspectClaim(id);
    setRecordedGoal(id);
  };
  const notice = !session
    ? t("session.missing")
    : claims.isError
      ? errorText(t, claims.error)
      : claims.isPending || canvas.loadingClaims
        ? t("notice.loadingProject")
        : "";
  const detailsNotice = snapshot.isError
    ? errorText(t, snapshot.error)
    : !graph
      ? t("notice.loadingClaim")
      : !node
        ? t("notice.noNodes")
        : target && !graph.nodes.has(target)
          ? t("claimCodes.nodeAbsent")
          : "";
  const workspaceState = claims.isError
    ? t("sidebar.readFailed")
    : claims.data
      ? t("sidebar.connected", { count: mapClaims.length })
      : t("sidebar.connecting");
  // The selected claim's graph: dialogs opened from its context menu act on it even with details closed.
  const actionsClaim = graph?.claim.id === claimId ? graph?.claim : undefined;
  const lean = goal?.reference;
  const lemmaPlanScope =
    actionsClaim?.lemma_plan_ready &&
    goal?.formalization &&
    typeof lean?.statement_id === "string" &&
    typeof lean.statement_version === "number" &&
    typeof lean.statement_sha256 === "string"
      ? {
          claimId: actionsClaim.id,
          title: actionsClaim.title || nodeTitle(t, goal),
          statementId: lean.statement_id,
          version: lean.statement_version,
          checksum: lean.statement_sha256,
          statement: goal.formalization,
        }
      : null;
  const right = liveRight ?? clamped.right;
  const proofHeading = (
    <nav className="resource-path proof-path" aria-label={t("navigation.resourcePath")}>
      <button
        ref={proofReturn}
        aria-label={t("canvasShell.map")}
        title={t("canvasShell.map")}
        type="button"
        className="conversation-text-action proof-return"
        onClick={() => closeProof()}
      >
        <span aria-hidden="true">‹</span> <span className="proof-return-label">{t("canvasShell.map")}</span>
      </button>
      <h1 title={graph?.claim.title ?? undefined} aria-current={proofPathNode ? undefined : "page"}>
        {proofPathNode && goal ? (
          <button type="button" className="resource-path-hop" onClick={() => selectNode(goal.id)}>
            {graph?.claim.title || t("graph.label")}
          </button>
        ) : (
          graph?.claim.title || t("graph.label")
        )}
      </h1>
      {proofPathNode && (
        <>
          <Chevron />
          <span
            className="resource-path-current"
            aria-current="page"
            title={nodeTitle(t, proofPathNode, { full: true })}
          >
            {nodeTitle(t, proofPathNode)}
          </span>
        </>
      )}
    </nav>
  );

  return (
    <div
      className="app"
      id="workbench"
      data-review={showReview ? "open" : "collapsed"}
      data-view={proofOpen ? "proof" : "project"}
      style={{ "--right-w": `${right}px` } as React.CSSProperties}
    >
      <main className="main">
        <PageHead
          heading={proofOpen ? proofHeading : <h1>{t("canvasShell.map")}</h1>}
          note={proofOpen ? undefined : t("canvasShell.mapNote")}
          actions={
            <ProjectActions
              session={session}
              claimId={(projectAction === "proof-task" ? claimId : actionsClaim?.id) ?? null}
              claimCode={actionsClaim?.code}
              lemmaPlanScope={lemmaPlanScope}
              claimTitle={actionsClaim?.title ?? ""}
              premiseReview={actionsClaim?.premise_review}
              premiseDialog={premiseDialog}
              projectAction={projectAction}
              importFile={importFile}
              onProjectAction={openProjectAction}
              onClose={() => {
                setPremiseDialog(null);
                setProjectAction(null);
                setImportFile(null);
              }}
              onPremiseRecorded={openRecorded}
              onRecorded={openRecorded}
              onGoalRecorded={showRecordedGoal}
              onOpenReference={openReference}
            />
          }
        />
        {notice && (
          <p className="notice" role="status" data-error={String(claims.isError)}>
            {notice}
          </p>
        )}
        {recordedGoal && (
          <div className="workbench-map-status" role="status">
            {t("canvasShell.goalAdded")}
            {claimId !== recordedGoal && (
              <button type="button" className="conversation-text-action" onClick={() => inspectClaim(recordedGoal)}>
                {t("canvasShell.viewOnMap")}
              </button>
            )}
          </div>
        )}
        {claims.data && !canvas.loadingClaims && (
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
            <div className="workbench-surfaces" data-proof-open={proofOpen}>
              <section
                className="graph-area project-map-area"
                inert={proofOpen}
                data-dropping={droppingFile || undefined}
                onDragOver={(event) => {
                  if (proofOpen || !draggingFiles(event)) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "copy";
                  setDroppingFile(true);
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDroppingFile(false);
                }}
                onDrop={(event) => {
                  if (!draggingFiles(event)) return;
                  event.preventDefault();
                  setDroppingFile(false);
                  const file = event.dataTransfer.files[0];
                  if (file) importDropped(file);
                }}
              >
                {droppingFile && (
                  <p className="map-drop-release" aria-hidden="true">
                    {t("mapDrop.release")}
                  </p>
                )}
                <ProjectCanvas
                  claims={mapClaims}
                  claimProgress={claimProgress}
                  status={workspaceState}
                  selected={mapSelection}
                  onSelect={inspectClaim}
                  onOpenProof={openProof}
                  active={!proofOpen}
                  onContextClaim={handleContextClaim}
                  claimActions={claimActions}
                  detailsOpen={showReview}
                  onDetailsChange={(open) => (open ? setDetailsOpen(true) : closeInspector())}
                  atlasClaims={atlas.data?.claims}
                  atlasOpen={mapAtlasOpen}
                  atlasBox={sizes.atlas}
                  atlasError={atlas.isError ? errorText(t, atlas.error) : undefined}
                  onToggleAtlas={() => setMapAtlasOpen((open) => !open)}
                  onAtlasBox={(box, persist) => layout.update({ atlas: box }, persist)}
                  layout={canvas.layout}
                  onLayout={applyCanvas}
                  layoutUnavailable={canvas.loading || Boolean(canvas.loadError)}
                  saving={canvas.saving}
                  empty={<MapDrop onFile={importDropped} onNewTheorem={() => openProjectAction("add-theorem")} />}
                />
              </section>
              {proofOpen && (
                <div className="proof-surface">
                  {graph && claimId ? (
                    <>
                      <div className="proof-tools">
                        <div
                          className="segmented proof-lenses"
                          role="group"
                          aria-label={t("views.label")}
                          aria-describedby={view === "references" ? "proof-references-description" : undefined}
                        >
                          {(["proof", "references", "impact"] as const).map((lens) => (
                            <button
                              key={lens}
                              type="button"
                              aria-pressed={view === lens}
                              onClick={() => changeView(lens)}
                            >
                              {t(`views.${lens}`)}
                            </button>
                          ))}
                        </div>
                        <button
                          id="proof-details-toggle"
                          type="button"
                          className="button"
                          aria-label={t("canvasShell.details")}
                          title={t("canvasShell.details")}
                          aria-expanded={showReview}
                          aria-controls="review"
                          onClick={() => setDetailsOpen(!showReview)}
                        >
                          <WorkbenchIcon name="details" />
                          <span>{t("canvasShell.details")}</span>
                        </button>
                      </div>
                      {view === "references" && (
                        <p id="proof-references-description" className="proof-view-description">
                          {t("views.referencesDescription")}
                        </p>
                      )}
                      {view === "impact" && (
                        <div
                          className="impact-bar"
                          data-error={impact.isError && !impact.isFetching ? "true" : undefined}
                        >
                          {graph.hypotheses.length > 0 && (
                            <>
                              <label htmlFor="premise-select">{t("impact.preview")}</label>
                              <select
                                id="premise-select"
                                value={assumption?.id ?? ""}
                                onChange={(event) => selectNode(event.target.value)}
                              >
                                {graph.hypotheses.map((premise) => (
                                  <option key={premise.id} value={premise.id}>
                                    {nodeTitle(t, premise)}
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
                      {graph.order.length === 1 && goal && view === "proof" && (
                        <p className="main-proof-empty">{t("projectCanvas.proofEmpty")}</p>
                      )}
                      {graph.order.length > 0 && (
                        <section className="graph-area main-proof-graph" aria-label={t("graph.label")}>
                          <GraphView
                            key={claimId}
                            graph={graph}
                            view={view}
                            selected={selected}
                            assumption={assumption?.id ?? null}
                            impact={impactPreview}
                            work={work.byNode}
                            running={work.running}
                            queued={work.queued}
                            cardSizes={sizes.cards[`${claimId}:${view}`] ?? NO_CARD_SIZES}
                            onResizeCard={(id, size) => layout.resizeCard(`${claimId}:${view}`, id, size)}
                            onSelect={selectNode}
                            nodeActions={nodeActions}
                          />
                        </section>
                      )}
                    </>
                  ) : (
                    <p className="notice" role={snapshot.isError ? "alert" : "status"}>
                      {detailsNotice || t("notice.loadingClaim")}
                    </p>
                  )}
                </div>
              )}
            </div>
          </>
        )}
        <div className="workbench-action-feedback" aria-live="polite">
          <div className="workbench-action-feedback-stack">
            {addressNotice && (
              <ActionFeedback
                message={t(`workbenchAddress.${addressNotice}`)}
                onDismiss={() => setAddressNotice(null)}
              />
            )}
            {copyNotice && (
              <ActionFeedback
                key={copyNotice.id}
                message={t(copyNotice.copied ? "graphActions.copied" : "graphActions.copyFailed")}
                onDismiss={() => setCopyNotice(undefined)}
              />
            )}
            {linkFeedback?.session === session && (
              <ActionFeedback
                message={t(removedLink ? "projectCanvas.linkRemoved" : "projectCanvas.linkRestored")}
                action={
                  removedLink
                    ? { label: t("projectCanvas.undo"), disabled: !canRestoreLink, run: restoreLink }
                    : undefined
                }
                onDismiss={() => setLinkFeedback(undefined)}
              />
            )}
          </div>
        </div>
      </main>
      {showReview && (
        <>
          <Splitter
            name="right"
            value={right}
            label={t("splitter.right")}
            onResize={(value) => setLiveRight(clampSize("right", value, layout.windowHeight))}
            onCommit={(value) => {
              setLiveRight(undefined);
              layout.update({ right: clampSize("right", value, layout.windowHeight) });
            }}
          />
          <ReviewPanel
            graph={graph}
            claims={mapClaims}
            claimId={claimId}
            claimProgress={claimId ? claimProgress.get(claimId) : undefined}
            claimReviews={claimId ? claimReviews.get(claimId) : undefined}
            view={view}
            selected={selected}
            work={selected ? work.byNode.get(selected) : undefined}
            assumption={assumption?.id ?? null}
            impact={impactPreview}
            impactLoading={impact.isFetching}
            impactError={impact.isError ? errorText(t, impact.error) : ""}
            notice={detailsNotice ? { text: detailsNotice, error: snapshot.isError } : undefined}
            actions={
              view === "impact" || !graph || !node
                ? null
                : {
                    primary: graphAction(t, "proof-task", () => openProofTask(graph.claim.id)),
                    secondary: [
                      ...(!proofOpen && claimId ? [graphAction(t, "open-proof", () => openProof(claimId))] : []),
                      graphAction(t, "ask-agent", () => askAgent(graph, node)),
                    ],
                  }
            }
            onSelect={selectNode}
            onReviewPremise={(id) => setPremiseDialog({ id })}
            onClose={closeInspector}
          >
            {graph && claimId && (
              <details
                className="inspector-disclosure inspector-activity"
                open={activityOpen}
                onToggle={(event) => setActivityOpen(event.currentTarget.open)}
              >
                <summary>{t("conversations.proofActivity")}</summary>
                <ProofActivity
                  session={session}
                  graph={graph}
                  claimId={claimId}
                  selected={node?.kind === "proposed_premise" ? null : selected}
                  activity={activity.data}
                  activityError={activityError}
                  running={work.running}
                  queued={work.queued}
                  active={activityOpen}
                  allNodes={allNodes}
                  onAllNodes={() => setAllNodes(!allNodes)}
                  onSelect={selectNode}
                  onSent={() => void queries.invalidateQueries({ queryKey: ["activity", claimId] })}
                />
              </details>
            )}
          </ReviewPanel>
        </>
      )}
    </div>
  );
}
