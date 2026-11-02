import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Session } from "../api/types";
import { useAuth } from "../state/auth";
import { useTask } from "../hooks/useTask";
import {
  Button,
  Dialog,
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  Pagination,
  Status,
  dateTime,
} from "../components/ui";
export default function HistoryPage() {
  const user = useAuth((s) => s.user)!;
  const [page, setPage] = useState(1);
  const [action, setAction] = useState<{ session: Session; remove: boolean }>();
  const task = useTask();
  const cache = useQueryClient();
  const sessions = useQuery({
    queryKey: ["sessions", user.id, false, page],
    queryFn: ({ signal }) => api.sessions(user.id, page, signal),
  });
  return (
    <>
      <PageHeader
        title="会话历史"
        description="继续之前的咨询，或管理已结束的会话。"
        actions={
          <a className="button primary" href="#/chat">
            开始新咨询
          </a>
        }
      />
      <ErrorState
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
      {sessions.isLoading ? (
        <Loading />
      ) : sessions.data?.length ? (
        <div className="history-list">
          {sessions.data.map((session) => (
            <article key={session.id}>
              <a href={"#/chat/" + session.id}>
                <strong>{session.title || "客服会话"}</strong>
                <p>{session.preview || "尚未发送消息"}</p>
                <small>
                  {dateTime(session.updated_at)} · {session.message_count}{" "}
                  条消息
                </small>
              </a>
              <div>
                <Status value={session.status} />
                {session.status !== "closed" && (
                  <Button
                    tone="plain"
                    onClick={() => setAction({ session, remove: false })}
                  >
                    关闭会话
                  </Button>
                )}
                <Button
                  tone="danger"
                  onClick={() => setAction({ session, remove: true })}
                >
                  删除
                </Button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="还没有咨询记录"
          description="发送第一个问题，之后可以在这里继续查看。"
        />
      )}
      <Pagination
        page={page}
        hasNext={sessions.data?.length === 50}
        onChange={setPage}
      />
      {action && (
        <Dialog
          title={action.remove ? "删除会话记录" : "关闭会话"}
          busy={task.pending}
          onClose={() => setAction(undefined)}
        >
          <p>
            {action.remove
              ? "将删除“" +
                (action.session.title || "客服会话") +
                "”及其关联消息与追踪，无法撤销。"
              : "关闭后将保留历史记录，后续咨询请新建会话。"}
          </p>
          <ErrorState error={task.error} />
          <div className="form-actions">
            <Button
              onClick={() => setAction(undefined)}
              disabled={task.pending}
            >
              取消
            </Button>
            <Button
              tone={action.remove ? "danger" : "primary"}
              busy={task.pending}
              onClick={() =>
                task.run(async () => {
                  if (action.remove) await api.deleteSession(action.session.id);
                  else await api.closeSession(action.session.id);
                  await cache.invalidateQueries({ queryKey: ["sessions"] });
                  setAction(undefined);
                })
              }
            >
              {action.remove ? "确认删除" : "确认关闭"}
            </Button>
          </div>
        </Dialog>
      )}
    </>
  );
}
