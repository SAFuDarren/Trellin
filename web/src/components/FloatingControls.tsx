import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type Ref,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import "../styles/floating-controls.css";

type Placement = "bottom-start" | "bottom-end" | "top-start" | "top-end";
type Dismiss = (restoreFocus?: boolean) => void;
type Point = { x: number; y: number };
type Edge = "first" | "last";

export type ContextMenuAnchor = { element: HTMLButtonElement; point?: Point };

type FloatingTrigger = {
  label: string;
  trigger?: ReactNode;
  triggerClassName?: string;
  className?: string;
  placement?: Placement;
  keepVisible?: RefObject<HTMLElement | null>;
  triggerId?: string;
  triggerRef?: Ref<HTMLButtonElement>;
  disabled?: boolean;
  dataCurrent?: boolean;
  onOpenChange?: (open: boolean) => void;
};

export type FloatingOption = {
  id: string;
  label: string;
  detail?: string;
  icon?: ReactNode;
  searchText?: string;
  disabled?: boolean;
  destructive?: boolean;
  shortcut?: string;
  count?: number;
  current?: boolean;
  onSelect?: () => void;
  href?: string;
};

export type FloatingGroup = {
  label?: string;
  options: FloatingOption[];
};

function positionPanel(
  trigger: HTMLElement,
  panel: HTMLElement,
  placement: Placement,
  keepVisible: HTMLElement | null | undefined,
  point?: Point,
) {
  const margin = 8;
  const gap = 5;
  const viewport = window.visualViewport;
  const top = viewport?.offsetTop ?? 0;
  const left = viewport?.offsetLeft ?? 0;
  const right = left + (viewport?.width ?? document.documentElement.clientWidth);
  const bottom = top + (viewport?.height ?? document.documentElement.clientHeight);
  const anchor = point
    ? { left: point.x, right: point.x, top: point.y, bottom: point.y }
    : trigger.getBoundingClientRect();
  const protectedArea = keepVisible?.getBoundingClientRect();
  const topEdge = Math.min(anchor.top, protectedArea?.top ?? anchor.top);
  const bottomEdge = Math.max(anchor.bottom, protectedArea?.bottom ?? anchor.bottom);
  const above = Math.max(0, topEdge - top - margin - gap);
  const below = Math.max(0, bottom - bottomEdge - margin - gap);
  const prefersTop = placement.startsWith("top");
  const preferred = prefersTop ? above : below;
  const alternative = prefersTop ? below : above;
  const useTop = (panel.scrollHeight <= preferred || preferred >= alternative) === prefersTop;
  panel.style.maxWidth = `${Math.max(0, right - left - 2 * margin)}px`;
  panel.style.setProperty("--floating-space", `${useTop ? above : below}px`);

  // Layout sizes exclude the entrance animation's translation.
  const x = placement.endsWith("end") ? anchor.right - panel.offsetWidth : anchor.left;
  const y = useTop ? topEdge - panel.offsetHeight - gap : bottomEdge + gap;
  panel.style.left = `${Math.max(left + margin, Math.min(x, right - panel.offsetWidth - margin))}px`;
  panel.style.top = `${Math.max(top + margin, Math.min(y, bottom - panel.offsetHeight - margin))}px`;
}

