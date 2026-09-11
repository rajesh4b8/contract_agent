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

/** `fetch`, with the caller's identity attached. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(path, {
    ...init,
    headers: {
      ...identityHeaders(),
      ...(init.headers ?? {}),
    },
  });
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
  } catch {
    // No JSON body; fall through to the generic message.
  }
  return `${fallback} (${response.status})`;
}
