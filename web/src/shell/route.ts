// Section routing on the URL hash (#review, #team/runtimes, …). The session token never stays in the hash.
import { useEffect, useState } from "react";

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

export function useRoute(): [Route, (section: Section, sub?: string) => void] {
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
    window.location.hash = href(section, sub);
  };
  return [route, go];
}
