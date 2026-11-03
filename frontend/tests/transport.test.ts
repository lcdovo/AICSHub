import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatTransport } from "../src/features/chat/transport";
import { tokens } from "../src/api/client";
import * as client from "../src/api/client";
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
  sessionStorage.clear();
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
  it("首个回包前断线时只读解析原客户端ID，无法查询仍不自动重发", async () => {
    vi.spyOn(client,"request").mockRejectedValueOnce(new Error("not accepted"));
    const callbacks = { event: vi.fn(), failed: vi.fn(), completed: vi.fn() };
    const transport = new ChatTransport(callbacks);
    transport.start(payload);
    const socket = FakeSocket.instances[0];
    socket.onopen?.();
    socket.disconnect();
    await vi.waitFor(()=>expect(callbacks.failed).toHaveBeenCalledWith(
      expect.stringContaining("不会自动重发"),
    ));
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
  it("同Run重复/乱序事件按sequence恢复，拒绝串会话与串Run", () => {
    const callbacks = {event:vi.fn(),failed:vi.fn(),completed:vi.fn()};
    const transport=new ChatTransport(callbacks);
    transport.start(payload);
    const socket=FakeSocket.instances[0];socket.onopen?.();
    const first={type:"stream_start",run_id:"run-a",session_id:"session-a",sequence:1};
    socket.receive(first);socket.receive(first);
    socket.receive({type:"token",run_id:"run-a",session_id:"session-a",sequence:3,content:"后"});
    socket.receive({type:"token",run_id:"run-b",session_id:"session-a",sequence:2,content:"不属于此Run"});
    socket.receive({type:"token",run_id:"run-a",session_id:"session-b",sequence:2,content:"不属于此会话"});
    socket.receive({type:"token",run_id:"run-a",session_id:"session-a",sequence:2,content:"先"});
    expect(callbacks.event.mock.calls.map(c=>c[0].sequence)).toEqual([1,2,3]);
    expect(callbacks.event.mock.calls.map(c=>c[0].content).filter(Boolean)).toEqual(["先","后"]);
    transport.close();
  });
  it("已知Run断线只读查询游标，不重发消息并恢复原完成事件", async () => {
    const request=vi.spyOn(client,"request").mockImplementation(async path=>path.endsWith("/run-a")?
      {status:"succeeded"}:[{type:"done",run_id:"run-a",session_id:"session-a",sequence:2,reply:"原答案"},
      {type:"stream_end",run_id:"run-a",session_id:"session-a",sequence:3,message_id:9}]);
    const callbacks={event:vi.fn(),failed:vi.fn(),completed:vi.fn()};
    const transport=new ChatTransport(callbacks);transport.start(payload);
    const socket=FakeSocket.instances[0];socket.onopen?.();
    const sent=JSON.parse(socket.sent[0]);expect(sent.client_message_id).toBeTruthy();
    socket.receive({type:"stream_start",run_id:"run-a",session_id:"session-a",sequence:1});
    socket.disconnect();
    await vi.waitFor(()=>expect(callbacks.completed).toHaveBeenCalledOnce());
    expect(request).toHaveBeenCalledWith("/api/v1/chat/runs/run-a/events?after=1");
    expect(socket.sent).toHaveLength(1);
    expect(callbacks.failed).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("aicshub-pending-run:session-a")).toBeNull();
  });
});
