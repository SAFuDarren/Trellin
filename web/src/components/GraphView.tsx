import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Claim } from "../api/client";
import {
  CARD,
  clampCardSize,
  highlights,
  impactItems,
  impactLineKey,
  layoutGraph,
  workLabel,
  type CardSize,
  type Graph,
  type Layout,
  type Point,
  type NodeWork,
  type View,
} from "../workbench/graph";
import { atlasShape, ATLAS_COLOURS } from "../workbench/atlas";
import { kindText, nodeStatus, nodeSub, nodeTitle, nodeTone, summarize, type Tone } from "../workbench/text";
import type { AtlasClaim, ImpactPreview } from "../workbench/types";
import type { AtlasBox, CardSizes } from "../workbench/useLayout";
import { Clock, Mark, Spin } from "./Icons";

const PROJECT_CLAIM_HEIGHT = 64;
const PROJECT_CLAIM_GAP = 8;
const PROJECT_CLAIM_HEADING = 24;
const NO_PROJECT_CLAIMS: Claim[] = [];

type Camera = { x: number; y: number; k: number };
type ResizePreview = { id: string; anchor: Point; size: CardSize };
type ResizeDrag = ResizePreview & { pointerId: number; x: number; y: number; zoom: number };

function anchoredCamera(camera: Camera, layout: Layout, id: string, anchor: Point): Camera {
  const point = layout.points.get(id);
  if (!point) return camera;
  return {
    ...camera,
    x: camera.x + (anchor.x - point.x) * camera.k,
    y: camera.y + (anchor.y - point.y) * camera.k,
  };
}

function dragSize(drag: ResizeDrag, event: React.PointerEvent): CardSize {
  return clampCardSize({
    width: drag.size.width + (event.clientX - drag.x) / drag.zoom,
    height: drag.size.height + (event.clientY - drag.y) / drag.zoom,
  });
}

type Props = {
  graph: Graph;
  view: View;
  selected: string | null;
  assumption: string | null;
  impact: ImpactPreview | undefined;
  work: Map<string, NodeWork>;
  running: number;
  queued: number;
  projectClaims: Claim[];
  recordedGoal: string | null;
  onOpenClaim: (claimId: string) => void;
  atlasClaims: AtlasClaim[];
  claimId: string;
  atlasOpen: boolean;
  atlasBox: AtlasBox | null;
  onAtlasBox: (box: AtlasBox, persist: boolean) => void;
  cardSizes: CardSizes;
  onResizeCard: (id: string, size: CardSize) => void;
  onSelect: (id: string) => void;
  onView: (view: View) => void;
};

