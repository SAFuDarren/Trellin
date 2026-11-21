import { useRef, type KeyboardEvent } from "react";

export function useComposerKeyboard(addressAgent?: () => boolean) {
  const composing = useRef(false);

  return {
    onCompositionStart: () => {
      composing.current = true;
    },
    onCompositionEnd: () => {
      composing.current = false;
    },
    onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // WebKit can report compositionend before the Enter that commits the candidate.
      if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
      if (event.key === "@" && !event.ctrlKey && !event.metaKey && !event.repeat) {
        const { value, selectionStart } = event.currentTarget;
        if (!value.slice(0, selectionStart).trim() && addressAgent?.()) event.preventDefault();
        return;
      }
      if (event.key !== "Enter" || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      if (!event.repeat) event.currentTarget.form?.requestSubmit();
    },
  };
}
