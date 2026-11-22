import { useLayoutEffect, useState, type RefObject } from "react";

export type ChatSize = { width: number; height: number };
type Area = { left: number; top: number; right: number; bottom: number };

const STORAGE_KEY = "research-multiplier-agent-chat-size";
const GAP = 12;
const MAX_SIZE = 4096;
export const DEFAULT_CHAT_SIZE = { width: 432, height: 540 };
export const MIN_CHAT_SIZE = { width: 280, height: 180 };

type ChatWindow = Window & {
  trellinAgentChatSize?: unknown;
  webkit?: { messageHandlers?: { trellinAgentChatSize?: { postMessage: (size: ChatSize) => void } } };
};

function restoredSize(value: unknown): ChatSize | undefined {
  if (!value || typeof value !== "object") return;
  const { width, height } = value as Partial<ChatSize>;
  if (
    typeof width === "number" &&
    Number.isInteger(width) &&
    width >= MIN_CHAT_SIZE.width &&
    width <= MAX_SIZE &&
    typeof height === "number" &&
    Number.isInteger(height) &&
    height >= MIN_CHAT_SIZE.height &&
    height <= MAX_SIZE
  )
    return { width, height };
}

export function readAgentChatSize(): ChatSize | undefined {
  const desktop = window as ChatWindow;
  if (desktop.webkit?.messageHandlers?.trellinAgentChatSize) return restoredSize(desktop.trellinAgentChatSize);
  try {
    return restoredSize(JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null"));
  } catch {
    return undefined;
  }
}

export function saveAgentChatSize(size: ChatSize) {
  const desktop = window as ChatWindow;
  const bridge = desktop.webkit?.messageHandlers?.trellinAgentChatSize;
  if (bridge) {
    desktop.trellinAgentChatSize = size;
    bridge.postMessage(size);
    return;
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(size));
  } catch {
    // The chosen size still applies to this window.
  }
}

function visible(element: Element | null | undefined): element is Element {
  return Boolean(element && !element.closest("[inert], [hidden]") && element.getClientRects().length);
}

function areaSize(area: Area) {
  return {
    width: Math.max(0, Math.floor(area.right - area.left)),
    height: Math.max(0, Math.floor(area.bottom - area.top)),
  };
}

function avoidAtlas(area: Area, atlas: DOMRect): Area {
  if (atlas.right <= area.left || atlas.left >= area.right || atlas.bottom <= area.top || atlas.top >= area.bottom)
    return area;
  const candidates = [
    { ...area, left: atlas.right + GAP },
    { ...area, top: atlas.bottom + GAP },
    { ...area, right: atlas.left - GAP },
    { ...area, bottom: atlas.top - GAP },
  ];
  const space = (candidate: Area) => {
    const { width, height } = areaSize(candidate);
    return width * height;
  };
  const usable = candidates.filter((candidate) => {
    const { width, height } = areaSize(candidate);
    return width >= MIN_CHAT_SIZE.width && height >= MIN_CHAT_SIZE.height;
  });
  return (usable.length ? usable : candidates).sort((a, b) => space(b) - space(a))[0]!;
}

function measure(shell: HTMLElement, dock: HTMLElement, workbench: boolean) {
  const main = shell.querySelector("main");
  const stage = workbench
    ? main?.querySelector(".project-map-area:not([inert]) .stage, .main-proof-graph .stage")
    : undefined;
  const zoom = stage?.querySelector(".zoom") ?? stage?.parentElement?.querySelector(".zoom");
  const atlas = stage?.querySelector(".atlas");
  const inspector = workbench ? shell.querySelector("#review") : main?.querySelector(".split > aside.aside");
  const areaElement = visible(stage) ? stage : main;
  const bounds = areaElement?.getBoundingClientRect();
  const dockBounds = dock.getBoundingClientRect();
  const area = {
    left: (bounds?.width ? bounds.left : 0) + GAP,
    top: (bounds?.height ? bounds.top : 0) + GAP,
    right: (bounds?.width ? bounds.right : window.innerWidth) - GAP,
    bottom:
      Math.min(
        bounds?.height ? bounds.bottom : window.innerHeight,
        dockBounds.height ? dockBounds.top : window.innerHeight,
      ) - GAP,
  };
  if (visible(inspector)) {
    const rect = inspector.getBoundingClientRect();
    if (rect.top < area.bottom && rect.bottom > area.top) {
      if (rect.left > area.left && rect.left < area.right) area.right = rect.left - GAP;
      else if (rect.top > area.top) area.bottom = Math.min(area.bottom, rect.top - GAP);
    }
  }
  if (visible(zoom)) area.bottom = Math.min(area.bottom, zoom.getBoundingClientRect().top - GAP);
  const available = visible(atlas) ? avoidAtlas(area, atlas.getBoundingClientRect()) : area;
  return {
    ...areaSize(available),
    right: Math.max(0, window.innerWidth - available.right),
    bottom: Math.max(0, window.innerHeight - available.bottom),
  };
}

export function useAgentChatArea(dock: RefObject<HTMLElement | null>, open: boolean, section: string, sub: string) {
  const [area, setArea] = useState({ ...DEFAULT_CHAT_SIZE, right: GAP, bottom: GAP });
  useLayoutEffect(() => {
    const footer = dock.current;
    const shell = footer?.previousElementSibling;
    if (!open || !footer || !(shell instanceof HTMLElement)) return;
    let frame = 0;
    const observed = new Set<Element>();
    const observer = "ResizeObserver" in window ? new ResizeObserver(schedule) : undefined;
    const atlasUpdates = new MutationObserver(schedule);
    let atlas: Element | null = null;

    function update() {
      if (!footer || !(shell instanceof HTMLElement)) return;
      const next = measure(shell, footer, section === "workbench");
      setArea((current) =>
        current.width === next.width &&
        current.height === next.height &&
        current.bottom === next.bottom &&
        current.right === next.right
          ? current
          : next,
      );
      const currentAtlas = shell.querySelector(".project-map-area:not([inert]) .atlas");
      if (currentAtlas !== atlas) {
        atlasUpdates.disconnect();
        if (currentAtlas) atlasUpdates.observe(currentAtlas, { attributes: true, attributeFilter: ["style"] });
        atlas = currentAtlas;
      }
      const elements = new Set<Element>([
        footer,
        shell,
        ...shell.querySelectorAll("main, #review, .split > aside.aside, .stage, .zoom, .atlas"),
      ]);
      for (const element of observed)
        if (!elements.has(element)) {
          observer?.unobserve(element);
          observed.delete(element);
        }
      for (const element of elements)
        if (!observed.has(element)) {
          observed.add(element);
          observer?.observe(element);
        }
    }

    function schedule() {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        update();
      });
    }

    update();
    const mutations = new MutationObserver(schedule);
    mutations.observe(shell, { childList: true, subtree: true, attributes: true, attributeFilter: ["inert"] });
    shell.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    return () => {
      cancelAnimationFrame(frame);
      mutations.disconnect();
      observer?.disconnect();
      atlasUpdates.disconnect();
      shell.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
    };
  }, [dock, open, section, sub]);
  return area;
}

function cap(value: number, minimum: number, maximum: number) {
  return Math.round(Math.max(minimum, Math.min(value, maximum, MAX_SIZE)));
}

export function resizeChatSize(size: ChatSize, available: ChatSize): ChatSize {
  return {
    width: cap(size.width, MIN_CHAT_SIZE.width, available.width),
    height: cap(size.height, MIN_CHAT_SIZE.height, available.height),
  };
}
