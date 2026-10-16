// Thin fetch wrapper for the studio API. Relative URLs: the approuter (BTP) or studio_api.py
// (local) serves the UI and /api from the same origin; the approuter adds the user's token.
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown, raw = false): Promise<T> {
  const init: RequestInit = { method, headers: {} as Record<string, string>, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path.replace(/^\//, ""), init);
  if (!res.ok) {
    let msg = res.statusText || `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not JSON */ }
    if (res.status === 401) msg = msg || "Your session expired — reload the page to sign in again.";
    throw new ApiError(res.status, msg);
  }
  if (raw) return (await res.text()) as unknown as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T = any>(p: string) => request<T>("GET", p),
  post: <T = any>(p: string, body?: unknown) => request<T>("POST", p, body ?? {}),
  text: (p: string, body?: unknown) => request<string>(body === undefined ? "GET" : "POST", p, body, true),
  /** URL for downloads (window.open / <a href>) */
  url: (p: string) => p.replace(/^\//, ""),
};
