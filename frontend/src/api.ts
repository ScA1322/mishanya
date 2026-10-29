// Клиент API панели. Сессия хранится в HttpOnly-cookie, CSRF-токен — только в памяти страницы.

let csrfToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setCsrf(token: string | null) {
  csrfToken = token;
}

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  status: number;
  details: { loc: string; msg: string }[];
  constructor(status: number, message: string, details: { loc: string; msg: string }[] = []) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

function describe(detail: unknown, status: number): { message: string; details: { loc: string; msg: string }[] } {
  if (typeof detail === "string") return { message: detail, details: [] };
  if (Array.isArray(detail)) {
    const items = detail.map((d) => ({ loc: String(d?.loc ?? ""), msg: String(d?.msg ?? d) }));
    const message = items.map((d) => (d.loc ? `${d.loc}: ${d.msg}` : d.msg)).join("; ");
    return { message: message || "Ошибка проверки данных", details: items };
  }
  return { message: `Ошибка сервера (${status})`, details: [] };
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "Нет связи с backend Mishanya. Проверьте, что приложение запущено (start.bat).");
  }
  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/login")) onUnauthorized?.();
    const { message, details } = describe(data?.detail ?? data, res.status);
    throw new ApiError(res.status, message, details);
  }
  return data as T;
}

export const get = <T = any>(path: string) => api<T>("GET", path);
export const post = <T = any>(path: string, body?: unknown) => api<T>("POST", path, body ?? {});
export const put = <T = any>(path: string, body?: unknown) => api<T>("PUT", path, body ?? {});
export const patch = <T = any>(path: string, body?: unknown) => api<T>("PATCH", path, body ?? {});
export const del = <T = any>(path: string) => api<T>("DELETE", path);

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