export function GraphView(props: Props) {
  const { t } = useTranslation();
  const { graph, view, selected, assumption, impact, work } = props;
  const stage = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<string, HTMLButtonElement>());
  const [heights, setHeights] = useState(new Map<string, number>());
  const drag = useRef<ResizeDrag | null>(null);
  const [resizing, setResizing] = useState<ResizePreview | null>(null);
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, k: 1 });
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  const moved = useRef(false);
  const revealedGoal = useRef<string | null>(null);
  const [panning, setPanning] = useState(false);

  const affected = useMemo(() => (view === "impact" ? impactItems(graph, impact) : new Map()), [graph, view, impact]);
  const lit = useMemo(
    () => highlights(graph, view, selected, assumption, affected),
    [graph, view, selected, assumption, affected],
  );
  const sizes = useMemo(
    () =>
      new Map(
        graph.order.map((id) => [
          id,
          props.cardSizes[id] ?? {
            width: CARD.width,
            height: heights.get(id) ?? CARD.height,
          },
        ]),
      ),
    [graph.order, props.cardSizes, heights],
  );
  const layout = useMemo(
    () => layoutGraph(graph, resizing ? new Map(sizes).set(resizing.id, resizing.size) : sizes),
    [graph, sizes, resizing],
  );
  const projectClaims = view === "proof" ? props.projectClaims : NO_PROJECT_CLAIMS;
  // The canvas can show other goals. Its proof positions, paths and scope stay claim-specific.
  const canvasWidth = layout.width + (projectClaims.length ? CARD.width + CARD.pad / 2 : 0);
  const projectHeight =
    CARD.pad * 2 + PROJECT_CLAIM_HEADING + projectClaims.length * (PROJECT_CLAIM_HEIGHT + PROJECT_CLAIM_GAP);
  const canvasHeight = projectClaims.length ? Math.max(layout.height, projectHeight) : layout.height;
  const positioned = resizing ? anchoredCamera(camera, layout, resizing.id, resizing.anchor) : camera;

  // Card heights decide where edges meet them; measure after every render that may change a card.
  // Runs after every render on purpose; it only sets state when a height actually changed.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (resizing) return;
    const next = new Map<string, number>();
    let changed = false;
    for (const id of graph.order) {
      const height = cards.current.get(id)?.offsetHeight || CARD.height;
      next.set(id, height);
      if (heights.get(id) !== height) changed = true;
    }
    if (changed || next.size !== heights.size) setHeights(next);
  });

  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      setStageSize({ width: element.clientWidth, height: element.clientHeight }),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const box = atlasPlacement(props.atlasBox, stageSize.width, stageSize.height);
  const atlasVisible = props.atlasOpen && props.atlasClaims.length > 0;

  const fit = useCallback(
    (includeProject = false) => {
      const { width, height } = stageSize;
      if (!width || !height) return;
      const fitWidth = includeProject ? canvasWidth : layout.width;
      const fitHeight = includeProject ? canvasHeight : layout.height;
      // Leave the overview map uncovered when there is room beside it; otherwise the graph may pass under it.
      const mapRight = !atlasVisible || box.y > height / 2 ? 0 : box.x + box.w + 12;
      const inset = mapRight && mapRight < width * 0.4 ? mapRight : 0;
      const k = Math.max(0.3, Math.min(1.25, (width - inset - 32) / fitWidth, (height - 56) / fitHeight));
      setCamera({
        k,
        x: inset + (width - inset - fitWidth * k) / 2,
        y: Math.max(8, (height - fitHeight * k) / 2),
      });
    },
    [stageSize, canvasWidth, canvasHeight, layout.width, layout.height, atlasVisible, box.x, box.y, box.w],
  );

  // A new claim gets a fresh view; otherwise the camera stays where the researcher left it.
  useEffect(() => {
    moved.current = false;
  }, [graph.claim.id]);
  useEffect(() => {
    if (!moved.current) fit(true);
  }, [fit]);
  useEffect(() => {
    if (!props.recordedGoal) {
      revealedGoal.current = null;
    } else if (
      props.recordedGoal !== revealedGoal.current &&
      stageSize.width &&
      stageSize.height &&
      projectClaims.some((claim) => claim.id === props.recordedGoal)
    ) {
      revealedGoal.current = props.recordedGoal;
      fit(true);
    }
  }, [fit, projectClaims, props.recordedGoal, stageSize]);

  const zoomAt = (factor: number, cx = stageSize.width / 2, cy = stageSize.height / 2) => {
    moved.current = true;
    setCamera(({ x, y, k }) => {
      const next = Math.max(0.3, Math.min(2.5, k * factor));
      return { k: next, x: cx - ((cx - x) * next) / k, y: cy - ((cy - y) * next) / k };
    });
  };
  const panBy = (dx: number, dy: number) => {
    moved.current = true;
    setCamera((current) => ({ ...current, x: current.x + dx, y: current.y + dy }));
  };

  // Wheel events must be non-passive to stop the page from scrolling.
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (drag.current) return;
      if (event.ctrlKey || event.metaKey) {
        const bounds = element.getBoundingClientRect();
        const factor = Math.exp(-event.deltaY * 0.01);
        const cx = event.clientX - bounds.left;
        const cy = event.clientY - bounds.top;
        moved.current = true;
        setCamera(({ x, y, k }) => {
          const next = Math.max(0.3, Math.min(2.5, k * factor));
          return { k: next, x: cx - ((cx - x) * next) / k, y: cy - ((cy - y) * next) / k };
        });
      } else {
        moved.current = true;
        setCamera((current) => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }));
      }
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, []);

  // Keep the selected card on screen when it is chosen from elsewhere.
  useEffect(() => {
    if (!selected) return;
    const point = layout.points.get(selected);
    const { width, height } = stageSize;
    const size = sizes.get(selected);
    if (!point || !size || !width || !height) return;
    setCamera((current) => {
      const left = current.x + point.x * current.k;
      const top = current.y + point.y * current.k;
      const right = left + size.width * current.k;
      const bottom = top + size.height * current.k;
      if (left >= 0 && top >= 0 && right <= width && bottom <= height) return current;
      return {
        ...current,
        x: current.x + width / 2 - (left + right) / 2,
        y: current.y + height / 2 - (top + bottom) / 2,
      };
    });
    // Only react to a new selection, not to every layout change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const commitResize = (id: string, size: CardSize, anchor: Point) => {
    const original = sizes.get(id);
    if (!original || (original.width === size.width && original.height === size.height)) return;
    const next = layoutGraph(graph, new Map(sizes).set(id, size));
    moved.current = true;
    setCamera((current) => anchoredCamera(current, next, id, anchor));
    props.onResizeCard(id, size);
  };
  const startResize = (event: React.PointerEvent<HTMLButtonElement>, id: string) => {
    if (event.button !== 0 || drag.current) return;
    const anchor = layout.points.get(id);
    const size = sizes.get(id);
    if (!anchor || !size) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id, size, anchor, pointerId: event.pointerId, x: event.clientX, y: event.clientY, zoom: camera.k };
  };
  const moveResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    moved.current = true;
    setResizing({ id: active.id, anchor: active.anchor, size: dragSize(active, event) });
  };
  const finishResize = (event: React.PointerEvent<HTMLButtonElement>, save: boolean) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    setResizing(null);
    if (save) commitResize(active.id, dragSize(active, event), active.anchor);
  };
  const resizeByKey = (event: React.KeyboardEvent<HTMLButtonElement>, id: string) => {
    const step = event.shiftKey ? 36 : 12;
    const delta = (
      {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
      } as Record<string, [number, number]>
    )[event.key];
    const anchor = layout.points.get(id);
    const size = sizes.get(id);
    if (!delta || !anchor || !size || drag.current) return;
    event.preventDefault();
    commitResize(id, clampCardSize({ width: size.width + delta[0], height: size.height + delta[1] }), anchor);
  };

  const startPan = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as Element).closest("button, a, select, input, textarea, figure, .zoom"))
      return;
    const element = event.currentTarget;
    const start = { x: event.clientX, y: event.clientY, camera };
    element.setPointerCapture(event.pointerId);
    setPanning(true);
    const move = (moveEvent: PointerEvent) => {
      moved.current = true;
      setCamera({
        ...start.camera,
        x: start.camera.x + moveEvent.clientX - start.x,
        y: start.camera.y + moveEvent.clientY - start.y,
      });
    };
    const stop = () => {
      element.removeEventListener("pointermove", move);
      element.removeEventListener("pointerup", stop);
      element.removeEventListener("pointercancel", stop);
      setPanning(false);
    };
    element.addEventListener("pointermove", move);
    element.addEventListener("pointerup", stop);
    element.addEventListener("pointercancel", stop);
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== stage.current) return;
    const pan = (
      { ArrowLeft: [48, 0], ArrowRight: [-48, 0], ArrowUp: [0, 48], ArrowDown: [0, -48] } as Record<
        string,
        [number, number]
      >
    )[event.key];
    if (pan) panBy(...pan);
    else if (event.key === "+" || event.key === "=") zoomAt(1.2);
    else if (event.key === "-") zoomAt(1 / 1.2);
    else if (event.key === "0") {
      moved.current = false;
      fit();
    } else return;
    event.preventDefault();
  };

  const isImpact = view === "impact";
  return (
    <>
      <div
        ref={stage}
        className="stage"
        tabIndex={0}
        role="application"
        aria-roledescription={t("stage.roleDescription")}
        aria-label={t("stage.label")}
        data-panning={panning ? "true" : undefined}
        onPointerDown={startPan}
        onKeyDown={onKeyDown}
      >
        <div
          className="plot"
          style={{
            width: canvasWidth,
            height: canvasHeight,
            transform: `translate(${positioned.x}px, ${positioned.y}px) scale(${positioned.k})`,
          }}
        >
          <svg className="graph-edges" aria-hidden="true" viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}>
            {graph.edges.map((edge) => (
              <path
                key={edge.key}
                className="graph-edge"
                data-kind={edge.drawKind}
                data-emphasis={lit.edges.has(edge) ? "strong" : "dim"}
                d={layout.paths.get(edge.key) ?? ""}
              />
            ))}
          </svg>
          <div className="graph-nodes">
            {graph.order.map((id) => {
              const node = graph.nodes.get(id)!;
              const point = layout.points.get(id) ?? { x: 0, y: 0 };
              const blocked = graph.blockers.has(id);
              const effect = affected.get(id);
              let caption = blocked ? t("node.blocked") : nodeStatus(t, node);
              if (isImpact && id === assumption) caption = t("impact.previewLabel");
              else if (isImpact && effect) caption = t(impactLineKey(effect));
              const nodeWork = work.get(id);
              const running = nodeWork?.running.size
                ? workLabel(nodeWork.running, t("chat.listSeparator", { defaultValue: ", " }))
                : "";
              // The badge shortens the agent names but always shows how many jobs run.
              const runningNames = nodeWork?.running.size
                ? [...nodeWork.running.keys()].join(t("chat.listSeparator", { defaultValue: ", " }))
                : "";
              const runningCount = nodeWork ? [...nodeWork.running.values()].reduce((sum, n) => sum + n, 0) : 0;
              const queued =
                !running && nodeWork?.queued.size
                  ? t("graph.queued", {
                      work: workLabel(nodeWork.queued, t("chat.listSeparator", { defaultValue: ", " })),
                    })
                  : "";
              const title = nodeTitle(t, node, { full: true });
              const sub = nodeSub(t, node, title);
              const customSize = resizing?.id === id ? resizing.size : props.cardSizes[id];
              const titleLines = customSize
                ? Math.max(1, Math.floor((customSize.height - (running || queued ? 88 : 68)) / 18) + 1)
                : 1;
              return (
                <div
                  key={id}
                  className="graph-node-frame"
                  style={{ left: point.x, top: point.y, width: customSize?.width ?? CARD.width }}
                >
                  <button
                    ref={(element) => {
                      if (element) cards.current.set(id, element);
                      else cards.current.delete(id);
                    }}
                    type="button"
                    className="graph-node"
                    data-node={id}
                    data-kind={node.kind}
                    data-emphasis={!isImpact || lit.nodes.has(id) ? "strong" : "dim"}
                    data-affected={String(isImpact && (Boolean(effect) || id === assumption))}
                    data-working={String(Boolean(running))}
                    aria-pressed={id === selected}
                    aria-controls="node-detail"
                    aria-label={[title, kindText(t, node.kind), caption, running || queued].filter(Boolean).join(", ")}
                    title={title}
                    data-expanded-title={titleLines > 1 ? "true" : undefined}
                    style={{ height: customSize?.height, "--title-lines": titleLines } as React.CSSProperties}
                    onClick={() => props.onSelect(id)}
                  >
                    <span className="node-head">
                      <Mark tone={nodeTone(node, blocked)} shape={node.kind === "hypothesis" ? "square" : undefined} />
                      <span className="node-title">{title}</span>
                    </span>
                    <span className="node-sub" data-math={String(sub.math)}>
                      {sub.text}
                    </span>
                    <span className="node-caption">
                      <span className="node-code">{graph.codes.get(id)}</span>
                      {caption}
                    </span>
                    {(running || queued) && (
                      <span className="work" data-state={running ? "running" : "queued"}>
                        {running ? <Spin /> : <Clock />}
                        <span className="work-text">{running ? runningNames : queued}</span>
                        {running && runningCount > 1 && <span className="work-count">{"×" + runningCount}</span>}
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    className="graph-node-resize"
                    aria-label={t("stage.resizeNode", { title })}
                    aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown"
                    aria-description={t("stage.resizeHelp")}
                    title={t("stage.resizeHelp")}
                    onPointerDown={(event) => startResize(event, id)}
                    onPointerMove={moveResize}
                    onPointerUp={(event) => finishResize(event, true)}
                    onPointerCancel={(event) => finishResize(event, false)}
                    onLostPointerCapture={(event) => finishResize(event, false)}
                    onKeyDown={(event) => resizeByKey(event, id)}
                  >
                    <svg viewBox="0 0 12 12" aria-hidden="true">
                      <path d="M2 10 10 2M6 10l4-4" />
                    </svg>
                  </button>
                </div>
              );
            })}
          </div>
          {projectClaims.length > 0 && (
            <section
              className="canvas-project-claims"
              aria-label={t("graph.projectClaims")}
              style={{ left: layout.width - CARD.pad / 2, top: CARD.pad, width: CARD.width, gap: PROJECT_CLAIM_GAP }}
            >
              <h2 className="canvas-project-heading" style={{ height: PROJECT_CLAIM_HEADING }}>
                {t("graph.projectClaims")}
              </h2>
              {projectClaims.map((claim) => {
                const node = {
                  id: `goal:${claim.id}`,
                  kind: "goal",
                  title: claim.title ?? undefined,
                  status: claim.status,
                };
                const title = nodeTitle(t, node, { full: true });
                const status = nodeStatus(t, node);
                return (
                  <button
                    key={claim.id}
                    className="canvas-project-claim"
                    data-node={node.id}
                    title={title}
                    aria-label={t("graph.openClaim", { title, status })}
                    type="button"
                    style={{ height: PROJECT_CLAIM_HEIGHT }}
                    onClick={() => props.onOpenClaim(claim.id)}
                  >
                    <span className="node-head">
                      <Mark tone={nodeTone(node, false)} />
                      <span className="node-title">{title}</span>
                    </span>
                    <span className="node-caption">{status}</span>
                  </button>
                );
              })}
            </section>
          )}
        </div>
        <div className="segmented" role="group" aria-label={t("views.label")}>
          {(["proof", "references", "impact"] as const).map((key) => (
            <button key={key} type="button" aria-pressed={view === key} onClick={() => props.onView(key)}>
              {t(`views.${key}`)}
            </button>
          ))}
        </div>
        {atlasVisible && (
          <AtlasMap
            claims={props.atlasClaims}
            claimId={props.claimId}
            box={box}
            maximumWidth={atlasMaximumWidth(stageSize.width)}
            onBox={props.onAtlasBox}
          />
        )}
        <div className="zoom" role="group" aria-label={t("stage.zoom")}>
          <button type="button" aria-label={t("stage.zoomOut")} onClick={() => zoomAt(1 / 1.2)}>
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M3 7h8" />
            </svg>
          </button>
          <button
            type="button"
            className="zoom-level"
            aria-label={t("stage.zoomReset")}
            onClick={() => zoomAt(1 / camera.k)}
          >
            {Math.round(camera.k * 100)}%
          </button>
          <button type="button" aria-label={t("stage.zoomIn")} onClick={() => zoomAt(1.2)}>
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M3 7h8M7 3v8" />
            </svg>
          </button>
          <span className="zoom-rule" aria-hidden="true" />
          <button
            type="button"
            aria-label={t("stage.fit")}
            onClick={() => {
              moved.current = false;
              fit();
            }}
          >
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M2 5V2h3M9 2h3v3M12 9v3H9M5 12H2V9" />
            </svg>
          </button>
        </div>
      </div>
      <Legend
        graph={graph}
        view={view}
        assumption={assumption}
        affected={affected}
        running={props.running}
        queued={props.queued}
      />
    </>
  );
}

