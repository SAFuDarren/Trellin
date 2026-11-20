import { useRef, type KeyboardEvent } from "react";

export function useComposerKeyboard() {
  const composing = useRef(false);

  return {
    onCompositionStart: () => {
      composing.current = true;
    },
    onCompositionEnd: () => {
      composing.current = false;
    },
    onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== "Enter" || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
      // WebKit can report compositionend before the Enter that commits the candidate.
      if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      if (!event.repeat) event.currentTarget.form?.requestSubmit();
    },
  };
}
