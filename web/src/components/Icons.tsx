import type { Tone } from "../workbench/text";

const ICONS: Record<string, string> = {
  search: "M20 20l-4.2-4.2M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0Z",
  workbench: "M3.5 10.5 12 3.5l8.5 7M5.5 9v10.5a1 1 0 0 0 1 1h3.25v-6h4.5v6h3.25a1 1 0 0 0 1-1V9",
  claims:
    "M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 5 4.5ZM9.5 4.5v15M15 4.5v15",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.5M4 12h.5M4 18h.5",
  table: "M4 5h16v14H4zM4 10h16M4 14.5h16m-10-9.5v14",
  "agent-work": "M4 7h16v13H4zM8 7V4h8v3M4 12h16m-10 0v2h4v-2",
  review: "M4 6h16l2 10v4H2v-4L4 6ZM2 16h5l2 2h6l2-2h5M9 11l2 2 4-4",
  library: "M5 4.5h4.5v15H5zM9.5 4.5H14v15H9.5zM14.5 5.2l3.8-1 3.2 14.3-3.8 1z",
  chat: "M5 5h14a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 17h-8l-4 3v-3H5a1.5 1.5 0 0 1-1.5-1.5v-9A1.5 1.5 0 0 1 5 5Z",
  team: "M9 11a3.5 3.5 0 1 0 0-7a3.5 3.5 0 0 0 0 7ZM2.5 20c.6-3.3 3.2-5.5 6.5-5.5s5.9 2.2 6.5 5.5M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14.8c1.9.7 3.2 2.5 3.5 5.2",
  progress: "M20.5 12A8.5 8.5 0 1 1 12 3.5M13 7.5l-3 5h4l-3 5M16.5 3.5H20.5V7.5",
  settings:
    "M10 3h4l.7 3 2 .9 2.7-1.2 2 3.5-2.2 2v1.6l2.2 2-2 3.5-2.7-1.2-2 .9-.7 3h-4l-.7-3-2-.9-2.7 1.2-2-3.5 2.2-2v-1.6l-2.2-2 2-3.5 2.7 1.2 2-.9.7-3ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  help: "M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17ZM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17v.5",
  proof: "M5 4v16h14V4zM8 9h8m-8 3h6m-6 3h4",
  add: "M12 5v14M5 12h14",
  organize: "M5 5h14M5 12h9m-9 7h6m4-2 2 2 3-4",
  reference: "M4.5 5h6v6h-6zM13.5 5h6v6h-6zM4.5 14h6v6h-6zM16.5 14v6m-3-3h6",
  task: "M8 5H5v15h14V5h-3M8 3h8v4H8zM8 13l2 2 5-5",
  details: "M4 5h16v14H4zM13 5v14M15.5 9h2m-2 4h2",
  note: "M10 5H4v15h15v-6m-8 0 .5-3 6.5-6.5 2.5 2.5L14 13.5z",
  copy: "M9 9h11v11H9zM15 6V4H4v11h2",
  code: "m8 6-5 6 5 6m8-12 5 6-5 6m-2-14-4 16",
  link: "M10 7l2-2a4 4 0 0 1 6 6l-2 2m-2 4-2 2a4 4 0 0 1-6-6l2-2m1 4 6-6",
  move: "M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3",
  cancel: "M6 6l12 12M18 6 6 18",
  // #326: Work is a list of proof obligations; the first two bullets are filled (see FILLED).
  work: "M4.5 16h4l-1.2 2.5h-4zM11.5 6.75h9M11.5 12h9M11.5 17.25h6",
  filter: "M4 6h16M7 12h10M10 18h4",
  inbox: "M4 13.5 6.5 5h11l2.5 8.5V19H4zM4 13.5h4.5l1 2h5l1-2H20",
  running: "M12 3.5a8.5 8.5 0 1 1-8.5 8.5M10.2 9v6l4.8-3z",
  blocked: "M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17ZM6 6l12 12",
  display: "M4 7h10M18 7h2M4 17h2M10 17h10M16 5v4M8 15v4",
  // #342: a claim whose prerequisites are done can move ahead.
  advance: "M4 12h13M12 6.5l5.5 5.5-5.5 5.5M20 5v14",
};

/** Parts of an icon drawn solid rather than as a stroke. */
const FILLED: Record<string, string> = {
  work: "M4.5 5.5h4l-1.2 2.5h-4zM4.5 10.75h4l-1.2 2.5h-4z",
};

export function WorkbenchIcon({ name }: { name: string }) {
  return (
    <svg className="workbench-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[name] ?? ""} />
      {FILLED[name] && <path d={FILLED[name]} fill="currentColor" />}
    </svg>
  );
}

export function Mark({ tone, shape }: { tone?: Tone; shape?: "square" }) {
  return <span className="mark" aria-hidden="true" data-tone={tone || undefined} data-shape={shape} />;
}

export function Spin({ className = "spin" }: { className?: string }) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" className={className}>
      <circle cx="6" cy="6" r="4.5" className="track" />
      <path d="M6 1.5a4.5 4.5 0 0 1 4.5 4.5" className="arc" />
    </svg>
  );
}

export function Clock() {
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" className="clock">
      <circle cx="6" cy="6" r="4.5" />
      <path d="M6 3.5V6l1.7 1.2" />
    </svg>
  );
}

export function Chevron() {
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" className="chevron">
      <path d="m4.2 2.5 3 3.5-3 3.5" />
    </svg>
  );
}

export function SendArrow() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 19V5" />
      <path d="m5 12 7-7 7 7" />
    </svg>
  );
}
