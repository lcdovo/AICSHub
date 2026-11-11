import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import type { Session } from "../src/api/types";
import { DeleteSessionButton } from "../src/features/chat/DeleteSessionButton";
import { emptyRoom } from "../src/features/chat/protocol";
import { useChat } from "../src/state/chat";

const session = { id: "synthetic-delete", title: "合成咨询" } as Session;
function wrap() {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const deleted = vi.fn();
  cache.setQueryData(["history", session.id, 50], { messages: [] });
  cache.setQueryData(["session", session.id], session);
  render(<QueryClientProvider client={cache}>
    <DeleteSessionButton session={session} onDeleted={deleted} />
  </QueryClientProvider>);
  return { cache, deleted };
}
beforeEach(() => { useChat.getState().clear(); sessionStorage.clear(); });
describe("聊天列表删除会话", () => {
  it("取消确认不请求删除", () => {
    const remove = vi.spyOn(api, "deleteSession").mockResolvedValue({});
    wrap();
    fireEvent.click(screen.getByRole("button", { name: "删除会话：合成咨询" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(remove).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("确认后删除指定会话并清理缓存，保留其他会话", async () => {
    const remove = vi.spyOn(api, "deleteSession").mockResolvedValue({});
    useChat.getState().setRoom(session.id, emptyRoom());
    useChat.getState().setDraft(session.id, "待清理草稿");
    useChat.getState().setDraft("other", "其他草稿");
    sessionStorage.setItem("aicshub-pending-run:" + session.id, "synthetic-run");
    const { cache, deleted } = wrap();
    fireEvent.click(screen.getByRole("button", { name: "删除会话：合成咨询" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleted).toHaveBeenCalledOnce());
    expect(remove).toHaveBeenCalledWith(session.id);
    expect(useChat.getState().rooms[session.id]).toBeUndefined();
    expect(useChat.getState().drafts[session.id]).toBeUndefined();
    expect(useChat.getState().drafts.other).toBe("其他草稿");
    expect(cache.getQueryData(["history", session.id, 50])).toBeUndefined();
    expect(cache.getQueryData(["session", session.id])).toBeUndefined();
    expect(sessionStorage.getItem("aicshub-pending-run:" + session.id)).toBeNull();
  });
  it("失败时保留会话及确认框，可再次尝试", async () => {
    vi.spyOn(api, "deleteSession").mockRejectedValue(new Error("删除失败，请重试"));
    useChat.getState().setDraft(session.id, "保留草稿");
    const { deleted, cache } = wrap();
    fireEvent.click(screen.getByRole("button", { name: "删除会话：合成咨询" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByText("删除失败，请重试")).toBeInTheDocument();
    expect(deleted).not.toHaveBeenCalled();
    expect(useChat.getState().drafts[session.id]).toBe("保留草稿");
    expect(cache.getQueryData(["session", session.id])).toEqual(session);
    expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled();
  });
  it("回复生成与保存期间禁用删除", () => {
    useChat.getState().setRoom(session.id, { ...emptyRoom(), phase: "streaming" });
    wrap();
    expect(screen.getByRole("button", { name: "删除会话：合成咨询" })).toBeDisabled();
  });
});
