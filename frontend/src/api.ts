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

function requestHeaders(method: string, hasBody: boolean): Record<string, string> {
  const headers: Record<string, string> = {};
  if (hasBody) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  return headers;
}

async function failure(res: Response, path: string): Promise<ApiError> {
  const body: ErrorBody | null = await res.json().catch(() => null);
  if (res.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/google")) {
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  return new ApiError(res.status, errorMessage(body, res.status), body?.fields ?? {});
}

/**
 * POST that streams newline-delimited JSON progress events (see RunProgress) and resolves with
 * the final `result` event's data. Falls back gracefully if a proxy delivers it all at once.
 */
export async function apiStream<T, E extends { type: string }>(
  path: string,
  body: unknown,
  onEvent: (event: E) => void,
): Promise<T> {
  const headers = { ...requestHeaders("POST", true), Accept: "application/x-ndjson" };
  const { res, status } = await send(path, "POST", headers, body);
  if (!res || TRANSIENT.has(status)) {
    throw new ApiError(status, status === 0 ? "Can't reach the server. Check your connection and try again." : "The server is restarting or busy. Please try again shortly.");
  }
  if (!res.ok) throw await failure(res, path);
  if (!res.headers.get("content-type")?.includes("ndjson")) return (await res.json()) as T;

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | undefined;
  const handle = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "result") result = event.data as T;
    else if (event.type === "error") throw new ApiError(event.status ?? 500, errorMessage({ detail: event.detail }, event.status ?? 500));
    else onEvent(event as E);
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline: number;
      while ((newline = buffer.indexOf("\n")) !== -1) {
        handle(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
      }
    }
    handle(buffer);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, "The connection dropped while grading. If you submitted, check the Submissions tab - it may still have been saved.");
  }
  if (result === undefined) throw new ApiError(0, "The server ended the response early. Please try again.");
  return result;
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers = requestHeaders(method, options.body !== undefined);

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
  if (!res.ok) throw await failure(res, path);
  return (await res.json().catch(() => null)) as T;
}
