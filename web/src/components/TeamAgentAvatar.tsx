import type { TeamAgent } from "../api/teamAgents";
import "../styles/team-agent-avatar.css";

const AVATAR_SYMBOLS = { book: "▤", compass: "⌖", spark: "✦" };

export function TeamAgentAvatar({ avatar, name }: Pick<TeamAgent, "avatar" | "name">) {
  const initials = [...name.trim()].slice(0, 2).join("").toLocaleUpperCase();
  return (
    <span className="team-agent-avatar" aria-hidden="true">
      {avatar === "initials" ? initials : AVATAR_SYMBOLS[avatar]}
    </span>
  );
}
