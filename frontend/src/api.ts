const CSRF_COOKIE = "pc_csrf";
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Fired when the server says the session is gone, so the app can reset its user state. */
export const SESSION_EXPIRED_EVENT = "promptcode:session-expired";

function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** Field path (e.g. "password", "test_cases.0.expected") -> message, for inline errors. */
    public fields: Record<string, string> = {},
  ) {
    super(message);
  }
}

interface ErrorBody {
  detail?: unknown;
  fields?: Record<string, string>;
}

function errorMessage(body: ErrorBody | null, status: number): string {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d: { loc?: string[]; msg?: string }) => d.msg)
      .filter(Boolean)
      .join("\n");
  }
  if (status === 429) return "You're going a bit fast. Please wait a moment and try again.";
  if (status >= 500) return "Something went wrong on our side. Please try again.";
  return `Request failed (${status}).`;
}

/** Statuses that mean "the server is briefly unavailable", not "your request is wrong". */
const TRANSIENT = new Set([0, 502, 503, 504]);
// Backoff for safe (GET) requests: a backend restart (uvicorn --reload takes a few seconds)
// or a network blip is retried quietly for ~16 seconds before the user ever sees an error.
const RETRY_DELAYS_MS = [300, 700, 1200, 2000, 3000, 4000, 5000];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function isTransient(err: unknown): boolean {
  return err instanceof ApiError && TRANSIENT.has(err.status);
}

async function send(path: string, method: string, headers: Record<string, string>, body?: unknown) {
  try {
    const res = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    // A plain-text 500 comes from the dev proxy (backend unreachable), not from our API,
    // which always answers in JSON.
    const fromProxy = res.status === 500 && !res.headers.get("content-type")?.includes("json");
    return { res, status: fromProxy ? 503 : res.status };
  } catch {
    return { res: null, status: 0 };
  }
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  // Only GETs are retried: re-sending a POST (e.g. a submission) could run it twice.
  let attempt = await send(path, method, headers, options.body);
  if (method === "GET") {
    for (const delay of RETRY_DELAYS_MS) {
      if (!TRANSIENT.has(attempt.status)) break;
      await sleep(delay);
      attempt = await send(path, method, headers, options.body);
    }
  }

  const { res, status } = attempt;
  if (!res || TRANSIENT.has(status)) {
    throw new ApiError(
      status,
      status === 0
        ? "Can't reach the server. Check your connection - we'll retry automatically."
        : "The server is restarting or busy. Retrying shortly…",
    );
  }

  if (res.status === 204) return undefined as T;
  const body: ErrorBody | null = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/google")) {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    throw new ApiError(res.status, errorMessage(body, res.status), body?.fields ?? {});
  }
  return body as T;
}
