import type { Message } from "../../api/types";
export type StreamEvent = {
  type: string;
  session_id?: string;
  trace_id?: string;
  content?: string;
  provider?: string;
  token_stream?: string;
  intent?: string;
  reply?: string;
  execution_time_ms?: number;
  message_id?: number;
  code?: number;
  message?: string;
  tool?: string;
  success?: boolean;
  tool_calls?: { tool: string; success: boolean }[];
  need_human?: boolean;
};
export type Phase =
  | "idle"
  | "connecting"
  | "waiting"
  | "streaming"
  | "saving"
  | "complete"
  | "error";
export type Room = {
  messages: Message[];
  phase: Phase;
  error: string;
  traceId?: string;
  intent?: string;
  latency?: number;
  validated: boolean;
  calls: { tool: string; success?: boolean }[];
  human?: boolean;
};
export const emptyRoom = (): Room => ({
  messages: [],
  phase: "idle",
  error: "",
  validated: false,
  calls: [],
});
export const isBusy = (phase: Phase) =>
  ["connecting", "waiting", "streaming", "saving"].includes(phase);
export function beginTurn(
  messages: Message[],
  text: string,
  sessionId: string,
): Room {
  return {
    ...emptyRoom(),
    phase: "connecting",
    messages: [
      ...messages,
      {
        id: -1,
        session_id: sessionId,
        role: "user",
        content: text,
        pending: true,
      },
      {
        id: -2,
        session_id: sessionId,
        role: "assistant",
        content: "",
        pending: true,
      },
    ],
  };
}
function assistant(room: Room, update: Partial<Message>): Message[] {
  return room.messages.map((message) =>
    message.id === -2 ? { ...message, ...update } : message,
  );
}
export function reduceStream(
  room: Room,
  event: StreamEvent,
  sessionId: string,
): Room {
  if (
    !isBusy(room.phase) ||
    (event.session_id && event.session_id !== sessionId)
  )
    return room;
  switch (event.type) {
    case "stream_start":
      return { ...room, phase: "waiting", traceId: event.trace_id };
    case "stream_mode":
      return { ...room, validated: event.token_stream === "validated" };
    case "token":
      return event.provider === "validated" || room.validated
        ? {
            ...room,
            phase: "streaming",
            messages: assistant(room, {
              content:
                (room.messages.find((m) => m.id === -2)?.content || "") +
                (event.content || ""),
            }),
          }
        : room;
    case "token_reset":
      return {
        ...room,
        phase: "waiting",
        messages: assistant(room, { content: "" }),
      };
    case "intent":
      return { ...room, intent: event.intent };
    case "tool_call_start":
      return {
        ...room,
        calls: [
          ...room.calls.filter((c) => c.tool !== event.tool),
          { tool: event.tool || "未知工具" },
        ],
      };
    case "tool_call_complete":
      return {
        ...room,
        calls: [
          ...room.calls.filter((c) => c.tool !== event.tool),
          { tool: event.tool || "未知工具", success: event.success },
        ],
      };
    case "done":
      return {
        ...room,
        phase: "saving",
        messages: assistant(room, {
          content:
            event.reply ??
            room.messages.find((m) => m.id === -2)?.content ??
            "",
        }),
        latency: event.execution_time_ms,
        intent: event.intent || room.intent,
        human: event.need_human,
        calls: event.tool_calls || room.calls,
      };
    case "stream_end":
      return {
        ...room,
        phase: "complete",
        messages: room.messages.map((m) =>
          m.id === -2
            ? { ...m, id: event.message_id || -2, pending: false }
            : m.id === -1
              ? { ...m, pending: false }
              : m,
        ),
      };
    case "error":
      return {
        ...room,
        phase: "error",
        error:
          event.code === 429
            ? "消息发送过于频繁，请稍后再试。请先查看历史，避免重复操作。"
            : event.code === 403
              ? "没有此会话的访问权限。"
              : event.message ||
                "服务未完成本轮回答。请先查看历史，避免重复操作。",
      };
    default:
      return room;
  }
}
export function parseStream(raw: string): StreamEvent | null {
  try {
    const event: unknown = JSON.parse(raw);
    if (
      !event ||
      typeof event !== "object" ||
      !("type" in event) ||
      typeof event.type !== "string"
    )
      return null;
    return event as StreamEvent;
  } catch {
    return null;
  }
}
