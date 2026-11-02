import { beforeEach, describe, expect, it } from "vitest";
import { useAuth } from "../src/state/auth";
import { useChat } from "../src/state/chat";
import { beginTurn } from "../src/features/chat/protocol";
import { tokens } from "../src/api/client";
beforeEach(() => {
  useAuth.getState().clear();
});
describe("账户状态隔离", () => {
  it("退出时删除令牌和上一账户的聊天草稿与回复", () => {
    useChat
      .getState()
      .setRoom("secret-room", beginTurn([], "私密合成问题", "secret-room"));
    useChat.getState().setDraft("secret-room", "未发送草稿");
    tokens.set("access", "refresh");
    useAuth.getState().clear();
    expect(tokens.access).toBeNull();
    expect(useChat.getState().rooms).toEqual({});
    expect(useChat.getState().drafts).toEqual({});
  });
});
