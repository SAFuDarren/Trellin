import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { useTranslation } from "react-i18next";
import type { Claim } from "../api/client";
import type { ProjectCanvasLayout, ProjectCanvasOperation } from "../api/projectCanvas";
import type { ClaimProgress } from "../workbench/claimProgress";
import type { Point } from "../workbench/graph";
import { nodeTitle } from "../workbench/text";
import type { AtlasClaim } from "../workbench/types";
import type { AtlasBox } from "../workbench/useLayout";
import { AtlasMap, atlasOpening, atlasPlacement } from "./AtlasMap";
import { ClaimProgressSummary, claimProgressText } from "./ClaimProgress";
import {
  ContextMenu,
  contextMenuProps,
  Menu,
  Picker,
  type ContextMenuAnchor,
  type FloatingGroup,
  type FloatingOption,
} from "./FloatingControls";
import { Chevron } from "./Icons";
import "../styles/project-canvas.css";

type Camera = Point & { k: number };
type Gesture = { pointer: number; start: Point; origin: Point; moved: boolean; id?: string };
type Preview = { id: string; point: Point };

const WIDTH = 192;
const HEIGHT = 80;
const COLUMN_STEP = 240;
const ROW_STEP = 128;
const BOUND = 1_000_000;
const directions: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

function coordinate(value: number) {
  return Math.round(Math.max(-BOUND, Math.min(BOUND, value)));
}

function connect(a: Point, b: Point) {
  const horizontal = Math.abs(a.x - b.x) >= Math.abs(a.y - b.y);
  if (horizontal) {
    const ax = a.x + (a.x < b.x ? WIDTH : 0);
    const bx = b.x + (a.x < b.x ? 0 : WIDTH);
    const middle = (ax + bx) / 2;
    const ay = a.y + HEIGHT / 2;
    const by = b.y + HEIGHT / 2;
    return `M ${ax} ${ay} C ${middle} ${ay}, ${middle} ${by}, ${bx} ${by}`;
  }
  const ax = a.x + WIDTH / 2;
  const bx = b.x + WIDTH / 2;
  const ay = a.y + (a.y < b.y ? HEIGHT : 0);
  const by = b.y + (a.y < b.y ? 0 : HEIGHT);
  const middle = (ay + by) / 2;
  return `M ${ax} ${ay} C ${ax} ${middle}, ${bx} ${middle}, ${bx} ${by}`;
}

function zoomCamera(camera: Camera, factor: number, center: Point) {
  const k = Math.max(Math.min(0.25, camera.k), Math.min(2.5, camera.k * factor));
  return {
    x: center.x - ((center.x - camera.x) * k) / camera.k,
    y: center.y - ((center.y - camera.y) * k) / camera.k,
    k,
  };
}

function visibleOffset(offset: number, start: number, extent: number, available: number) {
  if (extent > available) return (available - extent) / 2 - start;
  return Math.max(-start, Math.min(offset, available - start - extent));
}

type Props = {
  claims: Claim[];
  claimProgress?: ReadonlyMap<string, ClaimProgress>;
  selected: string | null;
  onSelect: (id: string) => void;
  onContextClaim: (id: string | null) => void;
  claimActions: (id: string, link: FloatingOption) => FloatingGroup[];
  detailsOpen: boolean;
  onToggleDetails: () => void;
  atlasClaims: AtlasClaim[] | undefined;
  atlasOpen: boolean;
  atlasBox: AtlasBox | null;
  onToggleAtlas: () => void;
  onAtlasBox: (box: AtlasBox, persist: boolean) => void;
  atlasError?: string;
  layout: ProjectCanvasLayout;
  onLayout: (edit: ProjectCanvasOperation) => void;
  layoutUnavailable?: boolean;
  saving?: boolean;
};

