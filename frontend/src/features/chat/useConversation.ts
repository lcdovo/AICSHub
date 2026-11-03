import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { errorText } from "../../api/client";
import type { Message } from "../../api/types";
import { useChat } from "../../state/chat";
import { beginTurn, emptyRoom, isBusy, reduceStream } from "./protocol";
import { ChatTransport } from "./transport";
export function useConversation(userId: number, id?: string, limit = 50) {
  const cache = useQueryClient();
  const transports = useRef(new Map<string, ChatTransport>());
  const mounted = useRef(true);
  const room = useChat((s) => (id ? s.rooms[id] : undefined));
  const history = useQuery({
    queryKey: ["history", id, limit],
    queryFn: ({ signal }) => api.history(id!, limit, signal),
    enabled: !!id,
  });
  const messages = room?.messages ?? history.data?.messages ?? [];
  useEffect(() => {
    if (!id || !history.data) return;
    const current = useChat.getState().rooms[id];
    if (
      current &&
      !isBusy(current.phase) &&
      history.data.messages.length > current.messages.length
    ) {
      useChat
        .getState()
        .setRoom(id, { ...current, messages: history.data.messages });
    }
  }, [id, limit, history.data]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const [key, transport] of transports.current) {
        transport.close();
        const old = useChat.getState().rooms[key];
        if (old && isBusy(old.phase))
          useChat.getState().setRoom(key, {
            ...old,
            phase: "error",
            error: "已离开会话页面，请刷新历史确认本轮结果。",
          });
      }
      transports.current.clear();
    };
  }, []);
  async function reload(sessionId = id) {
    if (
      !sessionId ||
      isBusy(useChat.getState().rooms[sessionId]?.phase || "idle")
    )
      return;
    const before = useChat.getState().rooms[sessionId];
    const data = await api.history(sessionId, limit);
    if (!mounted.current || useChat.getState().rooms[sessionId] !== before)
      return;
    useChat.getState().setRoom(sessionId, (old) => ({
      ...(old || emptyRoom()),
      messages: data.messages,
      phase: "idle",
      error: "",
    }));
    cache.setQueryData(["history", sessionId, limit], data);
  }
  async function send(
    text: string,
    sessionId: string,
    persisted: Message[],
    rest = false,
    action?: { action_id: string; action_version: number },
  ) {
    if (
      isBusy(useChat.getState().rooms[sessionId]?.phase || "idle") ||
      useChat.getState().rooms[sessionId]?.phase === "complete"
    )
      return;
    useChat
      .getState()
      .setRoom(sessionId, beginTurn(persisted, text, sessionId));
    useChat.getState().setDraft(sessionId, "");
    const event = (data: Parameters<typeof reduceStream>[1]) =>
      mounted.current &&
      useChat
        .getState()
        .setRoom(sessionId, (old) =>
          reduceStream(old || emptyRoom(), data, sessionId),
        );
    const failed = (message: string) =>
      mounted.current &&
      useChat.getState().setRoom(sessionId, (old) => ({
        ...(old || emptyRoom()),
        phase: "error",
        error: message,
      }));
    const complete = () => {
      transports.current.delete(sessionId);
      if (!mounted.current) return;
      void reload(sessionId).catch((e) =>
        failed("回复已收到，历史同步失败：" + errorText(e)),
      );
      void cache.invalidateQueries({ queryKey: ["sessions"] });
    };
    if (rest && !action) {
      try {
        const data = await api.send(userId, text, sessionId);
        event({
          type: "done",
          reply: data.reply,
          intent: data.intent || undefined,
          execution_time_ms: data.response_time_ms,
        });
        event({
          type: "stream_end",
          session_id: sessionId,
          message_id: data.message_id,
        });
        complete();
      } catch (error) {
        failed(errorText(error) + " 请刷新历史确认结果，避免重复提交。");
      }
      return;
    }
    const transport = new ChatTransport({ event, failed, completed: complete });
    transports.current.get(sessionId)?.close();
    transports.current.set(sessionId, transport);
    transport.start({ user_id: userId, session_id: sessionId, message: text, ...action });
  }
  return {
    room,
    messages,
    history,
    reload,
    send,
    busy: isBusy(room?.phase || "idle"),
  };
}