function FloatingLayer({
  element,
  point,
  className,
  placement = "bottom-start",
  keepVisible,
  dialog,
  dismissOnAnchor = false,
  onDismiss,
  children,
}: {
  element: HTMLButtonElement;
  point?: Point;
  className?: string;
  placement?: Placement;
  keepVisible?: RefObject<HTMLElement | null>;
  dialog?: { id: string; label: string };
  dismissOnAnchor?: boolean;
  onDismiss: () => void;
  children: (dismiss: Dismiss) => ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  // A native dialog owns its top layer, including controls portaled into it.
  const host = element.closest<HTMLDialogElement>("dialog[open]") ?? document.body;
  const dismiss = useCallback<Dismiss>(
    (restoreFocus = true) => {
      if (restoreFocus) element.focus({ preventScroll: true });
      onDismiss();
    },
    [element, onDismiss],
  );

  useLayoutEffect(() => {
    const floating = panel.current;
    if (!floating) return;
    const place = () => positionPanel(element, floating, placement, keepVisible?.current, point);
    place();
    const resize = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(place);
    resize?.observe(element);
    resize?.observe(floating);
    if (keepVisible?.current) resize?.observe(keepVisible.current);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    window.visualViewport?.addEventListener("resize", place);
    window.visualViewport?.addEventListener("scroll", place);
    host.addEventListener("animationend", place);
    (floating.querySelector<HTMLElement>("[data-floating-focus]") ?? floating).focus({ preventScroll: true });
    return () => {
      resize?.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      window.visualViewport?.removeEventListener("resize", place);
      window.visualViewport?.removeEventListener("scroll", place);
      host.removeEventListener("animationend", place);
    };
  }, [element, host, keepVisible, placement, point]);

  useEffect(() => {
    const floating = panel.current;
    if (!floating) return;

    const inside = (target: EventTarget | null) =>
      target instanceof Node && (element.contains(target) || floating.contains(target));
    const pointerDown = (event: PointerEvent) => {
      const { target } = event;
      if (target instanceof Node && (floating.contains(target) || (!dismissOnAnchor && element.contains(target)))) {
        return;
      }
      const hadFocus = inside(document.activeElement);
      dismiss(false);
      if (hadFocus) {
        window.requestAnimationFrame(() => {
          if (document.activeElement === document.body && element.isConnected) element.focus({ preventScroll: true });
        });
      }
    };
    const keyDown = (event: globalThis.KeyboardEvent) => {
      if (!inside(event.target) || event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dismiss();
      }
      if (event.key === "Tab" && floating.contains(document.activeElement)) {
        const stops = Array.from(
          floating.querySelectorAll<HTMLElement>(
            'input:not(:disabled), button:not(:disabled), a[href], [tabindex="0"]',
          ),
        ).filter((element) => element.tabIndex >= 0);
        const index = stops.indexOf(document.activeElement as HTMLElement);
        // Leave the portal from the trigger's position in the page's tab order.
        if (index < 0 || (event.shiftKey ? index === 0 : index === stops.length - 1)) dismiss();
      }
    };
    const focusIn = (event: FocusEvent) => {
      if (!inside(event.target)) dismiss(false);
    };
    const navigation = () => dismiss(false);
    document.addEventListener("pointerdown", pointerDown, true);
    document.addEventListener("keydown", keyDown, true);
    document.addEventListener("focusin", focusIn);
    window.addEventListener("hashchange", navigation);
    return () => {
      document.removeEventListener("pointerdown", pointerDown, true);
      document.removeEventListener("keydown", keyDown, true);
      document.removeEventListener("focusin", focusIn);
      window.removeEventListener("hashchange", navigation);
    };
  }, [dismiss, dismissOnAnchor, element]);

  return createPortal(
    <div
      ref={panel}
      className={className ? `floating-popover ${className}` : "floating-popover"}
      id={dialog?.id}
      role={dialog ? "dialog" : undefined}
      aria-label={dialog?.label}
      tabIndex={-1}
    >
      {children(dismiss)}
    </div>,
    host,
  );
}

export function Popover({
  label,
  trigger,
  triggerClassName,
  className,
  placement,
  keepVisible,
  triggerId,
  triggerRef,
  disabled,
  dataCurrent,
  onOpenChange,
  panelId,
  popup,
  selectedLabel,
  children,
}: FloatingTrigger & {
  panelId: string;
  popup: "menu" | "dialog";
  selectedLabel?: string;
  children: (dismiss: Dismiss, edge: Edge) => ReactNode;
}) {
  const [element, setElement] = useState<HTMLButtonElement | null>(null);
  const [edge, setEdge] = useState<Edge>("first");
  const open = element !== null;
  const close = useCallback(() => {
    setElement(null);
    onOpenChange?.(false);
  }, [onOpenChange]);

  function show(button: HTMLButtonElement, initialEdge: Edge = "first") {
    setEdge(initialEdge);
    setElement(button);
    onOpenChange?.(true);
  }

  return (
    <>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        className={triggerClassName ? `floating-trigger ${triggerClassName}` : "floating-trigger"}
        aria-label={label}
        aria-description={selectedLabel}
        title={selectedLabel ?? label}
        aria-haspopup={popup}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        data-current={dataCurrent || undefined}
        disabled={disabled}
        onClick={(event) => {
          if (!open) show(event.currentTarget);
          else {
            event.currentTarget.focus({ preventScroll: true });
            close();
          }
        }}
        onKeyDown={(event) => {
          if (open || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
          event.preventDefault();
          show(event.currentTarget, event.key === "ArrowUp" ? "last" : "first");
        }}
      >
        {trigger ?? label}
      </button>
      {element && (
        <FloatingLayer
          element={element}
          className={className}
          placement={placement}
          keepVisible={keepVisible}
          dialog={popup === "dialog" ? { id: panelId, label } : undefined}
          onDismiss={close}
        >
          {(dismiss) => children(dismiss, edge)}
        </FloatingLayer>
      )}
    </>
  );
}

export function contextMenuProps(onOpen: (anchor: ContextMenuAnchor) => void) {
  return {
    "aria-haspopup": "menu" as const,
    "aria-keyshortcuts": "ContextMenu Shift+F10",
    onContextMenu(event: MouseEvent<HTMLButtonElement>) {
      if (event.defaultPrevented) return;
      event.preventDefault();
      event.stopPropagation();
      const pointer = event.button !== 0 || event.ctrlKey || event.clientX !== 0 || event.clientY !== 0;
      onOpen({
        element: event.currentTarget,
        point: pointer ? { x: event.clientX, y: event.clientY } : undefined,
      });
    },
    onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
      if (
        event.defaultPrevented ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.nativeEvent.isComposing ||
        event.keyCode === 229 ||
        !(event.key === "ContextMenu" || (event.shiftKey && event.key === "F10"))
      ) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      onOpen({ element: event.currentTarget });
    },
  };
}

function fold(text: string) {
  return text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();
}

function shortcutOption(event: KeyboardEvent, options: FloatingOption[]) {
  if (event.defaultPrevented || event.repeat || !event.altKey || event.ctrlKey || event.metaKey || event.shiftKey)
    return;
  // Option-letter combinations may produce a symbol rather than a letter on macOS.
  const letter = /^Key[A-Z]$/.test(event.code) ? event.code.slice(3) : event.key.toUpperCase();
  return options.find((option) => option.onSelect && option.shortcut === `Alt+${letter}`);
}

function OptionContent({ option, checked }: { option: FloatingOption; checked?: boolean }) {
  return (
    <>
      {option.icon && (
        <span className="floating-option-icon" aria-hidden="true">
          {option.icon}
        </span>
      )}
      <span className="floating-option-text">
        <span>{option.label}</span>
        {option.detail && <small>{option.detail}</small>}
      </span>
      {option.shortcut && (
        <kbd className="floating-shortcut" aria-hidden="true">
          {option.shortcut}
        </kbd>
      )}
      {option.count !== undefined && <span className="floating-count">{option.count}</span>}
      {checked && <span className="floating-check" aria-hidden="true" />}
    </>
  );
}

function MenuContent({
  id,
  label,
  groups,
  edge,
  onSelect,
  dismiss,
}: {
  id: string;
  label: string;
  groups: FloatingGroup[];
  edge: Edge;
  onSelect?: (id: string) => void;
  dismiss: Dismiss;
}) {
  const typeahead = useRef({ text: "", at: 0 });
  const nonemptyGroups = groups.filter(({ options }) => options.length > 0);
  const options = nonemptyGroups.flatMap((group) => group.options);
  const available = options.filter((option) => !option.disabled);
  const initialOptions = available.length ? available : options;
  const initial = edge === "first" ? initialOptions[0]?.id : initialOptions.at(-1)?.id;
  function handleKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    const shortcut = shortcutOption(event, available);
    if (shortcut) {
      event.preventDefault();
      choose(shortcut);
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("[data-menu-option]"));
    if (elements.length === 0) return;
    const current = elements.findIndex((element) => element === document.activeElement);
    let next: HTMLElement | undefined;
    switch (event.key) {
      case "ArrowDown":
        next = elements[(current + 1) % elements.length];
        break;
      case "ArrowUp":
        next = elements[(Math.max(current, 0) + elements.length - 1) % elements.length];
        break;
      case "Home":
        next = elements[0];
        break;
      case "End":
        next = elements.at(-1);
        break;
      default: {
        if (event.key.length !== 1 || event.key === " ") return;
        const key = fold(event.key);
        const now = performance.now();
        const previous = now - typeahead.current.at < 500 ? typeahead.current.text : "";
        const query = previous === key ? key : previous + key;
        typeahead.current = { text: query, at: now };
        next = [...elements.slice(current + 1), ...elements.slice(0, current + 1)].find((element) =>
          fold(element.dataset.menuOption ?? "").startsWith(query),
        );
      }
    }
    if (next) {
      event.preventDefault();
      next.focus();
    }
  }

  function choose(option: FloatingOption) {
    if (option.disabled) return;
    dismiss();
    option.onSelect?.();
    onSelect?.(option.id);
  }

  return (
    <div
      id={id}
      role="menu"
      aria-label={label}
      data-floating-focus={initial === undefined ? "" : undefined}
      tabIndex={-1}
      onKeyDown={handleKey}
    >
      {nonemptyGroups.map(({ label: groupLabel, options }, groupIndex) => (
        <div key={groupIndex} role="presentation">
          {groupIndex > 0 && <div className="floating-separator" role="separator" />}
          <div role="group" aria-label={groupLabel}>
            {groupLabel && (
              <div className="floating-group-label" aria-hidden="true">
                {groupLabel}
              </div>
            )}
            {options.map((option) => {
              const attributes = {
                className: "floating-option",
                tabIndex: -1,
                "data-menu-option": option.label,
                "data-floating-focus": initial === option.id ? "" : undefined,
                "data-destructive": option.destructive || undefined,
                "aria-label": option.count === undefined ? option.label : `${option.label} ${option.count}`,
                "aria-description": option.detail,
                "aria-disabled": option.disabled || undefined,
                "aria-keyshortcuts": option.shortcut,
              };
              const content = <OptionContent option={option} checked={option.current} />;
              if (option.href && !option.disabled) {
                return (
                  <a
                    key={option.id}
                    {...attributes}
                    href={option.href}
                    role="menuitem"
                    aria-current={option.current ? "page" : undefined}
                    onClick={(event) => {
                      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
                      choose(option);
                    }}
                  >
                    {content}
                  </a>
                );
              }
              return (
                <button
                  key={option.id}
                  {...attributes}
                  type="button"
                  role={option.current === undefined ? "menuitem" : "menuitemradio"}
                  aria-checked={option.current}
                  onClick={() => choose(option)}
                >
                  {content}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Menu({
  groups,
  onSelect,
  intro,
  ...trigger
}: FloatingTrigger & { groups: FloatingGroup[]; onSelect?: (id: string) => void; intro?: ReactNode }) {
  const id = useId();
  return (
    <Popover {...trigger} panelId={id} popup="menu">
      {(dismiss, edge) => (
        <>
          {intro}
          <MenuContent
            id={id}
            label={trigger.label}
            groups={groups}
            edge={edge}
            onSelect={onSelect}
            dismiss={dismiss}
          />
        </>
      )}
    </Popover>
  );
}

export function ContextMenu({
  anchor,
  label,
  groups,
  onDismiss,
}: {
  anchor: ContextMenuAnchor;
  label: string;
  groups: FloatingGroup[];
  onDismiss: () => void;
}) {
  const id = useId();
  return (
    <FloatingLayer element={anchor.element} point={anchor.point} dismissOnAnchor onDismiss={onDismiss}>
      {(dismiss) => <MenuContent id={id} label={label} groups={groups} edge="first" dismiss={dismiss} />}
    </FloatingLayer>
  );
}

export function PickerContent({
  groups,
  searchLabel,
  searchAction,
  emptyLabel,
  value,
  footer,
  limit,
  filterText,
  edge = "first",
  onQueryChange,
  onChoose,
}: {
  groups: FloatingGroup[];
  searchLabel: string;
  searchAction?: ReactNode;
  emptyLabel: string;
  value?: string;
  footer?: ReactNode;
  limit?: { perGroup: number; label: string };
  filterText?: (query: string) => string;
  edge?: Edge;
  onQueryChange?: (query: string) => void;
  onChoose: (option: FloatingOption) => void;
}) {
  const id = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(value);
  const search = filterText?.(query) ?? query;
  const matches = useMemo(() => {
    const terms = fold(search.trim()).split(/\s+/);
    return groups
      .map((group) => ({
        ...group,
        options: group.options.filter((option) => {
          const haystack = fold([option.label, option.detail, option.searchText].join(" "));
          return terms.every((term) => haystack.includes(term));
        }),
      }))
      .filter((group) => group.options.length > 0);
  }, [groups, search]);
  const filtered = limit
    ? matches.map((group) => ({ ...group, options: group.options.slice(0, limit.perGroup) }))
    : matches;
  const hasMore = limit && matches.some((group) => group.options.length > limit.perGroup);
  const options = filtered.flatMap((group) => group.options);
  const enabled = options.filter((option) => !option.disabled);
  const active = enabled.find((option) => option.id === highlight) ?? (edge === "first" ? enabled[0] : enabled.at(-1));
  const optionId = (option: FloatingOption) => `${id}-${encodeURIComponent(option.id)}`;
  const activeId = active ? optionId(active) : undefined;

  useEffect(() => {
    const list = listRef.current;
    const row = activeId ? document.getElementById(activeId) : null;
    if (row && list?.contains(row)) row.scrollIntoView?.({ block: "nearest" });
  }, [activeId]);

  function handleKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    const shortcut = shortcutOption(event, enabled);
    if (shortcut) {
      event.preventDefault();
      onChoose(shortcut);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (active) onChoose(active);
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const index = active ? enabled.indexOf(active) : 0;
      const next = enabled[(index + direction + enabled.length) % enabled.length];
      if (next) setHighlight(next.id);
    }
  }

  return (
    <>
      <div className="floating-search">
        <input
          type="search"
          role="combobox"
          aria-label={searchLabel}
          aria-expanded="true"
          aria-controls={id}
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          autoComplete="off"
          data-floating-focus=""
          placeholder={searchLabel}
          value={query}
          onChange={(event) => {
            const nextQuery = event.target.value;
            setQuery(nextQuery);
            setHighlight(undefined);
            onQueryChange?.(nextQuery);
          }}
          onKeyDown={handleKey}
        />
        {searchAction}
      </div>
      <div id={id} ref={listRef} className="floating-results" role="listbox" aria-label={searchLabel}>
        {filtered.map(({ label: groupLabel, options }, groupIndex) => (
          <div key={groupIndex} role="group" aria-label={groupLabel}>
            {groupLabel && (
              <div className="floating-group-label" aria-hidden="true">
                {groupLabel}
              </div>
            )}
            {options.map((option) => (
              <button
                key={option.id}
                id={optionId(option)}
                type="button"
                role="option"
                aria-label={option.label}
                aria-description={option.detail}
                aria-selected={option.id === value}
                aria-disabled={option.disabled || undefined}
                aria-keyshortcuts={option.shortcut}
                tabIndex={-1}
                className="floating-option"
                data-highlighted={option.id === active?.id || undefined}
                disabled={option.disabled}
                onPointerMove={(event) => {
                  if (event.pointerType === "mouse") setHighlight(option.id);
                }}
                onClick={() => onChoose(option)}
              >
                <OptionContent option={option} checked={option.id === value} />
              </button>
            ))}
          </div>
        ))}
      </div>
      {options.length === 0 && (
        <p className="floating-empty" role="status">
          {emptyLabel}
        </p>
      )}
      {hasMore && (
        <p className="floating-limit" role="status">
          {limit.label}
        </p>
      )}
      {footer && <div className="floating-footer">{footer}</div>}
    </>
  );
}

export function Picker({
  groups,
  searchLabel,
  emptyLabel,
  value,
  footer,
  onSelect,
  focusOnSelect = true,
  ...trigger
}: FloatingTrigger & {
  groups: FloatingGroup[];
  searchLabel: string;
  emptyLabel: string;
  value?: string;
  footer?: ReactNode | ((dismiss: Dismiss) => ReactNode);
  onSelect?: (id: string) => void;
  focusOnSelect?: boolean;
}) {
  const id = useId();
  const selectedLabel = groups.flatMap((group) => group.options).find((option) => option.id === value)?.label;
  return (
    <Popover {...trigger} panelId={id} popup="dialog" selectedLabel={selectedLabel}>
      {(dismiss, edge) => (
        <PickerContent
          groups={groups}
          searchLabel={searchLabel}
          emptyLabel={emptyLabel}
          value={value}
          footer={typeof footer === "function" ? footer(dismiss) : footer}
          edge={edge}
          onChoose={(option) => {
            dismiss(focusOnSelect);
            option.onSelect?.();
            onSelect?.(option.id);
          }}
        />
      )}
    </Popover>
  );
}
