import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { atlasShape, ATLAS_COLOURS } from "../workbench/atlas";
import { summarize } from "../workbench/text";
import type { AtlasClaim } from "../workbench/types";
import type { AtlasBox } from "../workbench/useLayout";

const ATLAS_MIN_WIDTH = 110;
function atlasMaximumWidth(stageWidth: number) {
  return stageWidth ? Math.max(ATLAS_MIN_WIDTH, Math.min(420, stageWidth - 24)) : 420;
}

export function atlasPlacement(stored: AtlasBox | null, width: number, height: number, interactive = false): AtlasBox {
  // Keep the proof overview compact on narrow graphs. The project legend needs room for its controls.
  const base =
    stored ??
    (!interactive && width && width < 640
      ? { x: 12, y: Math.max(56, height - 128), w: 124 }
      : { x: 18, y: 14, w: 186 });
  const w = Math.round(Math.max(ATLAS_MIN_WIDTH, Math.min(atlasMaximumWidth(width), base.w)));
  if (!width || !height) return { ...base, w };
  // The project map keeps the legend visible, including space for a scrollbar and a wrapped caption.
  const tall = interactive ? (w * 3) / 4 + 76 : w * 0.95;
  return {
    w,
    x: Math.round(Math.max(0, Math.min(width - w, base.x))),
    y: Math.round(Math.max(0, Math.min(height - Math.min(tall, height), base.y))),
  };
}

export type StageRect = { x: number; y: number; w: number; h: number; selected?: boolean };

const ATLAS_MARGIN = { x: 18, y: 14 };
const ATLAS_SMALL = 160;

function overlapArea(a: StageRect, b: StageRect) {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

// Where the project map overview opens when the researcher has not placed it yet:
// the position that covers the least of the visible claim cards, never the
// selected one if another position avoids it. Corners come first; the bottom
// right holds the zoom controls. Narrow maps open the compact overview.
export function atlasOpening(width: number, height: number, cards: StageRect[]): AtlasBox {
  const w = width < 640 ? 124 : 186;
  // The drawing is 4:3. The caption, padding and (above ATLAS_SMALL) the legend add the rest.
  const drawn = (w * 3) / 4 + (w < ATLAS_SMALL ? 30 : 48);
  // atlasPlacement keeps this much room below an interactive overview.
  const bottom = height - ((w * 3) / 4 + 76);
  const right = width - w - ATLAS_MARGIN.x;
  const middle = (width - w) / 2;
  const positions = [
    { x: ATLAS_MARGIN.x, y: ATLAS_MARGIN.y },
    { x: right, y: ATLAS_MARGIN.y },
    { x: ATLAS_MARGIN.x, y: bottom },
    { x: middle, y: ATLAS_MARGIN.y },
    { x: middle, y: bottom },
  ];
  let best = positions[0]!;
  let least = Infinity;
  for (const position of positions) {
    const area = cards.reduce(
      (sum, card) => sum + overlapArea({ ...position, w, h: drawn }, card) * (card.selected ? 100 : 1),
      0,
    );
    if (area < least) {
      best = position;
      least = area;
    }
  }
  return atlasPlacement({ ...best, w }, width, height, true);
}

export function AtlasMap({
  claims,
  claimId,
  box,
  stageWidth,
  onBox,
  onSelectClaim,
}: {
  claims: AtlasClaim[];
  claimId: string | null;
  box: AtlasBox;
  stageWidth: number;
  onBox: (box: AtlasBox, persist: boolean) => void;
  onSelectClaim?: (id: string) => void;
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
      data-interactive={onSelectClaim ? "true" : undefined}
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
        {shape.keys.map((key) => {
          const title = key.title || t("atlas.untitled");
          const content = (
            <>
              <i aria-hidden="true" />
              {summarize(title, 5).replace(" …", "…")}
            </>
          );
          const style = { "--c": `var(--cat-${key.index + 1})` } as React.CSSProperties;
          return onSelectClaim ? (
            <button
              key={key.claimId}
              type="button"
              aria-label={title}
              aria-pressed={key.current}
              title={title}
              style={style}
              onClick={() => onSelectClaim(key.claimId)}
            >
              {content}
            </button>
          ) : (
            <span key={key.claimId} data-current={String(key.current)} title={title} style={style}>
              {content}
            </span>
          );
        })}
        {shape.more > 0 && <span>+{shape.more}</span>}
      </div>
      <span
        className="atlas-resize"
        role="separator"
        tabIndex={0}
        aria-valuemin={ATLAS_MIN_WIDTH}
        aria-valuemax={atlasMaximumWidth(stageWidth)}
        aria-valuenow={box.w}
        aria-label={t("atlas.resize")}
        title={t("atlas.resize")}
        onPointerDown={(event) => drag(event, "resize")}
        onKeyDown={onResizeKey}
      />
    </figure>
  );
}
