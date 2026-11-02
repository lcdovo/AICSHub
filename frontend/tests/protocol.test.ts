import { describe, expect, it } from "vitest";
import {
  beginTurn,
  parseStream,
  reduceStream,
} from "../src/features/chat/protocol";
describe("单轮流式协议", () => {
  it("隔离其他会话事件，未校验草稿不可见", () => {
    const room = beginTurn([], "问题", "a");
    expect(
      reduceStream(
        room,
        { type: "token", provider: "provider", content: "草稿" },
        "a",
      ).messages[1].content,
    ).toBe("");
    expect(
      reduceStream(room, { type: "stream_start", session_id: "b" }, "a"),
    ).toBe(room);
  });
  it("处理 validated token、重置、最终答复和持久化 ID", () => {
    let room = beginTurn([], "问题", "a");
    room = reduceStream(
      room,
      { type: "stream_mode", token_stream: "validated" },
      "a",
    );
    room = reduceStream(room, { type: "token", content: "被替换回答" }, "a");
    room = reduceStream(room, { type: "token_reset" }, "a");
    expect(room.messages[1].content).toBe("");
    room = reduceStream(
      room,
      { type: "token", provider: "validated", content: "已校验" },
      "a",
    );
    room = reduceStream(
      room,
      { type: "done", reply: "最终回答", execution_time_ms: 20 },
      "a",
    );
    expect(room.phase).toBe("saving");
    expect(room.messages[1].pending).toBe(true);
    room = reduceStream(
      room,
      { type: "stream_end", session_id: "a", message_id: 48 },
      "a",
    );
    expect(room.messages[1]).toMatchObject({
      id: 48,
      content: "最终回答",
      pending: false,
    });
    expect(reduceStream(room, { type: "token", content: "迟到" }, "a")).toBe(
      room,
    );
  });
  it("旧轮临时 ID 不会被新轮覆盖", () => {
    const first = [{ id: 10, role: "assistant" as const, content: "旧回答" }];
    const next = reduceStream(
      beginTurn(first, "新问题", "a"),
      { type: "token", provider: "validated", content: "新回答" },
      "a",
    );
    expect(next.messages[0].content).toBe("旧回答");
    expect(first).toHaveLength(1);
  });
  it("错误会终止忙状态并表达权限或限流", () => {
    expect(
      reduceStream(
        beginTurn([], "问题", "a"),
        { type: "error", code: 429 },
        "a",
      ),
    ).toMatchObject({ phase: "error", error: expect.stringContaining("频繁") });
    expect(
      reduceStream(
        beginTurn([], "问题", "a"),
        { type: "error", code: 403 },
        "a",
      ).error,
    ).toContain("权限");
  });
  it("损坏和未知消息不会崩溃", () => {
    expect(parseStream("bad")).toBeNull();
    expect(parseStream("null")).toBeNull();
    expect(parseStream('{"type":"intent","intent":"refund"}')?.intent).toBe(
      "refund",
    );
  });
});