const ATLAS_MIN_WIDTH = 110;
function atlasMaximumWidth(stageWidth: number) {
  return stageWidth ? Math.max(ATLAS_MIN_WIDTH, Math.min(420, stageWidth - 24)) : 420;
}

// Until moved, the map sits top-left on a wide canvas and as a small map bottom-left on a narrow one.
function atlasPlacement(stored: AtlasBox | null, width: number, height: number): AtlasBox {
  const base =
    stored ?? (width && width < 640 ? { x: 12, y: Math.max(56, height - 128), w: 124 } : { x: 18, y: 14, w: 186 });
  const w = Math.round(Math.max(ATLAS_MIN_WIDTH, Math.min(atlasMaximumWidth(width), base.w)));
  if (!width || !height) return { ...base, w };
  const tall = w * 0.95;
  return {
    w,
    x: Math.round(Math.max(0, Math.min(width - w, base.x))),
    y: Math.round(Math.max(0, Math.min(height - Math.min(tall, height), base.y))),
  };
}

function AtlasMap({
  claims,
  claimId,
  box,
  maximumWidth,
  onBox,
}: {
  claims: AtlasClaim[];
  claimId: string;
  box: AtlasBox;
  maximumWidth: number;
  onBox: (box: AtlasBox, persist: boolean) => void;
}) {
  const { t } = useTranslation();
  const shape = useMemo(() => atlasShape(claims, claimId), [claims, claimId]);
  const [moving, setMoving] = useState(false);
  const drag = (event: React.PointerEvent<HTMLElement>, mode: "move" | "resize") => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget;
    const start = { x: event.clientX, y: event.clientY, box };
    let latest = box;
    target.setPointerCapture(event.pointerId);
    setMoving(true);
    const move = (moveEvent: PointerEvent) => {
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      latest =
        mode === "move"
          ? { ...start.box, x: start.box.x + dx, y: start.box.y + dy }
          : { ...start.box, w: start.box.w + Math.max(dx, (dy * 4) / 3) };
      onBox(latest, false);
    };
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
      setMoving(false);
      onBox(latest, true);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
  };
  const onResizeKey = (event: React.KeyboardEvent) => {
    const step = event.shiftKey ? 48 : 16;
    const change = ({ ArrowRight: step, ArrowDown: step, ArrowLeft: -step, ArrowUp: -step } as Record<string, number>)[
      event.key
    ];
    if (change === undefined) return;
    event.preventDefault();
    onBox({ ...box, w: box.w + change }, true);
  };
  return (
    <figure
      className="atlas"
      data-small={String(box.w < 160)}
      data-moving={moving ? "true" : undefined}
      style={{ "--atlas-x": `${box.x}px`, "--atlas-y": `${box.y}px`, "--atlas-w": `${box.w}px` } as React.CSSProperties}
    >
      <figcaption title={t("atlas.move")} onPointerDown={(event) => drag(event, "move")}>
        <span>{t("atlas.title")}</span>
        <span>{t("atlas.nodes", { count: shape.nodeCount })}</span>
      </figcaption>
      <svg viewBox="0 0 200 150" role="img" aria-label={t("atlas.label")}>
        {shape.groups.map((group) => {
          const colour = group.index < ATLAS_COLOURS ? `var(--cat-${group.index + 1})` : "var(--edge-soft)";
          return (
            <g
              key={group.claimId}
              className={group.current ? "current" : ""}
              stroke={colour}
              fill={colour}
              opacity={group.current ? undefined : 0.45}
            >
              {group.lines.map(([x1, y1, x2, y2], position) => (
                <line
                  key={`l${position}`}
                  x1={x1.toFixed(1)}
                  y1={y1.toFixed(1)}
                  x2={x2.toFixed(1)}
                  y2={y2.toFixed(1)}
                />
              ))}
              {group.dots.map(([cx, cy, r], position) => (
                <circle key={`d${position}`} cx={cx.toFixed(1)} cy={cy.toFixed(1)} r={r} stroke="none" />
              ))}
            </g>
          );
        })}
        <circle cx={100} cy={75} r={2.6} fill="var(--ink)" stroke="none" />
        {shape.here && <circle cx={shape.here.x.toFixed(1)} cy={shape.here.y.toFixed(1)} r={11} className="here" />}
      </svg>
      <div className="atlas-keys">
        {shape.keys.map((key) => (
          <span
            key={key.claimId}
            data-current={String(key.current)}
            title={key.title ?? ""}
            style={{ "--c": `var(--cat-${key.index + 1})` } as React.CSSProperties}
          >
            <i aria-hidden="true" />
            {summarize(key.title || t("atlas.untitled"), 5).replace(" …", "…")}
          </span>
        ))}
        {shape.more > 0 && <span>+{shape.more}</span>}
      </div>
      <span
        className="atlas-resize"
        role="separator"
        tabIndex={0}
        aria-valuemin={ATLAS_MIN_WIDTH}
        aria-valuemax={maximumWidth}
        aria-valuenow={box.w}
        aria-label={t("atlas.resize")}
        title={t("atlas.resize")}
        onPointerDown={(event) => drag(event, "resize")}
        onKeyDown={onResizeKey}
      />
    </figure>
  );
}

