import type { Tone } from "../workbench/text";

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
