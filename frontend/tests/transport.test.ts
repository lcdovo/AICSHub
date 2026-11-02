import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatTransport } from "../src/features/chat/transport";
import { tokens } from "../src/api/client";
class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  closed = false;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(message: string) {
    this.sent.push(message);
  }
  close() {
    this.closed = true;
  }
  receive(event: object) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
  disconnect(code = 1006) {
    this.onclose?.({ code });
  }
}
const payload = { user_id: 101, session_id: "session-a", message: "合成消息" };
beforeEach(() => {
  FakeSocket.instances = [];
  tokens.set("qa-access", "qa-refresh");
  vi.stubGlobal("WebSocket", FakeSocket);
});
describe("单轮 WebSocket 生命周期", () => {
  it("连接前离开页面不发送，连接后仅发送一次", () => {
    const callbacks = { event: vi.fn(), failed: vi.fn(), completed: vi.fn() };
    const transport = new ChatTransport(callbacks);
    transport.start(payload);
    const socket = FakeSocket.instances[0];
    transport.close();
    socket.onopen?.();
    expect(socket.sent).toHaveLength(0);
    expect(socket.closed).toBe(true);
  });
  it("完成后清理连接和计时，不再消费迟到消息", () => {
    const callbacks = { event: vi.fn(), failed: vi.fn(), completed: vi.fn() };
    const transport = new ChatTransport(callbacks);
    transport.start(payload);
    const socket = FakeSocket.instances[0];
    socket.onopen?.();
    socket.receive({ type: "stream_start", session_id: "session-a" });
    socket.receive({
      type: "stream_end",
      session_id: "session-a",
      message_id: 1,
    });
    socket.receive({ type: "token", content: "迟到内容" });
    expect(socket.sent).toHaveLength(1);
    expect(callbacks.event).toHaveBeenCalledTimes(2);
    expect(callbacks.completed).toHaveBeenCalledOnce();
    expect(socket.closed).toBe(true);
  });
  it("连接中断标记不确定且绝不自动重发", () => {
    const callbacks = { event: vi.fn(), failed: vi.fn(), completed: vi.fn() };
    const transport = new ChatTransport(callbacks);
    transport.start(payload);
    const socket = FakeSocket.instances[0];
    socket.onopen?.();
    socket.disconnect();
    expect(callbacks.failed).toHaveBeenCalledWith(
      expect.stringContaining("不会自动重发"),
    );
    expect(FakeSocket.instances).toHaveLength(1);
    expect(socket.sent).toHaveLength(1);
  });
  it("无法解析的协议消息立即结束本轮", () => {
    const callbacks = { event: vi.fn(), failed: vi.fn(), completed: vi.fn() };
    const transport = new ChatTransport(callbacks);
    transport.start(payload);
    const socket = FakeSocket.instances[0];
    socket.onopen?.();
    socket.onmessage?.({ data: "{bad" });
    expect(callbacks.failed).toHaveBeenCalledWith(
      expect.stringContaining("无法识别"),
    );
    expect(socket.closed).toBe(true);
  });
});
