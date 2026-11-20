import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import "../styles/navigation-menu.css";

type NavigationItem = { href: string; label: string; current?: boolean; badge?: number };

/** A compact disclosure containing ordinary page links. */
export function NavigationMenu({
  label,
  className,
  indicator,
  children,
  items,
}: {
  label: string;
  className?: string;
  indicator?: boolean;
  children: ReactNode;
  items: NavigationItem[];
}) {
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);

  useLayoutEffect(() => {
    const trigger = triggerRef.current;
    const menu = menuRef.current;
    if (!open || !trigger || !menu) return;

    const place = () => {
      const anchor = trigger.getBoundingClientRect();
      const margin = 8;
      const gap = 5;
      const below = Math.max(0, window.innerHeight - anchor.bottom - gap - margin);
      const above = Math.max(0, anchor.top - gap - margin);
      const placeBelow = menu.scrollHeight <= below || below >= above;
      menu.style.maxHeight = `${placeBelow ? below : above}px`;
      const panel = menu.getBoundingClientRect();
      const left = Math.max(margin, Math.min(anchor.left, window.innerWidth - panel.width - margin));
      menu.style.left = `${left}px`;
      menu.style.top = `${placeBelow ? anchor.bottom + gap : anchor.top - panel.height - gap}px`;
    };

    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, items]);

  useEffect(() => {
    const trigger = triggerRef.current;
    const menu = menuRef.current;
    if (!open || !trigger || !menu) return;

    const inside = (target: EventTarget | null) =>
      target instanceof Node && (trigger.contains(target) || menu.contains(target));
    const dismiss = () => setOpen(false);
    const onPointerDown = (event: PointerEvent) => {
      if (inside(event.target)) return;
      const hadFocus = inside(document.activeElement);
      dismiss();
      if (hadFocus) {
        window.requestAnimationFrame(() => {
          if (document.activeElement === document.body && trigger.isConnected) trigger.focus({ preventScroll: true });
        });
      }
    };
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      trigger.focus({ preventScroll: true });
      dismiss();
    };
    const onFocusIn = (event: FocusEvent) => {
      if (!inside(event.target)) dismiss();
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onEscape);
    document.addEventListener("focusin", onFocusIn);
    window.addEventListener("hashchange", dismiss);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onEscape);
      document.removeEventListener("focusin", onFocusIn);
      window.removeEventListener("hashchange", dismiss);
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={className ? `navigation-trigger ${className}` : "navigation-trigger"}
        aria-label={label}
        title={label}
        aria-expanded={open}
        aria-controls={id}
        data-current={indicator || undefined}
        onClick={() => setOpen((current) => !current)}
      >
        {children}
      </button>
      <ul id={id} ref={menuRef} className="navigation-menu" aria-label={label} hidden={!open}>
        {items.map((item) => (
          <li key={item.href}>
            <a
              href={item.href}
              aria-current={item.current ? "page" : undefined}
              onClick={(event) => {
                if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
                triggerRef.current?.focus({ preventScroll: true });
                setOpen(false);
              }}
            >
              <span>{item.label}</span>
              {item.badge !== undefined && <span className="navigation-count">{item.badge}</span>}
            </a>
          </li>
        ))}
      </ul>
    </>
  );
}
