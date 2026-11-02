import { expireAuth, refreshCredentials, websocketUrl } from "../../api/client";
import { parseStream } from "./protocol";
import type { StreamEvent } from "./protocol";
type TurnCallbacks = {
  event: (event: StreamEvent) => void;
  failed: (message: string) => void;
  completed: () => void;
};
/** One socket owns one submitted turn; never replay after uncertain disconnect. */
export class ChatTransport {
  private socket?: WebSocket;
  private timeout?: ReturnType<typeof setTimeout>;
  private stopped = false;
  private finished = false;
  private sent = false;
  private renewed = false;
  constructor(private callbacks: TurnCallbacks) {}
  start(payload: { user_id: number; session_id: string; message: string }) {
    if (this.stopped) return;
    this.socket = new WebSocket(websocketUrl());
    this.arm(15_000, "连接超时。消息尚未发送，请检查网络。");
    const socket = this.socket;
    socket.onopen = () => {
      if (this.stopped) return;
      this.sent = true;
      socket.send(JSON.stringify(payload));
      this.arm(
        120_000,
        "等待服务响应超时。操作结果尚不确定，请刷新历史后确认。",
      );
    };
    socket.onmessage = (event) => {
      if (this.stopped) return;
      const data = parseStream(String(event.data));
      if (!data) {
        this.fail("收到无法识别的响应，请刷新历史确认本轮结果。");
        return;
      }
      if (data.type === "error" && data.code === 401) expireAuth();
      this.callbacks.event(data);
      this.arm(120_000, "服务响应中断，请刷新历史确认结果。");
      if (data.type === "error") {
        this.finished = true;
        this.close();
      }
      if (data.type === "stream_end") {
        this.finished = true;
        this.close();
        this.callbacks.completed();
      }
    };
    socket.onerror = () => {
      /* close carries authentication status when available. */
    };
    socket.onclose = async (event) => {
      if (this.stopped || this.finished) return;
      if (event.code === 4401 && !this.sent && !this.renewed) {
        this.renewed = true;
        clearTimeout(this.timeout);
        try {
          await refreshCredentials();
          if (!this.stopped) this.start(payload);
        } catch {
          expireAuth();
          this.fail("登录已过期，请重新登录。");
        }
        return;
      }
      this.fail(
        this.sent
          ? "连接已断开，本轮结果尚不确定。请刷新历史后确认，消息不会自动重发。"
          : "未能建立流式连接。可以重试，或明确选择普通请求。",
      );
    };
  }
  private arm(ms: number, message: string) {
    clearTimeout(this.timeout);
    this.timeout = setTimeout(() => this.fail(message), ms);
  }
  private fail(message: string) {
    if (this.stopped) return;
    this.callbacks.failed(message);
    this.close();
  }
  close() {
    this.stopped = true;
    clearTimeout(this.timeout);
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onmessage = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.close();
    }
  }
}
