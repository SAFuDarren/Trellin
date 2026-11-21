import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import type { Claim } from "../api/client";
import type { ProjectCanvasLayout, ProjectCanvasOperation } from "../api/projectCanvas";
import { formatShortcut, SHORTCUTS } from "../shortcuts";
import { graphAction } from "../workbench/actions";
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
type KeyboardMove = Preview & { origin: Point };

const WIDTH = 192;
const HEIGHT = 80;
const COLUMN_STEP = 240;
const ROW_STEP = 128;
const BOUND = 1_000_000;
const DOUBLE_CLICK_MS = 400;
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

function MapViewToggle({
  label,
  shortcut,
  pressed,
  onToggle,
  disabled,
  details,
  children,
}: {
  label: string;
  shortcut: string;
  pressed: boolean;
  onToggle: () => void;
  disabled?: boolean;
  details?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const hint = useId();
  return (
    <span className="map-view-control">
      <button
        id={details ? "map-details-toggle" : undefined}
        type="button"
        className="button map-view-toggle"
        disabled={disabled}
        aria-label={label}
        aria-describedby={hint}
        aria-keyshortcuts={shortcut}
        aria-pressed={pressed}
        aria-expanded={details ? pressed : undefined}
        aria-controls={details ? "review" : undefined}
        onClick={onToggle}
      >
        {children}
      </button>
      <span id={hint} className="map-view-hint" role="tooltip">
        {t("common.shortcutHint", { label, shortcut: formatShortcut(shortcut) })}
      </span>
    </span>
  );
}

type Props = {
  claims: Claim[];
  active?: boolean;
  claimProgress?: ReadonlyMap<string, ClaimProgress>;
  selected: string | null;
  onSelect: (id: string, openDetails?: boolean) => void;
  onOpenProof?: (id: string) => void;
  onContextClaim: (id: string | null) => void;
  claimActions: (id: string, link: FloatingOption, move: FloatingOption) => FloatingGroup[];
  detailsOpen: boolean;
  onDetailsChange: (open: boolean) => void;
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
  active = true,
  claimProgress,
  selected,
  onSelect,
  onOpenProof,
  onContextClaim,
  claimActions,
  detailsOpen,
  onDetailsChange,
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
  const proofClick = useRef<string | null>(null);
  const pendingDetails = useRef<{ id: string; timer: number } | null>(null);
  const inspected = useRef<{ id: string; width: number; height: number } | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0, visibleWidth: 0 });
  const { width: viewportWidth, height: viewportHeight } = size;
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, k: 1 });
  const unfittedCamera = useRef(camera);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [keyboardMove, setKeyboardMove] = useState<KeyboardMove | null>(null);
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
  const movingCard = keyboardMove && byId.get(keyboardMove.id);
  const moving = active && movingCard && !editingDisabled && !activeSource ? keyboardMove : null;
  const moveHint = t("projectCanvas.moveHint", {
    confirm: formatShortcut(SHORTCUTS.enter),
    cancel: formatShortcut(SHORTCUTS.escape),
  });
  const center = { x: size.width / 2, y: size.height / 2 };
  const mapBox = atlasPlacement(atlasBox ?? atlasOpeningBox, size.width, size.height, true);

  const dismissContextMenu = useCallback(() => {
    setContextTarget(null);
    onContextClaim(null);
  }, [onContextClaim]);

  const cancelPendingDetails = useCallback(() => {
    if (!pendingDetails.current) return false;
    window.clearTimeout(pendingDetails.current.timer);
    pendingDetails.current = null;
    return true;
  }, []);

  useEffect(
    () => () => {
      cancelPendingDetails();
    },
    [cancelPendingDetails],
  );

  useEffect(() => {
    const pending = pendingDetails.current;
    if (pending && (!active || detailsOpen || pending.id !== selected)) cancelPendingDetails();
  }, [active, cancelPendingDetails, detailsOpen, selected]);

  useEffect(() => {
    if (contextTarget && !contextCard) dismissContextMenu();
  }, [contextCard, contextTarget, dismissContextMenu]);

  useEffect(() => {
    if (keyboardMove && !moving) setKeyboardMove(null);
  }, [keyboardMove, moving]);

  useLayoutEffect(() => {
    if (visibleLinks.length || !removingLastLink.current) return;
    removingLastLink.current = false;
    if (document.activeElement === document.body) stage.current?.focus({ preventScroll: true });
  }, [visibleLinks.length]);

  useEffect(() => {
    const target = stage.current;
    if (!active || !target) return;
    const inspector = detailsOpen ? document.getElementById("review") : null;
    const observer = new ResizeObserver(() => {
      const { left, width, height } = target.getBoundingClientRect();
      const visibleWidth = inspector ? Math.min(width, inspector.getBoundingClientRect().left - left) : width;
      setSize((current) =>
        current.width === width && current.height === height && current.visibleWidth === visibleWidth
          ? current
          : { width, height, visibleWidth },
      );
    });
    observer.observe(target);
    if (inspector) observer.observe(inspector);
    return () => observer.disconnect();
  }, [active, detailsOpen]);

  const fit = useCallback(
    (all: boolean) => {
      const first = cards[0]?.point;
      if (!first || !viewportWidth || !viewportHeight) return;
      const left = Math.min(...cards.map(({ point }) => point.x));
      const top = Math.min(...cards.map(({ point }) => point.y));
      const width = Math.max(...cards.map(({ point }) => point.x)) - left + WIDTH;
      const height = Math.max(...cards.map(({ point }) => point.y)) - top + HEIGHT;
      const k = Math.min(1.15, Math.max(8, viewportWidth - 40) / width, Math.max(8, viewportHeight - 72) / height);
      if (!all && k < 0.6) {
        // Open at a readable scale on extensive maps; Fit to window can zoom out to the whole layout.
        const scale = 0.6;
        setCamera({ x: viewportWidth / 2 - (first.x + WIDTH / 2) * scale, y: 28 - first.y * scale, k: scale });
      } else {
        setCamera({
          x: (viewportWidth - width * k) / 2 - left * k,
          y: (viewportHeight - height * k) / 2 - top * k - 10,
          k,
        });
      }
    },
    [cards, viewportWidth, viewportHeight],
  );

  const lastAutomaticFit = useRef<typeof fit | null>(null);
  useEffect(() => {
    if (!active || movedCamera.current || lastAutomaticFit.current === fit) return;
    lastAutomaticFit.current = fit;
    fit(false);
  }, [active, fit]);

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

  useEffect(() => {
    if (!active) return;
    const id = detailsOpen ? selected : null;
    if (!id) {
      inspected.current = null;
      return;
    }
    const point = byId.get(id)?.point;
    const { visibleWidth: width, height } = size;
    if (!point || width <= 0 || height <= 0) return;
    const previous = inspected.current;
    if (previous?.id === id && previous.width === width && previous.height === height) return;
    inspected.current = { id, width, height };
    if (previous?.id !== id && contextTarget?.id === id) return;
    const neighbours = cards
      .filter((card) => card.id !== id)
      .map((card) => card.point)
      .sort((a, b) => Math.hypot(a.x - point.x, a.y - point.y) - Math.hypot(b.x - point.x, b.y - point.y));
    setCamera((current) => {
      const fitWidth = Math.max(1, width - 16);
      const fitHeight = Math.max(1, height - 16);
      const panelResized = previous?.width !== width || previous?.height !== height;
      let k = Math.min(current.k, fitWidth / WIDTH, fitHeight / HEIGHT);
      let left = point.x;
      let right = point.x + WIDTH;
      let top = point.y;
      let bottom = point.y + HEIGHT;
      for (const other of neighbours.slice(0, 2)) {
        const nextLeft = Math.min(left, other.x);
        const nextRight = Math.max(right, other.x + WIDTH);
        const nextTop = Math.min(top, other.y);
        const nextBottom = Math.max(bottom, other.y + HEIGHT);
        const fit = Math.min(k, fitWidth / (nextRight - nextLeft), fitHeight / (nextBottom - nextTop));
        if (fit < k && (!panelResized || fit < Math.min(current.k, 0.6))) continue;
        k = fit;
        [left, right, top, bottom] = [nextLeft, nextRight, nextTop, nextBottom];
      }
      const anchorX = point.x + WIDTH / 2;
      const anchorY = point.y + HEIGHT / 2;
      const x = visibleOffset(current.x + anchorX * (current.k - k), left * k, (right - left) * k, width);
      const y = visibleOffset(current.y + anchorY * (current.k - k), top * k, (bottom - top) * k, height);
      return x === current.x && y === current.y && k === current.k ? current : { x, y, k };
    });
  }, [active, byId, cards, detailsOpen, selected, size, contextTarget?.id]);

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
    cancelPendingDetails();
    setKeyboardMove(null);
    movedCamera.current = true;
    setContextTarget({ id, anchor });
    onContextClaim(id);
  }

  function moveAction(id: string): FloatingOption {
    const origin = byId.get(id)?.point;
    return graphAction(
      t,
      "move-card",
      origin && !editingDisabled && !activeSource
        ? () => {
            setKeyboardMove({ id, origin, point: origin });
            nodes.current.get(id)?.focus({ preventScroll: true });
          }
        : undefined,
    );
  }

  function linkAction(id: string | null): FloatingOption {
    const disabled = !activeSource && (!id || cards.length < 2 || editingDisabled);
    return graphAction(
      t,
      activeSource ? "cancel-link" : "link-claim",
      disabled
        ? undefined
        : () => {
            cancelPendingDetails();
            setLinkSource(activeSource ? null : id);
            setLinkNotice("");
          },
      disabled ? t("graphActions.linkUnavailable") : undefined,
    );
  }

  function choose(id: string, openDetails = true) {
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
    onSelect(id, openDetails);
  }

  function clickCard(event: MouseEvent<HTMLButtonElement>, id: string) {
    const chooseCard = !event.ctrlKey && (event.detail === 0 || draggedCard.current !== id);
    const plain = chooseCard && !activeSource && !event.metaKey && !event.altKey && !event.shiftKey;
    const open = plain && event.detail === 2 && proofClick.current === id && onOpenProof;
    proofClick.current = plain && event.detail === 1 ? id : null;
    draggedCard.current = null;
    cancelPendingDetails();
    if (open) {
      open(id);
      return;
    }
    if (!chooseCard) return;

    // The compact inspector overlays the map and moves cards into the remaining space.
    // Highlight immediately and keep the pointer target in place for a second click.
    if (
      plain &&
      event.detail === 1 &&
      onOpenProof &&
      !detailsOpen &&
      getComputedStyle(event.currentTarget).getPropertyValue("--map-inspector-overlay").trim() === "1"
    ) {
      choose(id, false);
      pendingDetails.current = {
        id,
        timer: window.setTimeout(() => {
          pendingDetails.current = null;
          onDetailsChange(true);
        }, DOUBLE_CLICK_MS),
      };
    } else {
      choose(id);
    }
  }

  function reveal(id: string) {
    const card = byId.get(id);
    if (!card) return;
    cancelPendingDetails();
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
    if (event.button !== 0 || event.ctrlKey || gesture.current || moving) return;
    if (pendingDetails.current?.id !== id) cancelPendingDetails();
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
    if (!current.moved) cancelPendingDetails();
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

  function moveByKey(event: KeyboardEvent<HTMLButtonElement>, move: KeyboardMove) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.nativeEvent.isComposing) return;
    const { id, origin, point } = move;
    if (event.key === SHORTCUTS.enter || event.key === SHORTCUTS.escape) {
      event.preventDefault();
      setKeyboardMove(null);
      if (event.key === SHORTCUTS.enter && !event.repeat && (point.x !== origin.x || point.y !== origin.y))
        onLayout({ action: "move", id, ...point });
      return;
    }
    const delta = directions[event.key];
    if (!delta) return;
    event.preventDefault();
    const step = event.shiftKey ? 32 : 8;
    movedCamera.current = true;
    setKeyboardMove((current) => {
      if (!current || current.id !== id) return current;
      return {
        ...current,
        point: {
          x: coordinate(current.point.x + delta[0] * step),
          y: coordinate(current.point.y + delta[1] * step),
        },
      };
    });
  }

  const canToggleDetails = Boolean(selected || detailsOpen);
  function toggleDetails() {
    cancelPendingDetails();
    movedCamera.current = true;
    onDetailsChange(!detailsOpen);
  }

  function onCanvasKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      if (cancelPendingDetails()) {
        proofClick.current = null;
        event.preventDefault();
      }
      if (activeSource) {
        setLinkSource(null);
        event.preventDefault();
      }
      return;
    }
    if (
      !event.repeat &&
      !event.altKey &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing &&
      event.target instanceof HTMLElement &&
      event.currentTarget.contains(event.target) &&
      !event.target.closest("input, select, textarea, [contenteditable]")
    ) {
      const key = event.key.toUpperCase();
      if (key === SHORTCUTS.details && canToggleDetails) {
        event.preventDefault();
        toggleDetails();
        return;
      }
      if (key === SHORTCUTS.atlas) {
        event.preventDefault();
        onToggleAtlas();
        return;
      }
    }
    if (event.target !== stage.current) return;
    const delta = directions[event.key];
    if (delta) {
      movedCamera.current = true;
      setCamera((current) => ({ ...current, x: current.x - delta[0] * 48, y: current.y - delta[1] * 48 }));
    } else if (event.key === "+" || event.key === "=") zoom(1.2);
    else if (event.key === "-") zoom(1 / 1.2);
    else if (event.key === SHORTCUTS.fit) fit(true);
    else return;
    event.preventDefault();
  }

  function position(id: string) {
    return moving?.id === id ? moving.point : preview?.id === id ? preview.point : byId.get(id)!.point;
  }

  const toolbarLink = linkAction(selected);

  return (
    <div className="project-canvas" onKeyDown={onCanvasKey}>
      <div className="project-canvas-tools">
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
          triggerClassName="button project-canvas-find"
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
        <div className="map-view-toggles">
          <MapViewToggle
            label={t("canvasShell.details")}
            shortcut={SHORTCUTS.details}
            pressed={detailsOpen}
            onToggle={toggleDetails}
            disabled={!canToggleDetails}
            details
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
              <path d="M14.5 4.5v15" />
            </svg>
          </MapViewToggle>
          <MapViewToggle
            label={t("sidebar.atlas")}
            shortcut={SHORTCUTS.atlas}
            pressed={atlasOpen}
            onToggle={onToggleAtlas}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="8" />
              <ellipse cx="12" cy="12" rx="3.6" ry="8" />
              <path d="M4 12h16" />
            </svg>
          </MapViewToggle>
        </div>
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
        {(moving || activeSource || linkNotice) && (
          <p className="project-canvas-message" role="status">
            {moving
              ? moveHint
              : activeSource
                ? t("projectCanvas.chooseLinkTarget", { title: activeSource.title })
                : linkNotice}
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
            const keyboardMoving = moving?.id === card.id;
            const movingShortcuts = keyboardMoving
              ? ` ${[...SHORTCUTS.arrows, SHORTCUTS.enter, SHORTCUTS.escape].join(" ")}`
              : "";
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
                data-moving={keyboardMoving || preview?.id === card.id ? "true" : undefined}
                aria-pressed={selected === card.id}
                aria-label={card.title}
                aria-description={[
                  progress.label,
                  progress.count ?? progress.emptyCountLabel,
                  progress.emptyCountHint,
                  card.domain,
                  keyboardMoving && moveHint,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                aria-keyshortcuts={menu["aria-keyshortcuts"] + (onOpenProof ? " Enter" : "") + movingShortcuts}
                title={card.title}
                style={{ left: at.x, top: at.y, width: WIDTH, height: HEIGHT }}
                onClick={(event) => {
                  if (!keyboardMoving) clickCard(event, card.id);
                }}
                onPointerDown={(event) => startGesture(event, card.id)}
                onBlur={() => {
                  if (keyboardMoving) setKeyboardMove(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === SHORTCUTS.enter && event.repeat) {
                    event.preventDefault();
                    return;
                  }
                  if (keyboardMoving) {
                    moveByKey(event, moving);
                    return;
                  }
                  menu.onKeyDown(event);
                  if (
                    !event.defaultPrevented &&
                    event.key === SHORTCUTS.enter &&
                    !activeSource &&
                    onOpenProof &&
                    !event.ctrlKey &&
                    !event.metaKey &&
                    !event.altKey &&
                    !event.shiftKey
                  ) {
                    event.preventDefault();
                    cancelPendingDetails();
                    onOpenProof(card.id);
                  }
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
          groups={claimActions(contextCard.id, linkAction(contextCard.id), moveAction(contextCard.id))}
          onDismiss={dismissContextMenu}
        />
      )}
    </div>
  );
}
