export type DesktopPosition = { claimId: string; nodeId: string };

type DesktopWindow = Window & {
  trellinPosition?: DesktopPosition;
  webkit?: {
    messageHandlers?: {
      trellinPosition?: { postMessage: (position: DesktopPosition) => void };
      trellinUpgradeProject?: { postMessage: (request: "confirm") => void };
    };
  };
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

export function desktopProjectUpgrade(): (() => void) | undefined {
  const bridge = (window as DesktopWindow).webkit?.messageHandlers?.trellinUpgradeProject;
  return bridge && (() => bridge.postMessage("confirm"));
}
