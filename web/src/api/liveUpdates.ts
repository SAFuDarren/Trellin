import { createContext, useContext } from "react";

export const WORKSPACE_WRITES = "workspace-writes-v1";
export const LiveWorkspaceUpdates = createContext(false);

export function useFallbackInterval(milliseconds: number, streamHealthy = false): number | false {
  const live = useContext(LiveWorkspaceUpdates);
  return live || streamHealthy ? false : milliseconds;
}
