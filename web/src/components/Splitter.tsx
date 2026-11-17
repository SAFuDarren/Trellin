import { useRef } from "react";
import { LIMITS, type SizeKey } from "../workbench/useLayout";

/** A draggable, keyboard-adjustable border between two panels. */
export function Splitter({
  name,
  value,
  label,
  onResize,
  onCommit,
  hidden,
}: {
  name: SizeKey;
  value: number;
  label: string;
  onResize: (value: number) => void;
  onCommit: (value: number) => void;
  hidden?: boolean;
}) {
  const dragging = useRef(false);
  const horizontal = name === "chat";
  const startDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const target = event.currentTarget;
    const start = { x: event.clientX, y: event.clientY, value };
    let latest = value;
    target.setPointerCapture(event.pointerId);
    target.dataset.dragging = "true";
    dragging.current = true;
    const move = (moveEvent: PointerEvent) => {
      const delta =
        name === "side"
          ? moveEvent.clientX - start.x
          : name === "right"
            ? start.x - moveEvent.clientX
            : start.y - moveEvent.clientY;
      latest = start.value + delta;
      onResize(latest);
    };
    const stop = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", stop);
      target.removeEventListener("pointercancel", stop);
      delete target.dataset.dragging;
      dragging.current = false;
      onCommit(latest);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", stop);
    target.addEventListener("pointercancel", stop);
    event.preventDefault();
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const grow = {
      side: ["ArrowRight", "ArrowLeft"],
      right: ["ArrowLeft", "ArrowRight"],
      chat: ["ArrowUp", "ArrowDown"],
    }[name];
    const step = event.shiftKey ? 48 : 16;
    if (event.key === grow[0]) onCommit(value + step);
    else if (event.key === grow[1]) onCommit(value - step);
    else return;
    event.preventDefault();
  };
  return (
    <div
      className={horizontal ? "splitter horizontal" : "splitter"}
      data-split={name}
      role="separator"
      aria-orientation={horizontal ? "horizontal" : "vertical"}
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={LIMITS[name][0]}
      aria-valuemax={LIMITS[name][1]}
      tabIndex={0}
      hidden={hidden}
      onPointerDown={startDrag}
      onKeyDown={onKeyDown}
    />
  );
}
