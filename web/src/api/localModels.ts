import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export function useLocalTextModels(session: string) {
  return useQuery({
    queryKey: ["project-intake-models", session],
    queryFn: ({ signal }) => api<{ models: string[] }>(session, "/api/project-intake/models", { signal }),
    retry: false,
  });
}