function Legend({
  graph,
  view,
  assumption,
  affected,
  running,
  queued,
}: {
  graph: Graph;
  view: View;
  assumption: string | null;
  affected: Map<string, unknown>;
  running: number;
  queued: number;
}) {
  const { t } = useTranslation();
  const kinds = new Set(graph.edges.map((edge) => edge.drawKind));
  const counts = new Map<Tone, number>();
  for (const [id, node] of graph.nodes) {
    const tone =
      view === "impact" && (affected.has(id) || id === assumption) ? "bad" : nodeTone(node, graph.blockers.has(id));
    if (tone) counts.set(tone, (counts.get(tone) ?? 0) + 1);
  }
  const lines = (["actual", "planned", "context"] as const).filter((kind) => kinds.has(kind));
  const tones = (["ok", "review", "bad", "open", "planned"] as const).filter((tone) => counts.get(tone));
  return (
    <div className="legend" role="status" aria-label={t("legend.label")}>
      {lines.map((kind) => (
        <span key={kind}>
          <i data-kind={kind} />
          {t(`edge.${kind}`)}
        </span>
      ))}
      {lines.length > 0 && <span className="rule" />}
      {tones.map((tone) => (
        <span key={tone}>
          <Mark tone={tone} />
          {tone === "bad" && view === "impact" ? t("legend.badImpact") : t(`tone.${tone}`)}
          <b>{counts.get(tone)}</b>
        </span>
      ))}
      {(running > 0 || queued > 0) && <span className="rule" />}
      {running > 0 && (
        <span>
          <Spin />
          {t("legend.running")}
          <b>{running}</b>
        </span>
      )}
      {queued > 0 && (
        <span>
          <Clock />
          {t("legend.queued")}
          <b>{queued}</b>
        </span>
      )}
    </div>
  );
}
