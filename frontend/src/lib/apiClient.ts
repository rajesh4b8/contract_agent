/**
 * The single place the frontend states who it is.
 *
 * The backend derives both the caller's role and their tenant from request
 * headers (`X-User-Role`, `X-Tenant-ID`). In development it falls back to
 * configured defaults when they are absent, which is why the app works today
 * without sending anything — but in production those endpoints answer 401 or
 * 403, so a page that never sends the headers simply cannot work there.
 *
 * This is mock identity, exactly like the backend half. When real auth lands,
 * `identityHeaders()` is the one function that changes: it should read the
 * session's token or claims instead of environment defaults.
 */
const DEV_ROLE = import.meta.env.VITE_USER_ROLE ?? 'LEGAL_REVIEWER';
const DEV_TENANT = import.meta.env.VITE_TENANT_ID ?? 'default-tenant';

export function identityHeaders(): Record<string, string> {
  return {
    'X-User-Role': DEV_ROLE,
    'X-Tenant-ID': DEV_TENANT,
  };
}

/**
 * A fresh correlation id for one user action.
 *
 * The backend's tracing middleware honours `X-Correlation-ID` and stamps it on
 * every log line and debug event the request produces. Sending our own means
 * the debug panel can group a two-minute analysis into one run instead of a
 * flat list of steps.
 */
export function newCorrelationId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `fe-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * How long a request may hang before we call it dead.
 *
 * Generous, because an analysis legitimately takes minutes. But not infinite:
 * `fetch` has no timeout of its own, so a backend that has stopped answering
 * leaves the page on a spinner for ever — no error, no retry, no way out.
 * Observed exactly that with a wedged dev server: "Loading SER-2026-0001…" and
 * nothing else, indefinitely.
 */
export const DEFAULT_TIMEOUT_MS = 30_000;

/** Thrown when a request ran out of time, so callers can say so specifically. */
export class RequestTimeout extends Error {
  constructor(path: string, ms: number) {
    super(
      `The server did not respond within ${Math.round(ms / 1000)}s (${path}). ` +
      `It may be restarting.`,
    );
    this.name = 'RequestTimeout';
  }
}

/**
 * `fetch`, with the caller's identity, a correlation id, and a deadline.
 *
 * Pass `timeoutMs: 0` for the calls that are genuinely allowed to take as long
 * as they take — running an analysis is minutes of model calls.
 */
export async function apiFetch(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<Response> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init;

  const controller = new AbortController();
  const timer =
    timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : undefined;

  try {
    return await fetch(path, {
      ...rest,
      // A caller's own signal wins; otherwise the deadline drives it.
      signal: rest.signal ?? controller.signal,
      headers: {
        ...identityHeaders(),
        'X-Correlation-ID': newCorrelationId(),
        ...(rest.headers ?? {}),
      },
    });
  } catch (e) {
    // `AbortError` is what a timeout looks like from here, and "The user
    // aborted a request" is not a useful thing to show a reviewer.
    if (e instanceof DOMException && e.name === 'AbortError' && timeoutMs > 0) {
      throw new RequestTimeout(path, timeoutMs);
    }
    throw e;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

/**
 * Read the server's explanation out of a failed response.
 *
 * The API answers an unapplicable decision with a specific reason — "a MODIFIED
 * decision needs edited_text" — which is far more use to a reviewer than the
 * status code.
 */
export async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') return body.detail;
    // FastAPI's own validation errors put a list of objects in `detail`.
    if (Array.isArray(body?.detail)) {
      const parts = body.detail.map((d: any) => d?.msg).filter(Boolean);
      if (parts.length > 0) return parts.join('; ');
    }
    if (typeof body?.message === 'string') return body.message;
  } catch {
    // No JSON body; fall through to the generic message.
  }
  return `${fallback} (${response.status})`;
}
