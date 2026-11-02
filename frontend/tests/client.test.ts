import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  clearCredentials,
  errorText,
  request,
  tokens,
} from "../src/api/client";
const response = (data: unknown, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
beforeEach(() => {
  clearCredentials();
  vi.unstubAllGlobals();
});
describe("统一请求与认证", () => {
  it("同一时刻多个 401 只轮换一次 refresh token", async () => {
    tokens.set("old", "refresh");
    let refreshCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        if (url.endsWith("/refresh")) {
          refreshCount++;
          await new Promise((resolve) => setTimeout(resolve, 10));
          return response({
            code: 0,
            data: { access_token: "new", refresh_token: "next" },
          });
        }
        return new Headers(init.headers).get("Authorization") === "Bearer new"
          ? response({ code: 0, data: "ok" })
          : response({ detail: "expired" }, 401);
      }),
    );
    expect(
      await Promise.all([request("/a"), request("/b"), request("/c")]),
    ).toEqual(["ok", "ok", "ok"]);
    expect(refreshCount).toBe(1);
    expect(tokens.refresh).toBe("next");
  });
  it("退出后迟到的刷新不能恢复登录", async () => {
    tokens.set("old", "refresh");
    let release: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.endsWith("/refresh")
          ? new Promise<Response>((resolve) => {
              release = resolve;
            })
          : Promise.resolve(response({}, 401)),
      ),
    );
    const pending = request("/a");
    await new Promise((resolve) => setTimeout(resolve, 10));
    clearCredentials();
    release(
      response({
        code: 0,
        data: { access_token: "late", refresh_token: "late" },
      }),
    );
    await expect(pending).rejects.toBeInstanceOf(ApiError);
    expect(tokens.access).toBeNull();
  });
  it("401 无刷新令牌清除身份并通知界面", async () => {
    tokens.set("old", "");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response({}, 401)),
    );
    const expired = vi.fn();
    window.addEventListener("auth-expired", expired);
    await expect(request("/secure")).rejects.toBeInstanceOf(ApiError);
    expect(tokens.access).toBeNull();
    expect(expired).toHaveBeenCalledOnce();
    window.removeEventListener("auth-expired", expired);
  });
  it("403 不触发刷新，429 保留 Retry-After", async () => {
    const mock = vi.fn(async () => response({ detail: "forbidden" }, 403));
    vi.stubGlobal("fetch", mock);
    await expect(request("/secure")).rejects.toMatchObject({ status: 403 });
    expect(mock).toHaveBeenCalledOnce();
    mock.mockImplementation(async () =>
      response({ detail: "limit" }, 429, { "Retry-After": "12" }),
    );
    try {
      await request("/secure");
    } catch (e) {
      expect(errorText(e)).toContain("12 秒");
    }
  });
  it("FormData 不伪造 JSON header，支持非 envelope 响应", async () => {
    const mock = vi.fn(async (_url: string, init: RequestInit) => {
      expect(new Headers(init.headers).get("Content-Type")).toBeNull();
      return response({ status: "received" });
    });
    vi.stubGlobal("fetch", mock);
    expect(
      await request("/upload", { method: "POST", body: new FormData() }),
    ).toEqual({ status: "received" });
  });
  it("取消请求保留 AbortError，不包装为断网", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      }),
    );
    await expect(request("/a")).rejects.toMatchObject({ name: "AbortError" });
  });
});
