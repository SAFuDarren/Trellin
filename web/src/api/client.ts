// Session handling and requests to the local workbench server (docs/interfaces.md, section 4).

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
  ) {
    super(message);
  }
}

export async function api<T>(
  session: string,
  path: string,
  init: { data?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const response = await fetch(path, {
    method: init.data === undefined ? "GET" : "POST",
    cache: "no-store",
    mode: "same-origin",
    // Retain the origin on local writes without sending a full referrer URL.
    referrerPolicy: init.data === undefined ? "no-referrer" : "origin",
    credentials: "omit",
    redirect: "error",
    headers: {
      "X-RM-Session": session,
      ...(init.data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: init.data === undefined ? undefined : JSON.stringify(init.data),
    signal: init.signal,
  });
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, "unreadable_response");
  }
  if (!response.ok) {
    const error = (payload as { error?: unknown }).error;
    const message =
      typeof error === "string"
        ? error
        : typeof error === "object" && error
          ? String((error as { message?: unknown }).message)
          : "request_failed";
    throw new ApiError(response.status, message);
  }
  return payload as T;
}

export type Claim = { id: string; title: string | null; status: string; domain: string | null };
export type ClaimsResponse = { claims: Claim[]; claim_id: string | null };
