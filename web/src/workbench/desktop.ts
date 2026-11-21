export type DesktopPosition = { claimId: string; nodeId: string };
export type DesktopProject = { id: string; name: string; available: boolean };
type DesktopProjectList = { projects: DesktopProject[]; activeId?: string };
export type DesktopProjectAction = { action: "new" | "folder" } | { action: "open"; id: string };
export type DesktopProjectLibrary = {
  list: () => Promise<DesktopProjectList>;
  act: (request: DesktopProjectAction) => Promise<void>;
};

type DesktopWindow = Window & {
  trellinPosition?: DesktopPosition;
  trellinProjectName?: string;
  webkit?: {
    messageHandlers?: {
      trellinPosition?: { postMessage: (position: DesktopPosition) => void };
      trellinUpgradeProject?: { postMessage: (request: "confirm") => void };
      trellinProjects?: { postMessage: (request: DesktopProjectAction | { action: "list" }) => Promise<unknown> };
    };
  };
};

export function desktopProjectName(): string | undefined {
  const name = (window as DesktopWindow).trellinProjectName;
  return typeof name === "string" ? name.trim() || undefined : undefined;
}

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

function isProjectId(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
}

function isDesktopProject(value: unknown): value is DesktopProject {
  if (!value || typeof value !== "object") return false;
  const project = value as Partial<DesktopProject>;
  return isProjectId(project.id) && typeof project.name === "string" && typeof project.available === "boolean";
}

export function desktopProjectLibrary(): DesktopProjectLibrary | undefined {
  const bridge = (window as DesktopWindow).webkit?.messageHandlers?.trellinProjects;
  if (typeof bridge?.postMessage !== "function") return;
  return {
    async list() {
      const reply = (await bridge.postMessage({ action: "list" })) as Partial<DesktopProjectList> | null;
      if (!reply || !Array.isArray(reply.projects) || !reply.projects.every(isDesktopProject))
        throw new Error("Invalid desktop project list");
      return {
        projects: reply.projects,
        activeId: isProjectId(reply.activeId) ? reply.activeId.toLowerCase() : undefined,
      };
    },
    async act(request) {
      if (request.action === "open" && !isProjectId(request.id)) throw new Error("Invalid desktop project ID");
      if ((await bridge.postMessage(request)) !== true) throw new Error("Desktop project action unavailable");
    },
  };
}
