import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";
import type { Claim } from "../api/client";
import type { ProjectCanvasLayout, ProjectCanvasOperation } from "../api/projectCanvas";
import type { Point } from "../workbench/graph";
import { nodeStatus, nodeTitle, nodeTone } from "../workbench/text";
import { Mark } from "./Icons";
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

type Props = {
  claims: Claim[];
  selected: string | null;
  onSelect: (id: string) => void;
  onOpenClaim: (id: string) => void;
  layout: ProjectCanvasLayout;
  onLayout: (edit: ProjectCanvasOperation) => void;
  layoutUnavailable?: boolean;
  saving?: boolean;
};

export function ProjectCanvas({
  claims,
  selected,
  onSelect,
  onOpenClaim,
  layout,
  onLayout,
  layoutUnavailable,
  saving,
}: Props) {
  const { t, i18n } = useTranslation();
  const stage = useRef<HTMLDivElement>(null);
  const searchMenu = useRef<HTMLDetailsElement>(null);
  const linksMenu = useRef<HTMLDetailsElement>(null);
  const nodes = useRef(new Map<string, HTMLButtonElement>());
  const movedCamera = useRef(false);
  const gesture = useRef<Gesture | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, k: 1 });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [panning, setPanning] = useState(false);
  const [linkSource, setLinkSource] = useState<string | null>(null);
  const [linkNotice, setLinkNotice] = useState("");
  const [query, setQuery] = useState("");

  const cards = useMemo(() => {
    // Titles may change. A stable claim identifier keeps the fallback placement deterministic.
    const ordered = [...claims].sort((a, b) => a.id.localeCompare(b.id));
    const columns = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(ordered.length))));
    return ordered.map((claim, index) => {
      const node = { id: `goal:${claim.id}`, kind: "goal", title: claim.title ?? undefined, status: claim.status };
      return {
        id: claim.id,
        title: nodeTitle(t, node, { full: true }),
        status: nodeStatus(t, node),
        tone: nodeTone(node, false),
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
  const editingDisabled = Boolean(layoutUnavailable || saving);
  const normalizedQuery = query.trim().toLocaleLowerCase(i18n.language);
  const matches = cards.filter((card) => card.title.toLocaleLowerCase(i18n.language).includes(normalizedQuery));
  const center = { x: size.width / 2, y: size.height / 2 };

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

  useEffect(() => {
    const target = stage.current;
    if (!target) return;
    const wheel = (event: WheelEvent) => {
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
    onSelect(id);
  }

  function reveal(id: string) {
    const card = byId.get(id);
    if (!card) return;
    choose(id);
    movedCamera.current = true;
    setCamera((current) => ({
      ...current,
      x: center.x - (card.point.x + WIDTH / 2) * current.k,
      y: center.y - (card.point.y + HEIGHT / 2) * current.k,
    }));
    if (searchMenu.current) searchMenu.current.open = false;
    nodes.current.get(id)?.focus({ preventScroll: true });
  }

  function startGesture(event: PointerEvent<HTMLElement>, id?: string) {
    if (event.button !== 0 || gesture.current) return;
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
    if (event.key === "Escape") {
      if (activeSource) {
        setLinkSource(null);
        event.preventDefault();
      } else {
        const menu = searchMenu.current?.open ? searchMenu.current : linksMenu.current;
        if (menu?.open) {
          menu.open = false;
          menu.querySelector("summary")?.focus();
          event.preventDefault();
        }
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

  return (
    <div className="project-canvas" onKeyDown={onCanvasKey}>
      <div className="project-canvas-tools">
        <button type="button" className="button" disabled={!selected} onClick={() => selected && onOpenClaim(selected)}>
          {t("projectCanvas.openProof")}
        </button>
        <button
          type="button"
          className="button"
          disabled={!activeSource && (!selected || cards.length < 2 || editingDisabled)}
          onClick={(event) => {
            event.currentTarget.focus();
            setLinkSource(activeSource ? null : selected);
            setLinkNotice("");
          }}
        >
          {t(activeSource ? "projectCanvas.cancelLink" : "projectCanvas.linkClaim")}
        </button>
        <details className="project-canvas-menu" ref={searchMenu}>
          <summary role="button" onClick={() => linksMenu.current?.removeAttribute("open")}>
            {t("projectCanvas.findClaim")}
          </summary>
          <div className="project-canvas-popover">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              aria-label={t("projectCanvas.searchClaims")}
              placeholder={t("projectCanvas.searchClaims")}
            />
            {matches.length ? (
              <ul>
                {matches.map((card) => (
                  <li key={card.id}>
                    <button type="button" className="project-canvas-menu-item" onClick={() => reveal(card.id)}>
                      {card.title}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p>{t("projectCanvas.noMatches")}</p>
            )}
          </div>
        </details>
        {visibleLinks.length > 0 && (
          <details className="project-canvas-menu" ref={linksMenu}>
            <summary role="button" onClick={() => searchMenu.current?.removeAttribute("open")}>
              {t("projectCanvas.researchLinks")}
            </summary>
            <ul className="project-canvas-popover project-canvas-link-list">
              {visibleLinks.map(({ source, target }) => {
                const params = { source: byId.get(source)!.title, target: byId.get(target)!.title };
                return (
                  <li key={`${source}:${target}`}>
                    <span>{t("projectCanvas.linkDescription", params)}</span>
                    <button
                      type="button"
                      className="button"
                      disabled={editingDisabled}
                      aria-label={t("projectCanvas.removeLink", params)}
                      onClick={() => onLayout({ action: "unlink", source, target })}
                    >
                      {t("projectCanvas.remove")}
                    </button>
                  </li>
                );
              })}
            </ul>
          </details>
        )}
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
          if (!(event.target as Element).closest("button")) startGesture(event);
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
            return (
              <button
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
                aria-description={card.status}
                aria-keyshortcuts={layoutUnavailable ? undefined : "ArrowLeft ArrowRight ArrowUp ArrowDown"}
                title={card.title}
                style={{ left: at.x, top: at.y, width: WIDTH, height: HEIGHT }}
                onClick={() => choose(card.id)}
                onPointerDown={(event) => startGesture(event, card.id)}
                onKeyDown={(event) => moveByKey(event, card.id, at)}
              >
                <span className="project-canvas-title">{card.title}</span>
                <span className="project-canvas-meta">
                  <Mark tone={card.tone} />
                  <span>{card.status}</span>
                  {card.domain && <span className="project-canvas-domain">{card.domain}</span>}
                </span>
              </button>
            );
          })}
        </div>
        {!cards.length && <p className="project-canvas-empty">{t("notice.noClaims")}</p>}
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
    </div>
  );
}