export function ProjectCanvas({
  claims,
  claimProgress,
  selected,
  onSelect,
  onContextClaim,
  claimActions,
  detailsOpen,
  onToggleDetails,
  atlasClaims,
  atlasOpen,
  atlasBox,
  onToggleAtlas,
  onAtlasBox,
  atlasError,
  layout,
  onLayout,
  layoutUnavailable,
  saving,
}: Props) {
  const { t } = useTranslation();
  const stage = useRef<HTMLDivElement>(null);
  const nodes = useRef(new Map<string, HTMLButtonElement>());
  const removingLastLink = useRef(false);
  const movedCamera = useRef(false);
  const gesture = useRef<Gesture | null>(null);
  const draggedCard = useRef<string | null>(null);
  const inspected = useRef<string | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, k: 1 });
  const unfittedCamera = useRef(camera);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [panning, setPanning] = useState(false);
  const [contextTarget, setContextTarget] = useState<{ id: string; anchor: ContextMenuAnchor } | null>(null);
  const [linkSource, setLinkSource] = useState<string | null>(null);
  const [linkNotice, setLinkNotice] = useState("");
  const [atlasOpeningBox, setAtlasOpeningBox] = useState<AtlasBox | null>(null);

  const cards = useMemo(() => {
    // Titles may change. A stable claim identifier keeps the fallback placement deterministic.
    const ordered = [...claims].sort((a, b) => a.id.localeCompare(b.id));
    const columns = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(ordered.length))));
    return ordered.map((claim, index) => {
      const node = { id: `goal:${claim.id}`, kind: "goal", title: claim.title ?? undefined, status: claim.status };
      return {
        id: claim.id,
        title: nodeTitle(t, node, { full: true }),
        status: claim.status,
        domain: claim.domain,
        point: layout.positions[claim.id] ?? {
          x: (index % columns) * COLUMN_STEP,
          y: Math.floor(index / columns) * ROW_STEP,
        },
      };
    });
  }, [claims, layout.positions, t]);
  const byId = useMemo(() => new Map(cards.map((card) => [card.id, card])), [cards]);
  const visibleLinks = layout.links.filter((link) => byId.has(link.source) && byId.has(link.target));
  const activeSource = linkSource ? byId.get(linkSource) : undefined;
  const contextCard = contextTarget && byId.get(contextTarget.id);
  const editingDisabled = Boolean(layoutUnavailable || saving);
  const center = { x: size.width / 2, y: size.height / 2 };
  const mapBox = atlasPlacement(atlasBox ?? atlasOpeningBox, size.width, size.height, true);

  const dismissContextMenu = useCallback(() => {
    setContextTarget(null);
    onContextClaim(null);
  }, [onContextClaim]);

  useEffect(() => {
    if (contextTarget && !contextCard) dismissContextMenu();
  }, [contextCard, contextTarget, dismissContextMenu]);

  useLayoutEffect(() => {
    if (visibleLinks.length || !removingLastLink.current) return;
    removingLastLink.current = false;
    if (document.activeElement === document.body) stage.current?.focus({ preventScroll: true });
  }, [visibleLinks.length]);

  useEffect(() => {
    const target = stage.current;
    if (!target) return;
    const observer = new ResizeObserver(() => setSize({ width: target.clientWidth, height: target.clientHeight }));
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  const fit = useCallback(
    (all: boolean) => {
      const first = cards[0]?.point;
      if (!first || !size.width || !size.height) return;
      const left = Math.min(...cards.map(({ point }) => point.x));
      const top = Math.min(...cards.map(({ point }) => point.y));
      const width = Math.max(...cards.map(({ point }) => point.x)) - left + WIDTH;
      const height = Math.max(...cards.map(({ point }) => point.y)) - top + HEIGHT;
      const k = Math.min(1.15, Math.max(8, size.width - 40) / width, Math.max(8, size.height - 72) / height);
      if (!all && k < 0.6) {
        // Open at a readable scale on extensive maps; Fit to window can zoom out to the whole layout.
        const scale = 0.6;
        setCamera({ x: size.width / 2 - (first.x + WIDTH / 2) * scale, y: 28 - first.y * scale, k: scale });
      } else {
        setCamera({ x: (size.width - width * k) / 2 - left * k, y: (size.height - height * k) / 2 - top * k - 10, k });
      }
    },
    [cards, size],
  );

  useEffect(() => {
    if (!movedCamera.current) fit(false);
  }, [fit]);

  useLayoutEffect(() => {
    // Choose the overview's opening corner once per opening, after the map has
    // been fitted, so that it stays put while the map is panned. A saved
    // position wins (#154).
    if (!atlasOpen) {
      setAtlasOpeningBox(null);
      return;
    }
    if (atlasBox || atlasOpeningBox || !size.width || !size.height) return;
    if (cards.length && camera === unfittedCamera.current) return;
    setAtlasOpeningBox(
      atlasOpening(
        size.width,
        size.height,
        cards.map(({ id, point }) => ({
          x: camera.x + point.x * camera.k,
          y: camera.y + point.y * camera.k,
          w: WIDTH * camera.k,
          h: HEIGHT * camera.k,
          selected: id === selected,
        })),
      ),
    );
  }, [atlasBox, atlasOpen, atlasOpeningBox, camera, cards, selected, size.height, size.width]);

  useLayoutEffect(() => {
    const id = detailsOpen ? selected : null;
    if (inspected.current === id) return;
    inspected.current = id;
    if (contextTarget?.id === id) return;
    const target = stage.current;
    const point = id ? byId.get(id)?.point : undefined;
    if (!target || !point) return;
    const { clientWidth, clientHeight } = target;
    setCamera((current) => {
      const x = visibleOffset(current.x, point.x * current.k, WIDTH * current.k, clientWidth);
      const y = visibleOffset(current.y, point.y * current.k, HEIGHT * current.k, clientHeight);
      return x === current.x && y === current.y ? current : { ...current, x, y };
    });
  }, [byId, contextTarget, detailsOpen, selected]);

  useEffect(() => {
    const target = stage.current;
    if (!target) return;
    const wheel = (event: WheelEvent) => {
      if ((event.target as Element).closest(".atlas")) return;
      event.preventDefault();
      if (gesture.current) return;
      movedCamera.current = true;
      const rect = target.getBoundingClientRect();
      if (event.ctrlKey || event.metaKey) {
        const at = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        setCamera((current) => zoomCamera(current, Math.exp(-event.deltaY * 0.01), at));
      } else {
        setCamera((current) => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }));
      }
    };
    target.addEventListener("wheel", wheel, { passive: false });
    return () => target.removeEventListener("wheel", wheel);
  }, []);

  function zoom(factor: number) {
    movedCamera.current = true;
    setCamera((current) => zoomCamera(current, factor, center));
  }

  function openContextMenu(id: string, anchor: ContextMenuAnchor) {
    movedCamera.current = true;
    setContextTarget({ id, anchor });
    onContextClaim(id);
  }

  function linkAction(id: string | null): FloatingOption {
    const disabled = !activeSource && (!id || cards.length < 2 || editingDisabled);
    return {
      id: "link-claim",
      label: t(activeSource ? "projectCanvas.cancelLink" : "projectCanvas.linkClaim"),
      disabled,
      detail: disabled ? t("graphActions.linkUnavailable") : undefined,
      onSelect: () => {
        if (disabled) return;
        setLinkSource(activeSource ? null : id);
        setLinkNotice("");
      },
    };
  }

  function choose(id: string) {
    if (activeSource) {
      if (activeSource.id === id || editingDisabled) return;
      const source = activeSource.id;
      const linked = visibleLinks.some(
        (link) => (link.source === source && link.target === id) || (link.target === source && link.source === id),
      );
      setLinkSource(null);
      setLinkNotice(linked ? t("projectCanvas.linkAlreadyExists") : "");
      if (!linked) onLayout({ action: "link", source, target: id });
    } else {
      setLinkNotice("");
    }
    movedCamera.current = true;
    onSelect(id);
  }

  function reveal(id: string) {
    const card = byId.get(id);
    if (!card) return;
    // Finding a card is navigation. Only a deliberate card activation completes a link.
    movedCamera.current = true;
    setLinkNotice("");
    onSelect(id);
    setCamera((current) => ({
      ...current,
      x: center.x - (card.point.x + WIDTH / 2) * current.k,
      y: center.y - (card.point.y + HEIGHT / 2) * current.k,
    }));
    nodes.current.get(id)?.focus({ preventScroll: true });
  }

  function startGesture(event: PointerEvent<HTMLElement>, id?: string) {
    if (event.button !== 0 || event.ctrlKey || gesture.current) return;
    draggedCard.current = null;
    const origin = id ? byId.get(id)?.point : camera;
    if (!origin || (id && (activeSource || editingDisabled))) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = {
      pointer: event.pointerId,
      start: { x: event.clientX, y: event.clientY },
      origin,
      moved: false,
      id,
    };
  }

  function draggedPoint(current: Gesture, x: number, y: number): Point {
    return {
      x: coordinate(current.origin.x + (x - current.start.x) / camera.k),
      y: coordinate(current.origin.y + (y - current.start.y) / camera.k),
    };
  }

  function moveGesture(event: PointerEvent<HTMLDivElement>) {
    const current = gesture.current;
    if (!current || current.pointer !== event.pointerId) return;
    const dx = event.clientX - current.start.x;
    const dy = event.clientY - current.start.y;
    if (!current.moved && Math.hypot(dx, dy) < 4) return;
    current.moved = true;
    movedCamera.current = true;
    if (current.id) {
      setPreview({
        id: current.id,
        point: draggedPoint(current, event.clientX, event.clientY),
      });
    } else {
      setPanning(true);
      setCamera((value) => ({ ...value, x: current.origin.x + dx, y: current.origin.y + dy }));
    }
  }

  function finishGesture(event: PointerEvent<HTMLDivElement>, save: boolean) {
    const current = gesture.current;
    if (!current || current.pointer !== event.pointerId) return;
    gesture.current = null;
    if (save && current.id && current.moved && !editingDisabled) {
      draggedCard.current = current.id;
      const point = draggedPoint(current, event.clientX, event.clientY);
      if (point.x !== current.origin.x || point.y !== current.origin.y)
        onLayout({ action: "move", id: current.id, ...point });
    }
    setPreview(null);
    setPanning(false);
  }

  function moveByKey(event: KeyboardEvent<HTMLButtonElement>, id: string, point: Point) {
    const delta = directions[event.key];
    if (!delta || editingDisabled || activeSource || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const step = event.shiftKey ? 32 : 8;
    movedCamera.current = true;
    onLayout({
      action: "move",
      id,
      x: coordinate(point.x + delta[0] * step),
      y: coordinate(point.y + delta[1] * step),
    });
  }

  function onCanvasKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      if (activeSource) {
        setLinkSource(null);
        event.preventDefault();
      }
      return;
    }
    if (event.target !== stage.current) return;
    const delta = directions[event.key];
    if (delta) {
      movedCamera.current = true;
      setCamera((current) => ({ ...current, x: current.x - delta[0] * 48, y: current.y - delta[1] * 48 }));
    } else if (event.key === "+" || event.key === "=") zoom(1.2);
    else if (event.key === "-") zoom(1 / 1.2);
    else if (event.key === "0") fit(true);
    else return;
    event.preventDefault();
  }

  function position(id: string) {
    return preview?.id === id ? preview.point : byId.get(id)!.point;
  }

  const toolbarLink = linkAction(selected);

  return (
    <div className="project-canvas" onKeyDown={onCanvasKey}>
      <div className="project-canvas-tools">
        <button
          id="map-details-toggle"
          type="button"
          className="button"
          disabled={!selected && !detailsOpen}
          aria-expanded={detailsOpen}
          aria-controls="review"
          onClick={() => {
            movedCamera.current = true;
            onToggleDetails();
          }}
        >
          {t("canvasShell.details")}
        </button>
        <button
          type="button"
          className="button"
          disabled={toolbarLink.disabled}
          onClick={(event) => {
            event.currentTarget.focus();
            toolbarLink.onSelect?.();
          }}
        >
          {toolbarLink.label}
        </button>
        <Picker
          label={t("projectCanvas.findClaim")}
          searchLabel={t("projectCanvas.searchClaims")}
          emptyLabel={t("projectCanvas.noMatches")}
          trigger={
            <>
              {t("projectCanvas.findClaim")}
              <Chevron />
            </>
          }
          triggerClassName="project-canvas-menu-trigger"
          className="project-canvas-popover"
          groups={[{ options: cards.map(({ id, title }) => ({ id, label: title })) }]}
          value={selected ?? undefined}
          focusOnSelect={false}
          onSelect={reveal}
        />
        {visibleLinks.length > 0 && (
          <Menu
            label={t("projectCanvas.researchLinks")}
            trigger={
              <>
                {t("projectCanvas.researchLinks")}
                <Chevron />
              </>
            }
            triggerClassName="project-canvas-menu-trigger"
            className="project-canvas-popover"
            groups={[
              {
                options: visibleLinks.map(({ source, target }) => ({
                  id: `${source}:${target}`,
                  label: t("projectCanvas.removeLink", {
                    source: byId.get(source)!.title,
                    target: byId.get(target)!.title,
                  }),
                  disabled: editingDisabled,
                  destructive: true,
                  onSelect: () => {
                    removingLastLink.current = visibleLinks.length === 1;
                    onLayout({ action: "unlink", source, target });
                  },
                })),
              },
            ]}
          />
        )}
        <button type="button" className="button" aria-pressed={atlasOpen} onClick={onToggleAtlas}>
          {t("sidebar.atlas")}
        </button>
      </div>
      <div
        ref={stage}
        className="stage project-canvas-stage"
        tabIndex={0}
        role="application"
        aria-label={t("projectCanvas.label")}
        data-panning={panning ? "true" : undefined}
        data-linking={activeSource ? "true" : undefined}
        onPointerDown={(event) => {
          if (!(event.target as Element).closest("button, .atlas")) startGesture(event);
        }}
        onPointerMove={moveGesture}
        onPointerUp={(event) => finishGesture(event, true)}
        onPointerCancel={(event) => finishGesture(event, false)}
        onLostPointerCapture={(event) => finishGesture(event, false)}
      >
        {(activeSource || linkNotice) && (
          <p className="project-canvas-message" role="status">
            {activeSource ? t("projectCanvas.chooseLinkTarget", { title: activeSource.title }) : linkNotice}
          </p>
        )}
        <div
          className="plot project-canvas-plot"
          style={{ transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.k})` }}
        >
          <svg className="project-canvas-paths" aria-hidden="true" width="1" height="1">
            {visibleLinks.map(({ source, target }) => (
              <path data-research-link="" key={`${source}:${target}`} d={connect(position(source), position(target))} />
            ))}
          </svg>
          {cards.map((card) => {
            const at = position(card.id);
            const menu = contextMenuProps((anchor) => openContextMenu(card.id, anchor));
            const movingShortcuts = layoutUnavailable ? "" : " ArrowLeft ArrowRight ArrowUp ArrowDown";
            const progress = claimProgressText(t, claimProgress?.get(card.id), card.status);
            return (
              <button
                {...menu}
                key={card.id}
                ref={(element) => {
                  if (element) nodes.current.set(card.id, element);
                  else nodes.current.delete(card.id);
                }}
                type="button"
                className="project-canvas-node"
                data-claim-id={card.id}
                data-link-source={activeSource?.id === card.id ? "true" : undefined}
                data-moving={preview?.id === card.id ? "true" : undefined}
                aria-pressed={selected === card.id}
                aria-label={card.title}
                aria-description={[progress.label, progress.count, card.domain].filter(Boolean).join(" · ")}
                aria-keyshortcuts={menu["aria-keyshortcuts"] + movingShortcuts}
                title={card.title}
                style={{ left: at.x, top: at.y, width: WIDTH, height: HEIGHT }}
                onClick={(event) => {
                  if (!event.ctrlKey && (event.detail === 0 || draggedCard.current !== card.id)) choose(card.id);
                  draggedCard.current = null;
                }}
                onPointerDown={(event) => startGesture(event, card.id)}
                onKeyDown={(event) => {
                  menu.onKeyDown(event);
                  if (!event.defaultPrevented) moveByKey(event, card.id, at);
                }}
              >
                <span className="project-canvas-title">{card.title}</span>
                <ClaimProgressSummary {...progress} detail={card.domain} />
              </button>
            );
          })}
        </div>
        {!cards.length && <p className="project-canvas-empty">{t("notice.noClaims")}</p>}
        {atlasOpen &&
          (atlasClaims?.length ? (
            (atlasBox || atlasOpeningBox) && (
              <AtlasMap
                claims={atlasClaims}
                claimId={selected}
                box={mapBox}
                stageWidth={size.width}
                onBox={onAtlasBox}
                onSelectClaim={reveal}
              />
            )
          ) : (
            <p className="project-canvas-atlas-status" role={atlasError ? "alert" : "status"}>
              {atlasError ?? t(atlasClaims ? "atlas.empty" : "atlas.loading")}
            </p>
          ))}
        <div className="zoom" role="group" aria-label={t("stage.zoom")}>
          <button type="button" aria-label={t("stage.zoomOut")} onClick={() => zoom(1 / 1.2)}>
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M3 7h8" />
            </svg>
          </button>
          <button
            type="button"
            className="zoom-level"
            aria-label={t("stage.zoomReset")}
            onClick={() => zoom(1 / camera.k)}
          >
            {Math.round(camera.k * 100)}%
          </button>
          <button type="button" aria-label={t("stage.zoomIn")} onClick={() => zoom(1.2)}>
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M3 7h8M7 3v8" />
            </svg>
          </button>
          <span className="zoom-rule" aria-hidden="true" />
          <button type="button" aria-label={t("stage.fit")} onClick={() => fit(true)}>
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M2 5V2h3M9 2h3v3M12 9v3H9M5 12H2V9" />
            </svg>
          </button>
        </div>
      </div>
      {contextTarget && contextCard && (
        <ContextMenu
          anchor={contextTarget.anchor}
          label={t("graphActions.forTarget", { title: contextCard.title })}
          groups={claimActions(contextCard.id, linkAction(contextCard.id))}
          onDismiss={dismissContextMenu}
        />
      )}
    </div>
  );
}
