import type { Page } from "@playwright/test";

// #283: rules that hold on every screen. Each violation names the element, its box
// and what to change, so the failure message is enough to act on.

export type Violation = { rule: string; target: string; detail: string };

export const MIN_TARGET = 24;

export async function layoutViolations(page: Page): Promise<Violation[]> {
  return page.evaluate((minTarget) => {
    const found: { rule: string; target: string; detail: string }[] = [];
    const describe = (element: Element) => {
      const html = element as HTMLElement;
      const name = (html.getAttribute("aria-label") ?? html.innerText ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
      const classes = [...element.classList].slice(0, 3).join(".");
      return `${element.tagName.toLowerCase()}${classes ? "." + classes : ""}${name ? ` "${name}"` : ""}`;
    };
    const box = (rect: DOMRect) =>
      `${Math.round(rect.x)},${Math.round(rect.y)} ${Math.round(rect.width)}×${Math.round(rect.height)}`;
    const visible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 1 || rect.height <= 1) return false; // includes visually hidden (.sr-only) text
      if (element.closest(".sr-only")) return false;
      const style = getComputedStyle(element);
      return style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity) !== 0;
    };

    // 1. No horizontal page scroll.
    const root = document.scrollingElement ?? document.documentElement;
    if (root.scrollWidth > window.innerWidth + 1) {
      found.push({
        rule: "page-scroll",
        target: "document",
        detail: `page is ${root.scrollWidth}px wide in a ${window.innerWidth}px window; find the element wider than its container and let it shrink (min-width: 0) or wrap`,
      });
    }

    // 2. Text is either fully shown or ends in an ellipsis with its full text available.
    for (const element of document.querySelectorAll<HTMLElement>("body *")) {
      if (!visible(element) || element.closest("[data-layout-ignore]")) continue;
      const own = [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim());
      if (!own) continue;
      const style = getComputedStyle(element);
      const clipsX = style.overflowX !== "visible" && element.scrollWidth > element.clientWidth + 1;
      const clipsY =
        style.overflowY !== "visible" &&
        style.overflowY !== "auto" &&
        style.overflowY !== "scroll" &&
        element.scrollHeight > element.clientHeight + 2;
      if (!clipsX && !clipsY) continue;
      const ellipsis = style.textOverflow === "ellipsis" || style.webkitLineClamp !== "none";
      const full =
        element.title ||
        element.closest("[title]")?.getAttribute("title") ||
        element.getAttribute("aria-label") ||
        element.closest("[aria-label]")?.getAttribute("aria-label");
      if (ellipsis && full) continue;
      found.push({
        rule: "clipped-text",
        target: describe(element),
        detail: `${box(element.getBoundingClientRect())} clips its text (${element.scrollWidth}×${element.scrollHeight} content)${ellipsis ? " with an ellipsis but no title" : " without an ellipsis"}; let it wrap, or truncate with text-overflow: ellipsis and a title`,
      });
    }

    // 3. Canvas cards stay inside their canvas and clear of the controls floating over it.
    // The Agent chat floats over content by design and is resized by the researcher, so it is not
    // an overlay here. The inspector counts only where it overlaps the canvas (compact windows).
    for (const stage of document.querySelectorAll<HTMLElement>(".graph-area .stage")) {
      if (!visible(stage)) continue;
      const frame = stage.getBoundingClientRect();
      const minCanvas = Math.min(480, window.innerWidth / 2);
      if (frame.width < minCanvas) {
        found.push({
          rule: "narrow-canvas",
          target: describe(stage),
          detail: `the canvas is ${Math.round(frame.width)}px wide, below ${Math.round(minCanvas)}px; fold side panels before the canvas gets this narrow`,
        });
      }
      const overlays = [
        ...document.querySelectorAll<HTMLElement>(".canvas-footer, .atlas, .project-canvas-tools, #review"),
      ].filter((overlay) => visible(overlay) && !stage.contains(overlay));
      // A map too large to show whole at a readable scale (60 %) may open cropped around its focus;
      // then only the selected card has to be in view. Otherwise every card does.
      const cards = [...stage.querySelectorAll<HTMLElement>(".project-canvas-node, .graph-node")].filter(visible);
      const rects = cards.map((card) => card.getBoundingClientRect());
      // The camera scale: a card's size on screen over its layout size.
      const scale = cards[0]?.offsetWidth ? rects[0]!.width / cards[0].offsetWidth : 1;
      const contentWidth = (Math.max(...rects.map((r) => r.right)) - Math.min(...rects.map((r) => r.left))) / scale;
      const contentHeight = (Math.max(...rects.map((r) => r.bottom)) - Math.min(...rects.map((r) => r.top))) / scale;
      const cropped = Math.min(frame.width / contentWidth, frame.height / contentHeight) < 0.6;
      for (const card of cards) {
        if (cropped && !card.matches('[aria-pressed="true"], [aria-current="true"]')) continue;
        const rect = card.getBoundingClientRect();
        const inside =
          rect.left >= frame.left - 1 &&
          rect.right <= frame.right + 1 &&
          rect.top >= frame.top - 1 &&
          rect.bottom <= frame.bottom + 1;
        if (!inside) {
          found.push({
            rule: "card-outside-canvas",
            target: describe(card),
            detail: `${box(rect)} is outside the canvas ${box(frame)}; fit the camera when a restored view leaves cards out`,
          });
          continue;
        }
        for (const overlay of overlays) {
          if (overlay.contains(card) || card.contains(overlay)) continue;
          const other = overlay.getBoundingClientRect();
          const overlapX = Math.min(rect.right, other.right) - Math.max(rect.left, other.left);
          const overlapY = Math.min(rect.bottom, other.bottom) - Math.max(rect.top, other.top);
          if (overlapX > 2 && overlapY > 2) {
            found.push({
              rule: "card-covered",
              target: describe(card),
              detail: `${box(rect)} is under ${describe(overlay)} ${box(other)}; fit the camera to the free area`,
            });
          }
        }
      }
    }

    // 4. Links styled as buttons look like buttons.
    for (const link of document.querySelectorAll<HTMLElement>("a.button")) {
      if (!visible(link)) continue;
      if (getComputedStyle(link).textDecorationLine !== "none") {
        found.push({
          rule: "underlined-button",
          target: describe(link),
          detail: "a link styled as .button is underlined; .button must reset text-decoration",
        });
      }
    }

    // 5. Interactive targets are large enough to hit (WCAG 2.5.8): at least 24×24, or a small
    // target whose 24×24 square around its centre touches no other target.
    const interactive =
      "button, a[href], select, input:not([type=hidden]), textarea, summary, [role=button], [role=tab], [role=menuitem], [role=option]";
    const targets = [...document.querySelectorAll<HTMLElement>(interactive)].filter(
      (element) => visible(element) && !element.closest("[data-layout-ignore], .sr-only"),
    );
    // A control inside a label, or inside a larger interactive row, is hit through that area.
    const hitArea = (element: HTMLElement) => {
      const owner = element.closest("label") ?? element.parentElement?.closest(interactive) ?? element;
      return owner.getBoundingClientRect();
    };
    const rects = targets.map(hitArea);
    // Inside a zoomable canvas, judge the layout size (before the camera transform): zooming out is
    // the researcher's choice, and counter-scaled handles keep their layout size on screen.
    const designSize = (element: HTMLElement, rect: DOMRect) =>
      element.closest(".plot") && element.offsetWidth
        ? { width: element.offsetWidth, height: element.offsetHeight }
        : { width: rect.width, height: rect.height };
    for (const [index, element] of targets.entries()) {
      const rect = rects[index]!;
      const size = designSize(element, rect);
      if (size.width >= minTarget - 0.5 && size.height >= minTarget - 0.5) continue;
      const style = getComputedStyle(element);
      if (style.display === "inline") continue; // targets inside a sentence are exempt
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const half = minTarget / 2;
      const crowded = rects.some((other, otherIndex) => {
        if (otherIndex === index || targets[otherIndex]!.contains(element) || element.contains(targets[otherIndex]!))
          return false;
        if (other === rect) return false;
        return other.left < cx + half && other.right > cx - half && other.top < cy + half && other.bottom > cy - half;
      });
      if (crowded) {
        found.push({
          rule: "small-target",
          target: describe(element),
          detail: `${box(rect)} is smaller than ${minTarget}×${minTarget} and crowded by another target; enlarge its hit area (padding or a ::before inset) or space it out`,
        });
      }
    }
    return found;
  }, MIN_TARGET);
}

export function formatViolations(violations: Violation[]) {
  return violations.map((v) => `[${v.rule}] ${v.target}: ${v.detail}`).join("\n");
}
