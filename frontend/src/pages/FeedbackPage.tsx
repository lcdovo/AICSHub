import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { api } from "../api";
import {
  Button,
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  dateTime,
} from "../components/ui";
import "../styles/feedback.css";

const filters = [
  { value: "", label: "全部反馈" },
  { value: "dislike", label: "点踩" },
  { value: "like", label: "点赞" },
];
const isLongAnswer = (answer: string | null) =>
  !!answer && (answer.length > 160 || answer.split("\n").length > 3);
const labels: Record<string, string> = {
  like: "点赞",
  dislike: "点踩",
  csat: "满意度",
  comment: "评论",
};

export default function FeedbackPage() {
  const [page, setPage] = useState(1);
  const [type, setType] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const query = useQuery({
    queryKey: ["feedback-records", page, type],
    queryFn: ({ signal }) => api.feedbackRecords(page, type, signal),
  });
  const selectType = (value: string) => {
    setType(value);
    setPage(1);
    setExpanded({});
  };
  const total = query.data?.total ?? 0;
  return (
    <div className="feedback-page">
      <PageHeader
        title="回答反馈"
        description="从用户评价中发现回答的改进空间。"
        actions={
          <Button onClick={() => void query.refetch()} busy={query.isFetching}>
            <RefreshCw size={15} />
            刷新反馈
          </Button>
        }
      />
      <div className="feedback-toolbar">
        <div
          className="feedback-filters"
          role="group"
          aria-label="评价类型快捷筛选"
        >
          {filters.map((filter) => (
            <button
              key={filter.value}
              type="button"
              aria-pressed={type === filter.value}
              onClick={() => selectType(filter.value)}
            >
              {filter.value === "dislike" ? (
                <ThumbsDown size={14} />
              ) : filter.value === "like" ? (
                <ThumbsUp size={14} />
              ) : null}
              {filter.label}
            </button>
          ))}
        </div>
        <label className="feedback-mobile-filter">
          评价类型
          <select
            value={type}
            onChange={(event) => selectType(event.target.value)}
          >
            {filters.map((filter) => (
              <option key={filter.value} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </select>
        </label>
        <span className="feedback-count" role="status">
          {query.isLoading ? (
            "正在读取反馈…"
          ) : query.error ? (
            "反馈读取失败"
          ) : (
            <>
              共 <strong>{total}</strong> 条{type ? labels[type] : "反馈"}
            </>
          )}
        </span>
      </div>
      <ErrorState error={query.error} retry={() => void query.refetch()} />
      <div className="feedback-results" aria-busy={query.isFetching}>
        {query.isLoading && <Loading rows={4} />}
        {query.data && !query.error && (
          <>
            {!query.data.records.length && (
              <Empty
                title={type ? `暂无${labels[type]}反馈` : "还没有收到反馈"}
                description={
                  type
                    ? "切换至全部反馈，查看其他评价。"
                    : "用户对客服回复点赞或点踩后，会显示在这里。"
                }
                action={
                  type ? (
                    <Button onClick={() => selectType("")}>查看全部反馈</Button>
                  ) : undefined
                }
              />
            )}
            {query.data.records.length > 0 && (
              <>
                <div className="feedback-list-heading">
                  <span>评价与客服回复</span>
                  <span>会话追溯</span>
                </div>
                <div className="feedback-list">
                  {query.data.records.map((record) => (
                    <article
                      className="feedback-row"
                      key={record.feedback_id}
                      aria-label={`${labels[record.feedback_type] || "评价"}：${record.username}`}
                    >
                      <div className="feedback-row-content">
                        <header className="feedback-row-meta">
                          <span
                            className={"feedback-badge " + record.feedback_type}
                          >
                            {record.feedback_type === "like" ? (
                              <ThumbsUp size={13} />
                            ) : record.feedback_type === "dislike" ? (
                              <ThumbsDown size={13} />
                            ) : null}
                            {labels[record.feedback_type] ||
                              record.feedback_type}
                          </span>
                          <span className="feedback-author">
                            {record.username}
                          </span>
                          <time dateTime={record.created_at}>
                            {dateTime(record.created_at)}
                          </time>
                        </header>
                        <p
                          className={
                            "feedback-answer" +
                            (expanded[record.feedback_id] ||
                            !isLongAnswer(record.answer)
                              ? " is-expanded"
                              : "")
                          }
                          id={"answer-" + record.feedback_id}
                        >
                          {record.answer ?? "原回复已不可用"}
                        </p>
                        {isLongAnswer(record.answer) && (
                          <button
                            type="button"
                            className="feedback-expand"
                            aria-expanded={!!expanded[record.feedback_id]}
                            aria-controls={"answer-" + record.feedback_id}
                            onClick={() =>
                              setExpanded((old) => ({
                                ...old,
                                [record.feedback_id]: !old[record.feedback_id],
                              }))
                            }
                          >
                            {expanded[record.feedback_id]
                              ? "收起原文"
                              : "展开完整回复"}
                            <ChevronDown size={13} />
                          </button>
                        )}
                      </div>
                      <a
                        className="feedback-session-link"
                        href={"#/chat/" + encodeURIComponent(record.session_id)}
                      >
                        查看会话
                        <ArrowRight size={14} />
                      </a>
                    </article>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
      {query.data && !query.error && total > 0 && (
        <footer className="feedback-pagination">
          <span>
            第 {(page - 1) * 20 + 1}–{Math.min(page * 20, total)} 条，共 {total}{" "}
            条
          </span>
          {total > 20 && (
            <nav aria-label="反馈分页">
              <Button
                aria-label="上一页"
                disabled={page === 1 || query.isFetching}
                onClick={() => {
                  setPage(page - 1);
                  setExpanded({});
                }}
              >
                <ChevronLeft size={15} />
              </Button>
              <span>
                第 {page} / {Math.max(1, Math.ceil(total / 20))} 页
              </span>
              <Button
                aria-label="下一页"
                disabled={page * 20 >= total || query.isFetching}
                onClick={() => {
                  setPage(page + 1);
                  setExpanded({});
                }}
              >
                <ChevronRight size={15} />
              </Button>
            </nav>
          )}
        </footer>
      )}
      <p className="feedback-footnote">
        每条回复保留用户的最新评价；查看会话可了解完整问答上下文。
      </p>
    </div>
  );
}
