import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { formatShortcut } from "../shortcuts";
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
import { kindText, nodeStatus, nodeTitle, nodeTone, type Tone } from "../workbench/text";
import type { ImpactPreview } from "../workbench/types";
import type { CardSizes } from "../workbench/useLayout";
import { ContextMenu, contextMenuProps, Popover, type ContextMenuAnchor, type FloatingGroup } from "./FloatingControls";
import { Chevron, Clock, Mark, Spin } from "./Icons";

type Camera = { x: number; y: number; k: number };
type CameraMode = "readable" | "fit" | "manual";
const READABLE_SCALE = 0.8;
const OVERVIEW_SCALE = 0.62;
const STAGE_INSET = 12;
const ZOOM_SPACE = 48;
type ResizePreview = { id: string; anchor: Point; size: CardSize };
type ResizeDrag = ResizePreview & { pointerId: number; x: number; y: number; zoom: number };
type NodeMenu = { id: string; claimId: string; view: View; anchor: ContextMenuAnchor };

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
  cardSizes: CardSizes;
  onResizeCard: (id: string, size: CardSize) => void;
  onSelect: (id: string) => void;
  nodeActions: (id: string) => FloatingGroup[];
};

export function GraphView(props: Props) {
  const { t } = useTranslation();
  const resizeHelp = t("stage.resizeHelp", { shortcut: formatShortcut("Shift") });
  const { graph, view, selected, assumption, impact, work } = props;
  const stage = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<string, HTMLButtonElement>());
  const [heights, setHeights] = useState(new Map<string, number>());
  const drag = useRef<ResizeDrag | null>(null);
  const [resizing, setResizing] = useState<ResizePreview | null>(null);
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, k: 1 });
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  const cameraMode = useRef<CameraMode>("readable");
  const initialNode = useRef(selected ?? graph.focusId ?? graph.order[0]);
  const [panning, setPanning] = useState(false);
  const [nodeMenu, setNodeMenu] = useState<NodeMenu | null>(null);
  const currentMenu =
    nodeMenu?.claimId === graph.claim.id && nodeMenu.view === view && nodeMenu.id === selected ? nodeMenu : null;
  const menuNode = currentMenu ? graph.nodes.get(currentMenu.id) : undefined;

  useEffect(() => {
    if (nodeMenu && !menuNode) setNodeMenu(null);
  }, [nodeMenu, menuNode]);

  const affected = useMemo(() => (view === "impact" ? impactItems(graph, impact) : new Map()), [graph, view, impact]);
  const lit = useMemo(
    () => highlights(graph, view, selected, assumption, affected),
    [graph, view, selected, assumption, affected],
  );
  const areaWidth = Math.max(1, stageSize.width - STAGE_INSET * 2);
  const areaHeight = Math.max(1, stageSize.height - STAGE_INSET - ZOOM_SPACE);
  const columns = Math.max(1, ...graph.levels.map((level) => level.length));
  const overviewWidth = Math.floor(
    (areaWidth / OVERVIEW_SCALE - CARD.pad * 2 - CARD.horizontalGap * (columns - 1)) / columns,
  );
  // Narrow cards only when a smaller card can keep every column in the overview.
  const defaultWidth = overviewWidth >= CARD.minWidth ? Math.min(CARD.width, overviewWidth) : CARD.width;
  const sizes = useMemo(
    () =>
      new Map(
        graph.order.map((id) => [
          id,
          props.cardSizes[id] ?? {
            width: defaultWidth,
            height: heights.get(id) ?? CARD.height,
          },
        ]),
      ),
    [graph.order, props.cardSizes, defaultWidth, heights],
  );
  const layout = useMemo(
    () => layoutGraph(graph, resizing ? new Map(sizes).set(resizing.id, resizing.size) : sizes),
    [graph, sizes, resizing],
  );
  const { width: canvasWidth, height: canvasHeight } = layout;
  const positioned = resizing ? anchoredCamera(camera, layout, resizing.id, resizing.anchor) : camera;

  // Wrapped titles determine the layout. Camera gestures need no new card measurements.
  useLayoutEffect(() => {
    if (resizing) return;
    const next = new Map(graph.order.map((id) => [id, cards.current.get(id)?.offsetHeight ?? CARD.height]));
    setHeights((current) =>
      current.size === next.size && [...next].every(([id, height]) => current.get(id) === height) ? current : next,
    );
  }, [graph, props.cardSizes, resizing, defaultWidth]);

  useLayoutEffect(() => {
    const element = stage.current;
    if (!element) return;
    const measure = () => {
      const { clientWidth: width, clientHeight: height } = element;
      setStageSize((current) => (current.width === width && current.height === height ? current : { width, height }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const fitScale = Math.min(1.25, areaWidth / canvasWidth, areaHeight / canvasHeight);
  const minimumScale = Math.min(0.3, fitScale);
  const fit = useCallback(
    (mode: Exclude<CameraMode, "manual">) => {
      if (!stageSize.width || !stageSize.height) return;
      const focus = initialNode.current;
      const point = focus ? layout.points.get(focus) : undefined;
      if (mode === "readable" && fitScale < OVERVIEW_SCALE && focus && point) {
        const card = sizes.get(focus) ?? CARD;
        const k = READABLE_SCALE;
        setCamera({
          k,
          x: STAGE_INSET + areaWidth / 2 - (point.x + card.width / 2) * k,
          y: STAGE_INSET + Math.min(areaHeight / 3, areaHeight - card.height * k) - point.y * k,
        });
        return;
      }
      setCamera({
        k: fitScale,
        x: STAGE_INSET + (areaWidth - canvasWidth * fitScale) / 2,
        y: STAGE_INSET + (areaHeight - canvasHeight * fitScale) / 2,
      });
    },
    [stageSize, areaWidth, areaHeight, fitScale, canvasWidth, canvasHeight, layout.points, sizes],
  );

  useLayoutEffect(() => {
    if (cameraMode.current !== "manual") fit(cameraMode.current);
  }, [fit]);

  const zoomAt = (factor: number, cx = stageSize.width / 2, cy = stageSize.height / 2) => {
    cameraMode.current = "manual";
    setCamera(({ x, y, k }) => {
      const next = Math.max(minimumScale, Math.min(2.5, k * factor));
      return { k: next, x: cx - ((cx - x) * next) / k, y: cy - ((cy - y) * next) / k };
    });
  };
  const panBy = (dx: number, dy: number) => {
    cameraMode.current = "manual";
    setCamera((current) => ({ ...current, x: current.x + dx, y: current.y + dy }));
  };

  // Wheel events must be non-passive to stop the page from scrolling.
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      const zooming = event.ctrlKey || event.metaKey;
      // Vertical scrolling on the proof graph should still reach the rest of the inspector.
      if (!zooming && !event.shiftKey && Math.abs(event.deltaY) >= Math.abs(event.deltaX)) return;
      event.preventDefault();
      if (drag.current) return;
      if (zooming) {
        const bounds = element.getBoundingClientRect();
        const factor = Math.exp(-event.deltaY * 0.01);
        const cx = event.clientX - bounds.left;
        const cy = event.clientY - bounds.top;
        cameraMode.current = "manual";
        setCamera(({ x, y, k }) => {
          const next = Math.max(minimumScale, Math.min(2.5, k * factor));
          return { k: next, x: cx - ((cx - x) * next) / k, y: cy - ((cy - y) * next) / k };
        });
      } else {
        cameraMode.current = "manual";
        setCamera((current) => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }));
      }
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [minimumScale]);

  // Selections made by opening a menu already have a visible target.
  useEffect(() => {
    if (!selected || currentMenu?.id === selected) return;
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

  const openNodeMenu = (id: string, anchor: ContextMenuAnchor) => {
    if (drag.current || panning) return;
    cameraMode.current = "manual";
    setNodeMenu({ id, claimId: graph.claim.id, view, anchor });
    if (id !== selected) props.onSelect(id);
  };

  const commitResize = (id: string, size: CardSize, anchor: Point) => {
    const original = sizes.get(id);
    if (!original || (original.width === size.width && original.height === size.height)) return;
    const next = layoutGraph(graph, new Map(sizes).set(id, size));
    cameraMode.current = "manual";
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
    cameraMode.current = "manual";
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
      cameraMode.current = "manual";
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
      cameraMode.current = "fit";
      fit("fit");
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
        aria-label={t("stage.label", { shortcut: formatShortcut("Primary") })}
        data-proof-view={view}
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
              const active = nodeWork && (running ? nodeWork.running : nodeWork.queued);
              const activeCount = active ? [...active.values()].reduce((sum, count) => sum + count, 0) : 0;
              const queued =
                !running && nodeWork?.queued.size
                  ? t("graph.queued", {
                      work: workLabel(nodeWork.queued, t("chat.listSeparator", { defaultValue: ", " })),
                    })
                  : "";
              const title = nodeTitle(t, node, { full: true });
              const customSize = resizing?.id === id ? resizing.size : props.cardSizes[id];
              const titleLines = customSize ? Math.max(1, Math.floor((customSize.height - 42) / 18)) : undefined;
              return (
                <div
                  key={id}
                  className="graph-node-frame"
                  data-resizing={resizing?.id === id || undefined}
                  style={{ left: point.x, top: point.y, width: customSize?.width ?? defaultWidth }}
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
                    data-sized={customSize ? "true" : undefined}
                    style={{ height: customSize?.height, "--title-lines": titleLines } as React.CSSProperties}
                    aria-expanded={currentMenu?.id === id}
                    onClick={(event) => {
                      if (!event.ctrlKey) props.onSelect(id);
                    }}
                    {...contextMenuProps((anchor) => openNodeMenu(id, anchor))}
                  >
                    <span className="node-head">
                      <Mark tone={nodeTone(node, blocked)} shape={node.kind === "hypothesis" ? "square" : undefined} />
                      <span className="node-title">{title}</span>
                    </span>
                    <span className="node-caption">
                      <span className="node-code">{graph.codes.get(id)}</span>
                      {activeCount > 0 && (
                        <span className="work" data-state={running ? "running" : "queued"} title={running || queued}>
                          {running ? <Spin /> : <Clock />}
                          <span className="work-count">{activeCount}</span>
                        </span>
                      )}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="graph-node-resize"
                    aria-label={t("stage.resizeNode", { title })}
                    aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown"
                    aria-description={resizeHelp}
                    title={resizeHelp}
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
        </div>
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
              cameraMode.current = "fit";
              fit("fit");
            }}
          >
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M2 5V2h3M9 2h3v3M12 9v3H9M5 12H2V9" />
            </svg>
          </button>
        </div>
      </div>
      {currentMenu && menuNode && (
        <ContextMenu
          key={currentMenu.id}
          anchor={currentMenu.anchor}
          label={t("graphActions.forTarget", { title: nodeTitle(t, menuNode, { full: true }) })}
          groups={props.nodeActions(currentMenu.id)}
          onDismiss={() => setNodeMenu(null)}
        />
      )}
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
  const edgeId = useId();
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
    <div className="legend">
      <div className="legend-counts" role="status" aria-label={t("legend.label")}>
        {tones.map((tone) => {
          const label = tone === "bad" && view === "impact" ? t("legend.badImpact") : t(`tone.${tone}`);
          const compactLabel =
            tone === "review"
              ? t("legend.review")
              : tone === "bad"
                ? t(`legend.${view === "impact" ? "affected" : "blocked"}`)
                : label;
          return (
            <span key={tone} title={label}>
              <Mark tone={tone} />
              <span className="legend-count-label">{compactLabel}</span>
              <b>{counts.get(tone)}</b>
            </span>
          );
        })}
        {running > 0 && (
          <span title={t("legend.running")}>
            <Spin />
            <span className="legend-count-label">{t("legend.running")}</span>
            <b>{running}</b>
          </span>
        )}
        {queued > 0 && (
          <span title={t("legend.queued")}>
            <Clock />
            <span className="legend-count-label">{t("legend.queued")}</span>
            <b>{queued}</b>
          </span>
        )}
      </div>
      {lines.length > 0 && (
        <Popover
          label={t("legend.edges")}
          panelId={edgeId}
          popup="dialog"
          placement="top-end"
          className="graph-edge-legend"
          triggerClassName="graph-edge-legend-toggle"
          trigger={
            <>
              <span className="graph-edge-legend-key" aria-hidden="true" />
              <span className="graph-edge-legend-label">{t("legend.edges")}</span>
              <Chevron />
            </>
          }
        >
          {() => (
            <ul>
              {lines.map((kind) => (
                <li key={kind}>
                  <i data-kind={kind} aria-hidden="true" />
                  {t(`edge.${kind}`)}
                </li>
              ))}
            </ul>
          )}
        </Popover>
      )}
    </div>
  );
}
