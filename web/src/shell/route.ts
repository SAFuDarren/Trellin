// Section routing on the URL hash (#review, #team/runtimes, …). The session token never stays in the hash.
import { useCallback, useEffect, useState } from "react";

export const SECTIONS = [
  "workbench",
  "review",
  "claims",
  "agent-work",
  "library",
  "chat",
  "team",
  "progress",
  "settings",
  "help",
] as const;
export type Section = (typeof SECTIONS)[number];
export type Route = { section: Section; sub: string };

export const EMPLOYEE_CHAT_PATH = "employee/";

export function parseRoute(hash: string): Route {
  const [head = "", ...rest] = hash.replace(/^#/, "").split("/");
  const section = (SECTIONS as readonly string[]).includes(head) ? (head as Section) : "workbench";
  const sub = decodeURIComponent(rest.join("/"));
  if (section === "claims" && (sub === "agent-work" || sub.startsWith("agent-work/"))) {
    return { section: "agent-work", sub: sub.slice("agent-work/".length) };
  }
  return { section, sub };
}

export function href(section: Section, sub = ""): string {
  return "#" + section + (sub ? "/" + encodeURIComponent(sub) : "");
}

/** #274: the workbench address names the open proof, as claim/<id> or claim/<id>/node/<node-id>. */
export type WorkbenchAddress = { claimId: string; nodeId?: string };

export function parseWorkbenchAddress(sub: string): WorkbenchAddress | null {
  const [kind, claimId, nodeKind, ...node] = sub.split("/");
  if (kind !== "claim" || !claimId) return null;
  const nodeId = nodeKind === "node" && node.length ? node.join("/") : undefined;
  return nodeId ? { claimId, nodeId } : { claimId };
}

export function workbenchSub(address: WorkbenchAddress | null): string {
  if (!address) return "";
  return "claim/" + address.claimId + (address.nodeId ? "/node/" + address.nodeId : "");
}

/** A readable workbench link: each part is encoded on its own so the slashes stay visible. */
export function workbenchHref(address: WorkbenchAddress | null): string {
  const sub = workbenchSub(address);
  return "#workbench" + (sub ? "/" + sub.split("/").map(encodeURIComponent).join("/") : "");
}

export type AddressMode = "push" | "replace";
type SetWorkbenchAddress = (address: WorkbenchAddress | null, mode: AddressMode) => void;

export function useRoute(): [Route, (section: Section, sub?: string) => void, SetWorkbenchAddress] {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      const hash = window.location.hash;
      const next = parseRoute(hash);
      if (hash.startsWith("#claims/") && next.section === "agent-work") {
        window.history.replaceState(window.history.state, "", href(next.section, next.sub));
      }
      setRoute(next);
    };
    onChange();
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const go = (section: Section, sub = "") => {
    window.location.hash = section === "workbench" ? workbenchHref(parseWorkbenchAddress(sub)) : href(section, sub);
  };
  // History entries written here do not fire hashchange, so the route follows them directly.
  const setWorkbenchAddress = useCallback<SetWorkbenchAddress>((address, mode) => {
    const next = workbenchHref(address);
    if (window.location.hash === next || (!window.location.hash && !address)) return;
    if (mode === "push") window.history.pushState(window.history.state, "", next);
    else window.history.replaceState(window.history.state, "", next);
    setRoute(parseRoute(next));
  }, []);
  return [route, go, setWorkbenchAddress];
}
