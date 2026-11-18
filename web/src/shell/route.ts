// Section routing on the URL hash (#review, #team/runtimes, …). The session token never stays in the hash.
import { useEffect, useState } from "react";

export const SECTIONS = [
  "workbench",
  "review",
  "claims",
  "library",
  "chat",
  "team",
  "progress",
  "settings",
  "help",
] as const;
export type Section = (typeof SECTIONS)[number];
export type Route = { section: Section; sub: string };

export function parseRoute(hash: string): Route {
  const [head = "", ...rest] = hash.replace(/^#/, "").split("/");
  const section = (SECTIONS as readonly string[]).includes(head) ? (head as Section) : "workbench";
  return { section, sub: decodeURIComponent(rest.join("/")) };
}

export function href(section: Section, sub = ""): string {
  return "#" + section + (sub ? "/" + encodeURIComponent(sub) : "");
}

export function useRoute(): [Route, (section: Section, sub?: string) => void] {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const go = (section: Section, sub = "") => {
    window.location.hash = href(section, sub);
  };
  return [route, go];
}
