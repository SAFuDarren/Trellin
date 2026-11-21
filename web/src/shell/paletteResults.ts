import { createElement, useEffect, useState } from "react";
import { queryOptions, useQueries, useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { api } from "../api/client";
import { useConversations } from "../api/conversations";
import { useProjectCanvasClaims } from "../api/projectCanvas";
import { useAtlas, useClaimGraph, type LibraryItem } from "../api/sections";
import { useTeamAgents } from "../api/teamAgents";
import { workspaceId as isWorkspaceId } from "../api/workspaceEvents";
import type { FloatingGroup, FloatingOption } from "../components/FloatingControls";
import { WorkbenchIcon } from "../components/Icons";
import { itemName } from "../pages/LibraryPage";
import { nodeTitle, normalizeReferenceCode } from "../workbench/text";
import type { GraphNode } from "../workbench/types";
import { paletteQuery, type PaletteScope } from "./paletteQuery";
import type { Section } from "./route";

const REFERENCE = /^[A-Z][A-Z0-9]{1,11}(?:-[0-9]{1,10})?$/;
const REFERENCE_ID = "reference:";
const NODE_ID = "node:";
const RECENT_LIMIT = 6;
const EMPTY_RECENTS: readonly string[] = [];
type ResolvedReference = { claim_id: string; node_id: string | null };

function readRecents(key: string | undefined): readonly string[] {
  if (!key) return EMPTY_RECENTS;
  try {
    const data: unknown = JSON.parse(window.localStorage.getItem(key) ?? "null");
    return Array.isArray(data) ? data.filter((id): id is string => typeof id === "string").slice(0, RECENT_LIMIT) : [];
  } catch {
    return EMPTY_RECENTS;
  }
}

export function usePaletteResults({
  session,
  workspaceId,
  claimId,
  query,
  enabled,
  t,
  open,
  go,
}: {
  session: string;
  workspaceId?: string | null;
  claimId?: string | null;
  query: string;
  enabled: boolean;
  t: TFunction;
  open: (claimId: string, nodeId?: string) => void;
  go: (section: Section, sub?: string) => void;
}): { groups: FloatingGroup[]; isLoading: boolean; scoped: boolean } {
  const project = isWorkspaceId(workspaceId) ? workspaceId : undefined;
  const key = project ? "trellin-palette-recent:" + project : undefined;
  const [stored, setStored] = useState(() => ({ session, project, ids: readRecents(key) }));
  const ids = !enabled
    ? EMPTY_RECENTS
    : stored.session === session && stored.project === project
      ? stored.ids
      : readRecents(key);
  const { scope, text } = paletteQuery(query);
  // Copied prose can contain a typographic dash. Send the resolver its canonical ASCII query.
  const code = enabled && scope !== "agents" ? normalizeReferenceCode(text).replace(/[‐‑‒–—−－]/g, "-") : "";
  const exact = REFERENCE.test(code) ? code : "";
  const [settledReference, setSettledReference] = useState("");
  useEffect(() => {
    if (!enabled || !exact) return;
    const timer = window.setTimeout(() => setSettledReference(exact), 160);
    return () => window.clearTimeout(timer);
  }, [enabled, exact]);

  const claims = useProjectCanvasClaims(session, enabled);
  const searchNodes = enabled && scope !== "agents" && (Boolean(text) || ids.some((id) => id.startsWith(NODE_ID)));
  const atlas = useAtlas(session, searchNodes);
  const snapshot = useClaimGraph(session, claimId ?? null, searchNodes);
  const library = useQuery({
    queryKey: ["library", session, project],
    queryFn: ({ signal }) => api<{ items: LibraryItem[] }>(session, "/api/library", { signal }),
    enabled,
    retry: false,
  });
  const conversations = useConversations(session);
  const agents = useTeamAgents(session, enabled);

  const recentReferences = ids
    .filter((id) => id.startsWith(REFERENCE_ID))
    .map((id) => id.slice(REFERENCE_ID.length))
    .filter((reference) => REFERENCE.test(reference));
  const codes = [...new Set([...(exact && exact === settledReference ? [exact] : []), ...recentReferences])];
  const references = useQueries({
    queries: codes.map((reference) =>
      queryOptions({
        queryKey: ["palette-reference", session, project, reference],
        queryFn: ({ signal }) =>
          api<ResolvedReference>(session, "/api/claim-code?" + new URLSearchParams({ code: reference }), { signal }),
        enabled,
        retry: false,
      }),
    ),
  });
  if (!enabled) return { groups: [], isLoading: false, scoped: false };

  function remember(id: string) {
    const next = [id, ...ids.filter((item) => item !== id)].slice(0, RECENT_LIMIT);
    setStored({ session, project, ids: next });
    if (!key) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // Keep the list available for this session when local storage is disabled.
    }
  }

  function result(id: string, label: string, action: () => void, detail?: string, searchText?: string): FloatingOption {
    return {
      id,
      label,
      detail,
      searchText,
      onSelect: () => {
        remember(id);
        action();
      },
    };
  }

  const currentClaims = claims.data?.claims ?? [];
  const currentConversations = conversations.data?.threads ?? [];
  const claimTitles = new Map(currentClaims.map((claim) => [claim.id, claim.title || t("sidebar.untitled")]));
  const indexedClaims = atlas.data?.claims ?? [];
  const currentNodes =
    snapshot.data?.claim.id === claimId ? snapshot.data?.nodes : indexedClaims.find(({ id }) => id === claimId)?.nodes;
  const resolved = references[codes.indexOf(exact)]?.data;
  function proofNodes(id: string, nodes: readonly GraphNode[]): FloatingOption[] {
    const title = claimTitles.get(id);
    if (!title) return [];
    return nodes.flatMap((node) => {
      const optionId = NODE_ID + id + ":" + node.id;
      if ((!text && !ids.includes(optionId)) || (resolved?.claim_id === id && resolved.node_id === node.id)) return [];
      const label = nodeTitle(t, node, { full: true });
      const detail = [title, node.code].filter(Boolean).join(" · ");
      return [
        {
          ...result(optionId, label, () => open(id, node.id), detail),
          matchText: [label, node.title, node.citation, node.code].filter(Boolean).join(" "),
        },
      ];
    });
  }
  const agentNames = new Map(agents.data?.agents.map((agent) => [agent.id, agent.name]));
  const firstConversationByAgent = new Map<string, string>();
  currentConversations.forEach(({ id, agent_id }) => {
    if (agent_id && !firstConversationByAgent.has(agent_id)) firstConversationByAgent.set(agent_id, id);
  });
  const groups: (FloatingGroup & { scope?: PaletteScope; icon: string })[] = [
    {
      scope: "claims",
      icon: "proof",
      label: t("palette.references"),
      options: codes.flatMap((reference, index) => {
        const data = references[index]?.data;
        const title = data && claimTitles.get(data.claim_id);
        if (!data || !title) return [];
        return [
          result(
            REFERENCE_ID + reference,
            reference,
            () => open(data.claim_id, data.node_id ?? undefined),
            title,
            reference === exact ? text : undefined,
          ),
        ];
      }),
    },
    {
      scope: "claims",
      icon: "claims",
      label: t("palette.claims"),
      options: currentClaims.map(({ id, domain }) =>
        result("claim:" + id, claimTitles.get(id)!, () => open(id), domain ?? undefined),
      ),
    },
    {
      icon: "library",
      label: t("palette.library"),
      options: (library.data?.items ?? []).map((item) =>
        result(
          "library:" + item.id,
          itemName(t, item),
          () => go("library", "~" + item.id),
          item.logical_key,
          [item.natural, item.lean, item.formula].filter(Boolean).join(" "),
        ),
      ),
    },
    {
      scope: "claims",
      icon: "proof",
      label: t("palette.currentProofNodes"),
      options: claimId ? proofNodes(claimId, currentNodes ?? []) : [],
    },
    {
      scope: "claims",
      icon: "proof",
      label: t("palette.otherProofNodes"),
      options: indexedClaims.flatMap(({ id, nodes }) => (id === claimId ? [] : proofNodes(id, nodes))),
    },
    {
      icon: "chat",
      label: t("palette.conversations"),
      options: currentConversations.map(({ id, title, agent_id }) =>
        result("conversation:" + id, title, () => go("chat", id), agentNames.get(agent_id ?? "")),
      ),
    },
    {
      scope: "agents",
      icon: "team",
      label: t("palette.agents"),
      options: (agents.data?.agents ?? []).map(({ id, name, description }) =>
        result(
          "agent:" + id,
          name,
          () => go("chat", firstConversationByAgent.get(id) ?? "employee/" + id),
          undefined,
          description,
        ),
      ),
    },
  ];
  const scopedGroups = groups
    .filter((group) => !scope || group.scope === scope)
    .map(({ label, icon, options }) => {
      const glyph = createElement(WorkbenchIcon, { name: icon });
      return { label, options: options.map((option) => ({ ...option, icon: glyph })) };
    });
  const byId = new Map(scopedGroups.flatMap(({ options }) => options.map((option) => [option.id, option])));
  // Once searching, proof nodes stay grouped in claim order even when previously visited.
  const visibleRecents = text ? ids.filter((id) => !id.startsWith(NODE_ID)) : ids;
  const recent = new Set(visibleRecents);
  return {
    groups: [
      { label: t("palette.recent"), options: visibleRecents.flatMap((id) => byId.get(id) ?? []) },
      ...scopedGroups.map((group) => ({ ...group, options: group.options.filter(({ id }) => !recent.has(id)) })),
    ],
    scoped: Boolean(scope),
    isLoading:
      claims.isPending ||
      library.isPending ||
      conversations.isPending ||
      agents.isPending ||
      (searchNodes && (atlas.isPending || (Boolean(claimId) && snapshot.isPending))) ||
      Boolean(exact && (exact !== settledReference || references[codes.indexOf(exact)]?.isPending)),
  };
}
