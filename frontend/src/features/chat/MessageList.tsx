import "../../styles/feedback.css";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, memo, Suspense, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowDown, Check, Copy, ThumbsDown, ThumbsUp } from "lucide-react";
import type { Message } from "../../api/types";
import { api } from "../../api";
import { Button, ErrorState, dateTime } from "../../components/ui";
import { useTask } from "../../hooks/useTask";
const MarkdownContent = lazy(() => import("./MarkdownContent"));
const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <Suspense fallback={<p>{content}</p>}>
      <MarkdownContent content={content} />
    </Suspense>
  );
});
const AssistantContent = memo(function AssistantContent({
  content,
}: {
  content: string;
}) {
  const index = content.search(/\n+参考来源[：:]/);
  const body = index >= 0 ? content.slice(0, index) : content;
  const sources =
    index >= 0 ? content.slice(index).replace(/^\s*参考来源[：:]\s*/, "") : "";
  return (
    <>
      <Markdown content={body} />
      {sources && (
        <details className="citations">
          <summary>查看参考来源</summary>
          <Markdown content={sources} />
        </details>
      )}
    </>
  );
});
const MessageRow = memo(function MessageRow({
  message,
  userId,
  sessionId,
  feedback,
}: {
  message: Message;
  userId: number;
  sessionId: string;
  feedback: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [submitting, setSubmitting] = useState<"like" | "dislike" | null>(null);
  const [voteSaved, setVoteSaved] = useState(false);
  const cache = useQueryClient();
  const votes = useQuery({
    queryKey: ["feedback-votes", userId, sessionId],
    queryFn: ({ signal }) => api.sessionVotes(sessionId, signal),
    enabled: feedback,
  });
  const voted = votes.data?.[String(message.id)];
  const vote = async (type: "like" | "dislike") => {
    setSubmitting(type);
    setVoteSaved(false);
    try {
      await api.feedback(userId, sessionId, message, type);
      cache.setQueryData<Record<string, "like" | "dislike">>(
        ["feedback-votes", userId, sessionId],
        (old) => ({ ...old, [String(message.id)]: type }),
      );
      setVoteSaved(true);
    } finally {
      setSubmitting(null);
    }
  };
  const task = useTask();
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <article
      className={"message " + message.role}
      aria-label={message.role === "user" ? "用户消息" : "客服回复"}
    >
      <header>
        <strong>
          {message.role === "user"
            ? "你"
            : message.role === "system"
              ? "系统提示"
              : "智能客服"}
        </strong>
        <time>
          {message.created_at
            ? dateTime(message.created_at)
            : message.pending
              ? message.role === "user"
                ? "未确认送达"
                : "本轮消息"
              : ""}
        </time>
      </header>
      <div className="message-content">
        {message.role === "assistant" ? (
          <AssistantContent content={message.content} />
        ) : (
          <p>{message.content}</p>
        )}
      </div>
      {message.role === "assistant" && message.content && !message.pending && (
        <div className="message-actions">
          <Button
            tone="plain"
            onClick={() =>
              task.run(async () => {
                await navigator.clipboard.writeText(message.content);
                setCopied(true);
                clearTimeout(timer.current);
                timer.current = setTimeout(() => setCopied(false), 2000);
              })
            }
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? "已复制" : "复制"}
          </Button>
          {feedback && message.id > 0 && (
            <>
              <Button
                tone="plain"
                className="vote-button"
                aria-pressed={voted === "like"}
                disabled={task.pending || votes.isLoading || !!votes.error}
                busy={submitting === "like"}
                onClick={() =>
                  task.run(async () => {
                    await vote("like");
                  })
                }
              >
                <ThumbsUp size={14} />
                {voted === "like" ? "已点赞" : "点赞"}
              </Button>
              <Button
                tone="plain"
                className="vote-button vote-dislike"
                busy={submitting === "dislike"}
                aria-pressed={voted === "dislike"}
                disabled={task.pending || votes.isLoading || !!votes.error}
                onClick={() =>
                  task.run(async () => {
                    await vote("dislike");
                  })
                }
              >
                <ThumbsDown size={14} />
                {voted === "dislike" ? "已点踩" : "点踩"}
              </Button>
              {voteSaved && (
                <span className="vote-status" role="status">
                  评价已保存，感谢反馈
                </span>
              )}
            </>
          )}
        </div>
      )}
      <ErrorState error={task.error || (feedback ? votes.error : null)} />
    </article>
  );
});
export function MessageList({
  messages,
  sessionId,
  userId,
  feedback,
  children,
}: {
  messages: Message[];
  sessionId: string;
  userId: number;
  feedback: boolean;
  children?: ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [away, setAway] = useState(false);
  useEffect(() => {
    pinned.current = true;
    setAway(false);
    container.current?.scrollTo({ top: container.current.scrollHeight });
  }, [sessionId]);
  useEffect(() => {
    if (pinned.current)
      container.current?.scrollTo({ top: container.current.scrollHeight });
  }, [messages]);
  return (
    <div className="message-region">
      <div
        ref={container}
        className="message-scroll"
        onScroll={() => {
          const el = container.current;
          if (!el) return;
          pinned.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 100;
          setAway(!pinned.current);
        }}
      >
        {messages.map((message) => (
          <MessageRow
            key={message.id}
            message={message}
            userId={userId}
            sessionId={sessionId}
            feedback={feedback}
          />
        ))}
        {children}
      </div>
      {away && (
        <Button
          className="new-messages"
          onClick={() => {
            pinned.current = true;
            setAway(false);
            container.current?.scrollTo({
              top: container.current.scrollHeight,
              behavior: "instant",
            });
          }}
        >
          <ArrowDown size={14} />
          回到最新消息
        </Button>
      )}
    </div>
  );
}
