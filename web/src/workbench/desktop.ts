export type DesktopPosition = { claimId: string; nodeId: string };

type DesktopWindow = Window & {
  trellinPosition?: DesktopPosition;
  webkit?: { messageHandlers?: { trellinPosition?: { postMessage: (position: DesktopPosition) => void } } };
};

export function desktopPosition(): DesktopPosition | undefined {
  return (window as DesktopWindow).trellinPosition;
}

export function saveDesktopPosition(position: DesktopPosition): void {
  const desktop = window as DesktopWindow;
  const bridge = desktop.webkit?.messageHandlers?.trellinPosition;
  if (bridge) {
    desktop.trellinPosition = position;
    bridge.postMessage(position);
  }
}
