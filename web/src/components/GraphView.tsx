import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CARD,
  highlights,
  impactItems,
  impactLineKey,
  layoutGraph,
  workLabel,
  type Graph,
  type NodeWork,
  type View,
} from "../workbench/graph";
import { atlasShape, ATLAS_COLOURS } from "../workbench/atlas";
import { kindText, nodeStatus, nodeSub, nodeTitle, nodeTone, summarize, type Tone } from "../workbench/text";
import type { AtlasClaim, ImpactPreview } from "../workbench/types";
import type { AtlasBox } from "../workbench/useLayout";
import { Clock, Mark, Spin } from "./Icons";

type Camera = { x: number; y: number; k: number };

type Props = {
  graph: Graph;
  view: View;
  selected: string | null;
  assumption: string | null;
  impact: ImpactPreview | undefined;
  work: Map<string, NodeWork>;
  running: number;
  queued: number;
  atlasClaims: AtlasClaim[];
  claimId: string;
  atlasOpen: boolean;
  atlasBox: AtlasBox | null;
  onAtlasBox: (box: AtlasBox, persist: boolean) => void;
  onSelect: (id: string) => void;
  onView: (view: View) => void;
};

export function GraphView(props: Props) {
  const { t } = useTranslation();
  const { graph, view, selected, assumption, impact, work } = props;
  const stage = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<string, HTMLButtonElement>());
  const [heights, setHeights] = useState(new Map<string, number>());
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, k: 1 });
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  const moved = useRef(false);
  const [panning, setPanning] = useState(false);

  const affected = useMemo(() => (view === "impact" ? impactItems(graph, impact) : new Map()), [graph, view, impact]);
  const lit = useMemo(
    () => highlights(graph, view, selected, assumption, affected),
    [graph, view, selected, assumption, affected],
  );
  const layout = useMemo(() => layoutGraph(graph, heights), [graph, heights]);

  // Card heights decide where edges meet them; measure after every render that may change a card.
  // Runs after every render on purpose; it only sets state when a height actually changed.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    const next = new Map<string, number>();
    let changed = false;
    for (const id of graph.order) {
      const height = cards.current.get(id)?.offsetHeight || 72;
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

  const fit = useCallback(() => {
    const { width, height } = stageSize;
    if (!width || !height) return;
    // Leave the overview map uncovered when there is room beside it; otherwise the graph may pass under it.
    const mapRight = !atlasVisible || box.y > height / 2 ? 0 : box.x + box.w + 12;
    const inset = mapRight && mapRight < width * 0.4 ? mapRight : 0;
    const k = Math.max(0.3, Math.min(1.25, (width - inset - 32) / layout.width, (height - 56) / layout.height));
    setCamera({
      k,
      x: inset + (width - inset - layout.width * k) / 2,
      y: Math.max(8, (height - layout.height * k) / 2),
    });
  }, [stageSize, layout.width, layout.height, atlasVisible, box.x, box.y, box.w]);

  // A new claim gets a fresh view; otherwise the camera stays where the researcher left it.
  useEffect(() => {
    moved.current = false;
  }, [graph.claim.id]);
  useEffect(() => {
    if (!moved.current) fit();
  }, [fit]);

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
    if (!point || !width || !height) return;
    setCamera((current) => {
      const left = current.x + point.x * current.k;
      const top = current.y + point.y * current.k;
      const right = left + CARD.width * current.k;
      const bottom = top + (heights.get(selected) ?? 72) * current.k;
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
            width: layout.width,
            height: layout.height,
            transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.k})`,
          }}
        >
          <svg className="graph-edges" aria-hidden="true" viewBox={`0 0 ${layout.width} ${layout.height}`}>
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
              const sub = nodeSub(t, node);
              const title = nodeTitle(t, node);
              return (
                <button
                  key={id}
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
                  style={{ "--x": `${point.x}px`, "--y": `${point.y}px` } as React.CSSProperties}
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
              );
            })}
          </div>
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
            stored={props.atlasBox}
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

// Until moved, the map sits top-left on a wide canvas and as a small map bottom-left on a narrow one.
function atlasPlacement(stored: AtlasBox | null, width: number, height: number): AtlasBox {
  const base =
    stored ?? (width && width < 640 ? { x: 12, y: Math.max(56, height - 128), w: 124 } : { x: 18, y: 14, w: 186 });
  const w = Math.round(Math.max(110, Math.min(420, base.w, width ? width - 24 : base.w)));
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
  onBox,
}: {
  claims: AtlasClaim[];
  claimId: string;
  box: AtlasBox;
  stored: AtlasBox | null;
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
