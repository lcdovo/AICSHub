import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, History, Plus, Send, Settings2 } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../state/auth";
import { useChat } from "../state/chat";
import { navigate, useRoute } from "../app/router";
import {
  Button,
  Dialog,
  Empty,
  ErrorState,
  Loading,
  Notice,
  PageHeader,
  Pagination,
  Status,
  dateTime,
  intentLabels,
} from "../components/ui";
import { useTask } from "../hooks/useTask";
import { useConversation } from "../features/chat/useConversation";
import { MessageList } from "../features/chat/MessageList";
import { HandoffDialog } from "../features/chat/HandoffDialog";
import { Diagnostics } from "../features/chat/Diagnostics";
import { RefundPanel } from "../features/chat/RefundPanel";
import { DeleteSessionButton } from "../features/chat/DeleteSessionButton";
export default function ChatPage() {
  const user = useAuth((s) => s.user)!;
  const admin = user.role === "admin";
  const { id } = useRoute();
  const cache = useQueryClient();
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(50);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [humanOpen, setHumanOpen] = useState(false);
  const [diagnostics, setDiagnostics] = useState(false);
  const [mode, setMode] = useState<"stream" | "rest">("stream");
  const task = useTask();
  const sessions = useQuery({
    queryKey: ["sessions", user.id, admin, page],
    queryFn: ({ signal }) =>
      admin
        ? api.allSessions(page, signal)
        : api.sessions(user.id, page, signal),
  });
  const details = useQuery({
    queryKey: ["session", id],
    queryFn: ({ signal }) => api.session(id!, signal),
    enabled: !!id,
  });
  const conversation = useConversation(user.id, id, limit);
  const draftKey = id || "new";
  const input = useChat((s) => s.drafts[draftKey] || "");
  const setInput = (value: string) =>
    useChat.getState().setDraft(draftKey, value);
  const own = !id || details.data?.user_id === user.id;
  const closed = details.data?.status === "closed";
  const canSend =
    !conversation.busy &&
    conversation.room?.phase !== "complete" &&
    (!id ||
      (!!details.data &&
        !conversation.history.isFetching &&
        !conversation.history.error &&
        own &&
        !closed));
  const create = async () => {
    const session = await api.createSession(user.id);
    cache.setQueryData(["session", session.id], {
      ...session,
      user_id: user.id,
    });
    await cache.invalidateQueries({ queryKey: ["sessions"] });
    navigate("chat", session.id);
    setHistoryOpen(false);
  };
  async function send() {
    if (!input.trim() || !canSend) return;
    await task.run(async () => {
      let sessionId = id;
      if (!sessionId) {
        const session = await api.createSession(user.id);
        sessionId = session.id;
        cache.setQueryData(["session", sessionId], {
          ...session,
          user_id: user.id,
        });
        navigate("chat", sessionId);
        useChat.getState().setDraft("new", "");
      }
      await conversation.send(
        input.trim(),
        sessionId,
        conversation.messages,
        mode === "rest",
      );
    });
  }
  const historyList = (
    <>
      <div className="session-toolbar">
        <h2>{admin ? "会话记录" : "我的会话"}</h2>
        <Button
          tone="plain"
          aria-label="刷新会话"
          onClick={() => void sessions.refetch()}
        >
          刷新
        </Button>
      </div>
      <div className="session-items">
        <ErrorState
          error={sessions.error}
          retry={() => void sessions.refetch()}
        />
        {sessions.isLoading ? (
          <Loading />
        ) : !sessions.data?.length ? (
          <Empty
            title="还没有会话"
            description="开始一次咨询，记录会保存在这里。"
          />
        ) : (
          sessions.data.map((session) => (
            <div className="session-row" key={session.id}>
            <a
              href={"#/chat/" + session.id}
              className={
                "session-item" + (session.id === id ? " selected" : "")
              }
              key={session.id}
              onClick={() => {
                setHistoryOpen(false);
                setLimit(50);
              }}
            >
              <strong>
                {admin && session.user_id
                  ? "用户 #" + session.user_id + " · "
                  : ""}
                {session.title ||
                  intentLabels[session.last_intent || ""] ||
                  "客服会话"}
              </strong>
              <span>
                {session.preview ||
                  (session.user_id
                    ? "会话 " +
                      session.id.slice(0, 8) +
                      " · " +
                      session.message_count +
                      " 条消息"
                    : "尚未开始咨询")}
              </span>
              <small>
                {dateTime(session.updated_at || session.created_at)} ·{" "}
                {session.status === "closed" ? "已关闭" : "进行中"}
              </small>
            </a>
            <DeleteSessionButton session={session} onDeleted={() => {
              if (session.id === id) {
                setHistoryOpen(false);
                setHumanOpen(false);
                setLimit(50);
                navigate("chat");
              }
              if (sessions.data.length === 1 && page > 1) setPage(page - 1);
            }} />
            </div>
          ))
        )}
      </div>
      <Pagination
        page={page}
        hasNext={sessions.data?.length === 50}
        onChange={setPage}
      />
    </>
  );
  return (
    <div className="chat-page">
      <PageHeader
        title={admin ? "会话工作台" : "我的智能客服"}
        description={
          admin
            ? "查看会话记录，定位问题与回答依据。数据按需刷新。"
            : "咨询订单、售后与使用问题，接着上次的对话继续。"
        }
        actions={
          <>
            <Button
              className="history-toggle"
              onClick={() => setHistoryOpen(true)}
            >
              <History size={16} />
              会话
            </Button>
            <Button busy={task.pending} onClick={() => task.run(create)}>
              <Plus size={16} />
              {admin ? "新建测试会话" : "新建会话"}
            </Button>
          </>
        }
      />
      <ErrorState error={task.error} />
      <div className="conversation-layout">
        <aside className="conversation-list" aria-label="会话列表">
          {historyList}
        </aside>
        <section className="conversation-pane">
          <header className="conversation-heading">
            <div>
              <h2>
                {id
                  ? details.data?.last_intent
                    ? intentLabels[details.data.last_intent] || "客服会话"
                    : "客服会话"
                  : "开始一次咨询"}
              </h2>
              <span>
                {id ? (
                  <Status value={details.data?.status} />
                ) : (
                  "问题会保存为独立会话"
                )}
              </span>
            </div>
            <div className="actions">
              {admin && (
                <Button tone="plain" onClick={() => setDiagnostics(true)}>
                  <Settings2 size={16} />
                  诊断
                </Button>
              )}
              {!admin && id && (
                <Button onClick={() => setHumanOpen(true)}>转人工</Button>
              )}
            </div>
          </header>
          <ErrorState
            error={details.error}
            retry={() => void details.refetch()}
          />
          <ErrorState
            error={conversation.history.error}
            retry={() => task.run(() => conversation.reload())}
          />
          {id && conversation.history.isLoading ? (
            <Loading label="正在读取会话" />
          ) : (
            <MessageList
              messages={
                conversation.room?.phase === "error"
                  ? conversation.messages.filter(
                      (message) =>
                        message.role !== "assistant" || !!message.content,
                    )
                  : conversation.messages
              }
              sessionId={id || ""}
              userId={user.id}
              feedback={!admin && own}
            >
              {conversation.messages.length === 0 && (
                <div className="conversation-welcome">
                  <h2>{admin ? "从一条测试消息开始" : "有什么可以帮你？"}</h2>
                  <p>
                    说明你的问题或订单号。涉及退款等业务操作时，请核对客服给出的确认信息。
                  </p>
                  <div className="quick-questions">
                    {[
                      "退换货有哪些要求？",
                      "如何查询订单物流？",
                      "产品使用遇到问题怎么办？",
                    ].map((question) => (
                      <button key={question} onClick={() => setInput(question)}>
                        {question}
                        <ChevronRight size={16} />
                      </button>
                    ))}
                  </div>
                  <small>回答以知识库与业务工具的实际结果为依据。</small>
                </div>
              )}
              {id && conversation.messages.length >= limit && (
                <Button
                  tone="plain"
                  onClick={() => setLimit((value) => value + 50)}
                >
                  加载更多历史（当前读取上限 {limit} 条）
                </Button>
              )}
            </MessageList>
          )}
          {id && <RefundPanel sessionId={id} busy={conversation.busy} admin={admin}
            confirm={(action_id, action_version) => conversation.send("确认", id, conversation.messages, false, { action_id, action_version })} />}
          {conversation.room?.error && (
            <Notice kind="error">
              {conversation.room.error}
              <Button
                tone="plain"
                onClick={() => task.run(() => conversation.reload())}
              >
                刷新本轮记录
              </Button>
            </Notice>
          )}
          {(conversation.busy || conversation.room?.phase === "complete") && (
            <div className="stream-status" role="status">
              {conversation.room?.phase === "connecting"
                ? "正在连接服务…"
                : conversation.room?.phase === "complete"
                  ? "回答已保存，正在同步会话记录…"
                  : conversation.room?.phase === "saving"
                    ? "回答已接收，正在保存…"
                    : conversation.room?.phase === "streaming"
                      ? "正在接收经服务端校验的答复…"
                      : "正在处理并校验回答…"}
            </div>
          )}
          {id && !own ? (
            <div className="read-only">
              其他用户的会话只读。新建测试会话后可以测试 Agent。
            </div>
          ) : closed ? (
            <div className="read-only">会话已关闭。请新建会话继续咨询。</div>
          ) : (
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <label className="sr-only" htmlFor="message-input">
                你的问题
              </label>
              <textarea
                id="message-input"
                rows={2}
                maxLength={5000}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing &&
                    e.keyCode !== 229
                  ) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder="输入你的问题…"
                disabled={conversation.busy || task.pending}
              />
              <div className="composer-footer">
                <label className="transport-choice">
                  传输
                  <select
                    aria-label="消息传输方式"
                    value={mode}
                    onChange={(e) =>
                      setMode(e.target.value as "stream" | "rest")
                    }
                    disabled={conversation.busy}
                  >
                    <option value="stream">流式连接</option>
                    <option value="rest">普通请求</option>
                  </select>
                </label>
                <span className="composer-hint">
                  Enter 发送，Shift + Enter 换行
                </span>
                <Button
                  type="submit"
                  tone="primary"
                  disabled={!canSend || !input.trim()}
                  busy={conversation.busy || task.pending}
                >
                  <Send size={16} />
                  发送
                </Button>
              </div>
            </form>
          )}
        </section>
      </div>
      {historyOpen && (
        <Dialog title="会话历史" onClose={() => setHistoryOpen(false)}>
          <div className="mobile-session-list">{historyList}</div>
        </Dialog>
      )}
      {humanOpen && id && (
        <HandoffDialog
          key={id}
          userId={user.id}
          sessionId={id}
          onClose={() => setHumanOpen(false)}
        />
      )}
      {diagnostics && (
        <Diagnostics
          room={conversation.room}
          onClose={() => setDiagnostics(false)}
        />
      )}
    </div>
  );
}
