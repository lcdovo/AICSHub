import { memo, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowDown, Check, Copy, ThumbsDown, ThumbsUp } from "lucide-react";
import type { Message } from "../../api/types";
import { api } from "../../api";
import { Button, ErrorState, dateTime } from "../../components/ui";
import { useTask } from "../../hooks/useTask";
const plugins = [remarkGfm];
const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={plugins}
      components={{
        table: (props) => (
          <div
            className="markdown-table"
            tabIndex={0}
            role="region"
            aria-label="回答中的表格"
          >
            <table {...props} />
          </div>
        ),
        a: (props) => (
          <a {...props} target="_blank" rel="noopener noreferrer" />
        ),
      }}
    >
      {content}
    </ReactMarkdown>
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
  const [voted, setVoted] = useState(false);
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
                disabled={voted}
                busy={task.pending}
                onClick={() =>
                  task.run(async () => {
                    await api.feedback(userId, sessionId, message, "like");
                    setVoted(true);
                  })
                }
              >
                <ThumbsUp size={14} />
                {voted ? "已反馈" : "有帮助"}
              </Button>
              <Button
                tone="plain"
                disabled={voted}
                onClick={() =>
                  task.run(async () => {
                    await api.feedback(userId, sessionId, message, "dislike");
                    setVoted(true);
                  })
                }
              >
                <ThumbsDown size={14} />
                需改进
              </Button>
            </>
          )}
        </div>
      )}
      <ErrorState error={task.error} />
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
