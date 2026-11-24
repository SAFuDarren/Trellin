import { useState } from "react";

/**
 * Keep the first defined value until `key` changes. A new conversation's default Agent (#328) must not
 * move while the conversation is being created, because the draft and its request belong to that Agent.
 */
export function useStickyValue<T>(value: T | undefined, key: string): T | undefined {
  const [kept, setKept] = useState<{ key: string; value: T | undefined }>({ key, value });
  if (kept.key !== key || (kept.value === undefined && value !== undefined)) {
    setKept({ key, value });
    return value;
  }
  return kept.value;
}
