import { createContext, useContext, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { ConversationPreview, ConversationRequest } from "../api/conversations";

type Editor = {
  text: string;
  selectedModel: string;
  recipientId?: string;
  omitHistory?: boolean;
  creation?: { scope: string; id: string };
  pending: "preview" | "send" | "archive" | null;
  error: string;
  request: ConversationRequest | null;
  preview?: { scope: string; payload: ConversationPreview };
};

const EMPTY_EDITOR: Editor = { text: "", selectedModel: "", pending: null, error: "", request: null };
type Change = (editor: Editor) => Editor;

const Editors = createContext<{
  session: string;
  values: Record<string, Editor>;
  update: (id: string, change: Change) => void;
  active: () => boolean;
  reserve: (id: string) => boolean;
  release: (id: string) => void;
} | null>(null);

export function ConversationEditors({ session, children }: { session: string; children: ReactNode }) {
  const [values, setValues] = useState<Record<string, Editor>>({});
  const reserved = useRef(new Set<string>());
  const mounted = useRef(true);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  return (
    <Editors.Provider
      value={{
        session,
        values,
        update: (id, change) => {
          if (!mounted.current) return;
          setValues((previous) => ({ ...previous, [id]: change(previous[id] ?? EMPTY_EDITOR) }));
        },
        active: () => mounted.current,
        reserve: (id) => {
          if (!mounted.current || reserved.current.has(id)) return false;
          reserved.current.add(id);
          return true;
        },
        release: (id) => reserved.current.delete(id),
      }}
    >
      {children}
    </Editors.Provider>
  );
}

export function useConversationEditor(session: string, id: string) {
  const context = useContext(Editors);
  const workspace = context?.session === session ? context : null;
  const [local, setLocal] = useState(EMPTY_EDITOR);
  const localReserved = useRef(false);
  return {
    editor: workspace ? (workspace.values[id] ?? EMPTY_EDITOR) : local,
    update: (change: Change) => (workspace ? workspace.update(id, change) : setLocal(change)),
    updateThread: (threadId: string, change: Change) => workspace?.update(threadId, change),
    keepsRequest: () => Boolean(workspace?.active()),
    reserve: () => {
      if (workspace) return workspace.reserve(id);
      if (localReserved.current) return false;
      localReserved.current = true;
      return true;
    },
    release: () => {
      if (workspace) workspace.release(id);
      else localReserved.current = false;
    },
  };
}

export function useConversationSending(session: string) {
  const context = useContext(Editors);
  const values = context?.session === session ? context.values : {};
  return new Set(
    Object.entries(values)
      .filter(([, editor]) => editor.pending === "send")
      .map(([id]) => id),
  );
}
