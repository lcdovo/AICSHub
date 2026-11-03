import { expireAuth, refreshCredentials, websocketUrl, request } from "../../api/client";
import { parseStream } from "./protocol";
import type { StreamEvent } from "./protocol";
type TurnCallbacks = {
  event: (event: StreamEvent) => void;
  failed: (message: string) => void;
  completed: () => void;
};
/** 只重放持久Run事件，断线恢复不再次提交业务消息。 */
export class ChatTransport {
  private socket?: WebSocket;
  private timeout?: ReturnType<typeof setTimeout>;
  private stopped = false;
  private finished = false;
  private sent = false;
  private renewed = false;
  private runId?: string;
  private sessionId?: string;
  private clientMessageId?: string;
  private sequence = 0;
  private buffered = new Map<number, StreamEvent>();
  private recovering = false;
  constructor(private callbacks: TurnCallbacks) {}
  start(payload: { user_id: number; session_id: string; message: string; action_id?: string; action_version?: number; client_message_id?: string }) {
    if (this.stopped) return;
    const clientMessageId = payload.client_message_id || crypto.randomUUID();
    payload = {...payload, client_message_id: clientMessageId};
    this.sessionId = payload.session_id;
    this.clientMessageId = payload.client_message_id;
    sessionStorage.setItem("aicshub-pending-client:"+payload.session_id,clientMessageId);
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
      this.consume(data);
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
      if (this.runId) { void this.recover(); return; }
      if (this.sent && this.clientMessageId) { void this.resolveAndRecover(); return; }
      this.fail(
        this.sent
          ? "连接已断开，本轮结果尚不确定。请刷新历史后确认，消息不会自动重发。"
          : "未能建立流式连接。可以重试，或明确选择普通请求。",
      );
    };
  }
  private consume(data: StreamEvent) {
    if (this.stopped || (data.session_id && data.session_id !== this.sessionId)) return;
    if (data.run_id) {
      if (this.runId && data.run_id !== this.runId) return;
      this.runId = data.run_id;
      if (this.sessionId) sessionStorage.setItem("aicshub-pending-run:" + this.sessionId, this.runId);
    }
    if (data.sequence !== undefined) {
      if (!Number.isInteger(data.sequence) || data.sequence <= this.sequence) return;
      if (data.sequence > this.sequence + 1) {
        if (this.buffered.size >= 100) { this.fail("事件队列超限，请查询原轮次。"); return; }
        this.buffered.set(data.sequence, data);
        return;
      }
      this.sequence = data.sequence;
    }
    if (data.type === "error" && data.code === 401) expireAuth();
    this.callbacks.event(data);
    this.arm(120_000, "服务响应中断，请查询原轮次结果。");
    if (data.type === "error" || data.type === "stream_end") {
      this.finished = true;
      if (this.sessionId) {
        sessionStorage.removeItem("aicshub-pending-run:" + this.sessionId);
        sessionStorage.removeItem("aicshub-pending-client:" + this.sessionId);
      }
      this.close();
      if (data.type === "stream_end") this.callbacks.completed();
      return;
    }
    const next = this.buffered.get(this.sequence + 1);
    if (next) { this.buffered.delete(this.sequence + 1); this.consume(next); }
  }
  private async resolveAndRecover() {
    try {
      const run=await request<{run_id:string}>("/api/v1/chat/sessions/"+encodeURIComponent(this.sessionId!)+"/runs/"+encodeURIComponent(this.clientMessageId!));
      if (this.stopped) return;
      this.runId=run.run_id;
      await this.recover();
    } catch {
      if (!this.stopped) this.fail("连接已断开，尚无法确认本轮是否接受。请刷新历史，消息不会自动重发。");
    }
  }
  private async recover() {
    if (this.recovering || !this.runId || this.stopped) return;
    this.recovering = true;
    clearTimeout(this.timeout);
    try {
      for (let attempt = 0; attempt < 90 && !this.stopped; attempt++) {
        const status = await request<{status: string}>("/api/v1/chat/runs/" + this.runId);
        const events = await request<StreamEvent[]>("/api/v1/chat/runs/" + this.runId + "/events?after=" + this.sequence);
        for (const event of events) this.consume(event);
        if (this.stopped) return;
        if (["failed", "cancelled"].includes(status.status)) {
          this.fail("本轮已中断或取消，请查询业务回执，不会自动重做。"); return;
        }
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      if (!this.stopped) this.fail("轮次仍在执行或对账，可稍后刷新历史；不会重新提交。");
    } catch {
      if (!this.stopped) this.fail("暂时无法查询原轮次，请稍后刷新历史，不会自动重做。");
    }
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
