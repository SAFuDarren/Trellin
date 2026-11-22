// Panel sizes and toggles, remembered per browser (same storage key and shape as the previous interface).
import { useCallback, useEffect, useState } from "react";
import { clampCardSize, type CardSize } from "./graph";

export type CardSizes = Record<string, CardSize>;
type CardLayouts = Record<string, CardSizes>;

export type AtlasBox = { x: number; y: number; w: number };
export type Sizes = {
  side: number;
  right: number;
  chat: number;
  chatOpen: boolean;
  atlasOpen: boolean;
  reviewOpen: boolean | null;
  atlas: AtlasBox | null;
  cards: CardLayouts;
};
export type SizeKey = "side" | "right" | "chat";

const KEY = "research-multiplier-workbench-layout";
export const DEFAULT_SIZES: Sizes = {
  side: 240,
  right: 340,
  chat: 320,
  chatOpen: true,
  atlasOpen: true,
  reviewOpen: null,
  atlas: null,
  cards: {},
};
export const LIMITS: Record<SizeKey, [number, number]> = { side: [180, 420], right: [280, 640], chat: [140, 640] };

function entries(value: unknown): [string, unknown][] {
  return value && typeof value === "object" && !Array.isArray(value) ? Object.entries(value) : [];
}

function restoreCards(saved: unknown): CardLayouts {
  return Object.fromEntries(
    entries(saved).map(([scope, cards]) => [
      scope,
      Object.fromEntries(
        entries(cards).flatMap(([id, savedSize]) => {
          const size = savedSize as Partial<CardSize> | null;
          return size &&
            typeof size.width === "number" &&
            typeof size.height === "number" &&
            Number.isFinite(size.width) &&
            Number.isFinite(size.height)
            ? [[id, clampCardSize({ width: size.width, height: size.height })]]
            : [];
        }),
      ),
    ]),
  );
}

function load(): Sizes {
  const sizes = { ...DEFAULT_SIZES };
  try {
    const saved = JSON.parse(window.localStorage.getItem(KEY) || "{}") as Partial<Record<keyof Sizes, unknown>>;
    for (const key of ["side", "right", "chat"] as const)
      if (typeof saved[key] === "number") sizes[key] = saved[key] as number;
    for (const key of ["chatOpen", "atlasOpen", "reviewOpen"] as const) {
      if (typeof saved[key] === "boolean") (sizes[key] as boolean) = saved[key] as boolean;
    }
    const atlas = saved.atlas as Partial<AtlasBox> | null | undefined;
    if (atlas && ["x", "y", "w"].every((key) => typeof atlas[key as keyof AtlasBox] === "number"))
      sizes.atlas = atlas as AtlasBox;
    sizes.cards = restoreCards(saved.cards);
  } catch {
    // Defaults stay in place.
  }
  return sizes;
}

export function clampSize(key: SizeKey, value: number, windowHeight: number): number {
  const [low, high] = LIMITS[key];
  // The conversation never takes more than about half the window, so the graph keeps room.
  const ceiling = key === "chat" ? Math.min(high, Math.round(windowHeight * 0.45)) : high;
  return Math.round(Math.max(low, Math.min(Math.max(low, ceiling), value)));
}

/** Forget saved panel sizes; the workbench starts from the defaults next time it opens. */
export function resetLayout(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing was stored.
  }
}

export function useLayout() {
  const [sizes, setSizes] = useState<Sizes>(load);
  const [windowSize, setWindowSize] = useState({ width: window.innerWidth, height: window.innerHeight });
  useEffect(() => {
    const onResize = () => setWindowSize({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const save = useCallback((next: Sizes) => {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Sizes still apply for this visit.
    }
  }, []);
  const update = useCallback(
    (change: Partial<Sizes>, persist = true) => {
      setSizes((current) => {
        const next = { ...current, ...change };
        if (persist) save(next);
        return next;
      });
    },
    [save],
  );
  const resizeCard = useCallback(
    (scope: string, id: string, size: CardSize) => {
      setSizes((current) => {
        const next = {
          ...current,
          cards: { ...current.cards, [scope]: { ...current.cards[scope], [id]: size } },
        };
        save(next);
        return next;
      });
    },
    [save],
  );
  const reset = useCallback(() => {
    setSizes(DEFAULT_SIZES);
    save(DEFAULT_SIZES);
  }, [save]);
  // Without a saved choice, the inspector folds away on narrow windows.
  const reviewOpen = sizes.reviewOpen ?? windowSize.width >= 760;
  const clamped = {
    side: clampSize("side", sizes.side, windowSize.height),
    right: clampSize("right", sizes.right, windowSize.height),
    chat: clampSize("chat", sizes.chat, windowSize.height),
  };
  return { sizes, clamped, reviewOpen, windowHeight: windowSize.height, update, resizeCard, reset };
}
