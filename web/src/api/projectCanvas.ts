import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Claim } from "./client";

type Point = { x: number; y: number };
export type ResearchLink = { source: string; target: string };
export type ProjectCanvasLayout = { positions: Record<string, Point>; links: ResearchLink[] };
export type ObligationCount = { verified: number; total: number };
/** #342: the decisive evidence kind and its review, and the kinds of other verified evidence, as recorded. */
export type ClaimAssurance = { kind: string | null; review: string | null; supporting: string[] };
export type ProjectClaim = Claim & { obligations?: ObligationCount; assurance?: ClaimAssurance; uses?: string[] };
export type ProjectCanvasOperation =
  | ({ action: "move"; id: string } & Point)
  | ({ action: "link" | "unlink" } & ResearchLink);

const PATH = "/api/project-canvas";
const EMPTY: ProjectCanvasLayout = { positions: {}, links: [] };

export function orderedResearchLink({ source, target }: ResearchLink): ResearchLink {
  return source < target ? { source, target } : { source: target, target: source };
}

export function sameResearchLink(a: ResearchLink | null, b: ResearchLink): boolean {
  return !!a && ((a.source === b.source && a.target === b.target) || (a.source === b.target && a.target === b.source));
}

function preview(layout: ProjectCanvasLayout, operation: ProjectCanvasOperation): ProjectCanvasLayout {
  if (operation.action === "move") {
    return { ...layout, positions: { ...layout.positions, [operation.id]: { x: operation.x, y: operation.y } } };
  }
  const link = orderedResearchLink(operation);
  const others = layout.links.filter((candidate) => !sameResearchLink(candidate, link));
  return { ...layout, links: operation.action === "link" ? [...others, link] : others };
}

export function useProjectCanvasClaims(session: string, enabled: boolean) {
  return useQuery({
    queryKey: ["project-canvas-claims", session],
    queryFn: ({ signal }) => api<{ claims: ProjectClaim[] }>(session, `${PATH}/claims`, { signal }),
    enabled,
    retry: false,
  });
}

export function useProjectCanvas(session: string, enabled: boolean) {
  const queries = useQueryClient();
  const key = ["project-canvas", session];
  const busy = useRef(false);
  const index = useProjectCanvasClaims(session, enabled);
  const canvas = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => api<ProjectCanvasLayout>(session, PATH, { signal }),
    enabled,
    retry: false,
  });
  const save = useMutation({
    mutationFn: (change: ProjectCanvasOperation) => api<ProjectCanvasLayout>(session, PATH, { data: change }),
    onSuccess: (result) => {
      queries.setQueryData(key, result);
    },
    onError: () => {
      void queries.invalidateQueries({ queryKey: key });
    },
    onSettled: () => {
      busy.current = false;
    },
  });
  return {
    claims: index.data?.claims,
    loadingClaims: index.isPending,
    layout: save.isPending && save.variables ? preview(canvas.data ?? EMPTY, save.variables) : (canvas.data ?? EMPTY),
    loading: canvas.isPending,
    saving: save.isPending,
    loadError: canvas.error,
    indexError: index.error,
    saveError: save.error,
    reload: () => {
      save.reset();
      void index.refetch();
      void canvas.refetch();
    },
    apply: (operation: ProjectCanvasOperation, onSaved?: (layout: ProjectCanvasLayout) => void) => {
      if (!canvas.isSuccess || busy.current) return;
      busy.current = true;
      save.mutate(operation, { onSuccess: onSaved });
    },
  };
}
