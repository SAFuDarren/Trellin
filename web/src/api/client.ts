// Session handling and requests to the local workbench server (docs/interfaces.md, section 4).

import type { ClaimsList } from "./claims.generated";

const SESSION_KEY = "research-multiplier-session";

/** Read the launch token from the URL hash once, keep it for this tab, and remove it from the address bar. */
export function readSession(
  location: Location = window.location,
  storage: Storage | undefined = safeSessionStorage(),
): string | null {
  const token = new URLSearchParams(location.hash.slice(1)).get("session");
  if (token) {
    history.replaceState(null, "", location.pathname + location.search);
    try {
      storage?.setItem(SESSION_KEY, token);
    } catch {
      // The tab can still use the launch token for this page load.
    }
    return token;
  }
  try {
    return storage?.getItem(SESSION_KEY) ?? null;
  } catch {
    return null;
  }
}

function safeSessionStorage(): Storage | undefined {
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
  }
}

function request(
  session: string,
  path: string,
  init: { data?: unknown; signal?: AbortSignal; idempotencyKey?: string } = {},
  extraHeaders: Record<string, string> = {},
): Promise<Response> {
  return fetch(path, {
    method: init.data === undefined ? "GET" : "POST",
    cache: "no-store",
    mode: "same-origin",
    // Retain the origin on local writes without sending a full referrer URL.
    referrerPolicy: init.data === undefined ? "no-referrer" : "origin",
    credentials: "omit",
    redirect: "error",
    headers: {
      ...extraHeaders,
      "X-RM-Session": session,
      ...(init.data === undefined ? {} : { "Content-Type": "application/json" }),
      ...(init.data !== undefined && init.idempotencyKey ? { "Idempotency-Key": init.idempotencyKey } : {}),
    },
    body: init.data === undefined ? undefined : JSON.stringify(init.data),
    signal: init.signal,
  });
}

export function workspaceEventStream(
  session: string,
  workspace: string,
  cursor: string,
  signal: AbortSignal,
  coverage?: string,
) {
  return request(
    session,
    `/api/v1/workspaces/${encodeURIComponent(workspace)}/events`,
    { signal },
    {
      Accept: "text/event-stream",
      "Last-Event-ID": cursor,
      ...(coverage ? { "X-RM-Workbench-Updates": coverage } : {}),
    },
  );
}

export async function api<T>(
  session: string,
  path: string,
  init: { data?: unknown; signal?: AbortSignal; idempotencyKey?: string } = {},
): Promise<T> {
  const response = await request(session, path, init);
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, "unreadable_response");
  }
  if (!response.ok) {
    const error = (payload as { error?: unknown } | null)?.error;
    const details = typeof error === "object" && error ? (error as { message?: unknown; code?: unknown }) : null;
    const message =
      typeof error === "string" ? error : typeof details?.message === "string" ? details.message : "request_failed";
    throw new ApiError(response.status, message, typeof details?.code === "string" ? details.code : undefined);
  }
  return payload as T;
}

export type { Claim } from "./claims.generated";
export type ClaimsResponse = Pick<ClaimsList, "claim_id" | "claim_codes"> & { claims: ClaimsList["items"] };
