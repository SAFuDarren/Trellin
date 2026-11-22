import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { MIN_CHAT_SIZE, resizeChatSize, type ChatSize } from "../shell/agentChatGeometry";

type Edge = "top" | "left" | "corner";

export function AgentChatResizeHandles({
  panel,
  preferred,
  available,
  onSize,
  onCommit,
}: {
  panel: RefObject<HTMLElement | null>;
  preferred: ChatSize | undefined;
  available: ChatSize;
  onSize: (size: ChatSize) => void;
  onCommit: (size: ChatSize) => void;
}) {
  const { t } = useTranslation();
  const [measured, setMeasured] = useState<ChatSize | undefined>();
  useLayoutEffect(() => {
    const element = panel.current;
    if (!element) return;
    const measure = () => {
      const { width, height } = element.getBoundingClientRect();
      setMeasured({ width: Math.round(width), height: Math.round(height) });
    };
    measure();
    if (!("ResizeObserver" in window)) return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [panel]);
  const drag = useRef<{
    pointer: number;
    edge: Edge;
    x: number;
    y: number;
    actual: ChatSize;
    original: ChatSize | undefined;
    latest: ChatSize | undefined;
  } | null>(null);

  function size() {
    const rect = panel.current?.getBoundingClientRect();
    return { width: rect?.width ?? available.width, height: rect?.height ?? available.height };
  }

  function adjust(edge: Edge, actual: ChatSize, saved: ChatSize | undefined, dx: number, dy: number) {
    const clamped = resizeChatSize({ width: actual.width + dx, height: actual.height + dy }, available);
    return {
      width: edge === "top" && saved ? saved.width : clamped.width,
      height: edge === "left" && saved ? saved.height : clamped.height,
    };
  }

  function start(event: PointerEvent<HTMLDivElement>, edge: Edge) {
    if (event.button !== 0 || drag.current) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointer: event.pointerId,
      edge,
      x: event.clientX,
      y: event.clientY,
      actual: size(),
      original: preferred,
      latest: undefined,
    };
    event.currentTarget.dataset.dragging = "true";
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    const dx = current.edge === "top" ? 0 : current.x - event.clientX;
    const dy = current.edge === "left" ? 0 : current.y - event.clientY;
    if (!current.latest && !dx && !dy) return;
    current.latest = adjust(current.edge, current.actual, current.original, dx, dy);
    onSize(current.latest);
  }

  function finish(event: PointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    drag.current = null;
    delete event.currentTarget.dataset.dragging;
    if (current.latest) onCommit(current.latest);
  }

  function key(event: KeyboardEvent<HTMLDivElement>, edge: Exclude<Edge, "corner">) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.nativeEvent.isComposing) return;
    const arrows = edge === "left" ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
    const direction = arrows.indexOf(event.key);
    if (direction < 0) return;
    event.preventDefault();
    const delta = (direction === 0 ? 1 : -1) * (event.shiftKey ? 48 : 16);
    onCommit(adjust(edge, size(), preferred, edge === "left" ? delta : 0, edge === "top" ? delta : 0));
  }

  return (
    <>
      {(["top", "left", "corner"] as const).map((edge) => {
        const axis = edge === "top" ? "height" : "width";
        return (
          <div
            key={edge}
            className="agent-chat-resize"
            data-edge={edge}
            aria-hidden={edge === "corner" || undefined}
            role={edge === "corner" ? undefined : "separator"}
            tabIndex={edge === "corner" ? undefined : 0}
            aria-orientation={edge === "corner" ? undefined : edge === "top" ? "horizontal" : "vertical"}
            aria-label={edge === "corner" ? undefined : t(`agentDock.resize.${axis}`)}
            aria-valuemin={edge === "corner" ? undefined : Math.min(MIN_CHAT_SIZE[axis], available[axis])}
            aria-valuemax={edge === "corner" ? undefined : available[axis]}
            aria-valuenow={edge === "corner" ? undefined : measured?.[axis]}
            aria-keyshortcuts={
              edge === "corner" ? undefined : edge === "top" ? "ArrowUp ArrowDown" : "ArrowLeft ArrowRight"
            }
            onPointerDown={(event) => start(event, edge)}
            onPointerMove={move}
            onPointerUp={finish}
            onPointerCancel={finish}
            onLostPointerCapture={finish}
            onKeyDown={edge === "corner" ? undefined : (event) => key(event, edge)}
          />
        );
      })}
    </>
  );
}
