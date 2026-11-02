import type { Credentials } from "./types";

const base = import.meta.env.VITE_API_BASE || "";
export const tokens = {
  get access() {
    return localStorage.getItem("cs_access_token");
  },
  get refresh() {
    return localStorage.getItem("cs_refresh_token");
  },
  set(access: string, refresh: string) {
    localStorage.setItem("cs_access_token", access);
    localStorage.setItem("cs_refresh_token", refresh);
  },
  clear() {
    localStorage.removeItem("cs_access_token");
    localStorage.removeItem("cs_refresh_token");
    localStorage.removeItem("cs_user");
  },
};
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public traceId?: string | null,
    public retryAfter?: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "登录已过期，请重新登录。";
    if (error.status === 403) return "没有访问这项资源的权限。";
    if (error.status === 429)
      return (
        "操作过于频繁，请" +
        (error.retryAfter ? "等待 " + error.retryAfter + " 秒后" : "稍后") +
        "再试。"
      );
    return error.message;
  }
  return error instanceof Error ? error.message : "请求未完成，请重试。";
}
type Payload<T> = {
  code?: number;
  message?: string;
  data?: T;
  detail?: string | { msg: string }[];
};
let refreshing: Promise<Credentials> | undefined;
let epoch = 0;
export function clearCredentials() {
  epoch++;
  tokens.clear();
}
export function expireAuth() {
  clearCredentials();
  window.dispatchEvent(new Event("auth-expired"));
}
async function raw<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (tokens.access) headers.set("Authorization", "Bearer " + tokens.access);
  let response: Response;
  try {
    response = await fetch(base + path, { ...options, headers });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("无法连接服务，请检查网络后重试。", 0);
  }
  const payload = (await response.json().catch(() => ({}))) as Payload<T>;
  if (!response.ok || (payload.code !== undefined && payload.code !== 0)) {
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : Array.isArray(payload.detail)
          ? payload.detail.map((x) => x.msg).join("；")
          : undefined;
    throw new ApiError(
      detail || payload.message || "请求失败（" + response.status + "）",
      response.status,
      response.headers.get("X-Trace-Id"),
      response.headers.get("Retry-After"),
    );
  }
  return Object.prototype.hasOwnProperty.call(payload, "data")
    ? (payload.data as T)
    : (payload as T);
}
export async function refreshCredentials(): Promise<Credentials> {
  if (!refreshing) {
    const startEpoch = epoch;
    const refresh = tokens.refresh;
    if (!refresh) throw new ApiError("登录已过期", 401);
    refreshing = raw<Credentials>("/api/v1/users/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refresh }),
    })
      .then((data) => {
        if (epoch !== startEpoch) throw new ApiError("登录状态已变化", 401);
        tokens.set(data.access_token, data.refresh_token);
        window.dispatchEvent(
          new CustomEvent("auth-refreshed", { detail: data }),
        );
        return data;
      })
      .finally(() => {
        refreshing = undefined;
      });
  }
  return refreshing;
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
  canRefresh = true,
): Promise<T> {
  const access = tokens.access;
  const requestEpoch = epoch;
  try {
    return await raw<T>(path, options);
  } catch (error) {
    if (
      !(error instanceof ApiError) ||
      error.status !== 401 ||
      !canRefresh ||
      /\/(login|refresh)$/.test(path)
    )
      throw error;
    if (requestEpoch !== epoch) throw error;
    try {
      if (access === tokens.access) await refreshCredentials();
    } catch (refreshError) {
      if (refreshError instanceof ApiError && refreshError.status === 401)
        expireAuth();
      throw refreshError;
    }
    try {
      return await raw<T>(path, options);
    } catch (retryError) {
      if (retryError instanceof ApiError && retryError.status === 401)
        expireAuth();
      throw retryError;
    }
  }
}
export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
export function websocketUrl() {
  const url = new URL("/api/v1/chat/stream", base || window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (tokens.access) url.searchParams.set("access_token", tokens.access);
  return url.toString();
}
