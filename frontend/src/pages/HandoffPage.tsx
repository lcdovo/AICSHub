import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { api } from "../api";
import type { Handoff, HumanAgent } from "../api/types";
import {
  Button,
  Dialog,
  Empty,
  ErrorState,
  Field,
  Loading,
  Notice,
  PageHeader,
  Status,
  dateTime,
} from "../components/ui";
import { useTask } from "../hooks/useTask";
function HandoffAction({
  item,
  agents,
  onClose,
}: {
  item: Handoff;
  agents: HumanAgent[];
  onClose: () => void;
}) {
  const [agent, setAgent] = useState("");
  const [resolution, setResolution] = useState("");
  const task = useTask();
  const cache = useQueryClient();
  const assigning = item.status === "pending";
  return (
    <Dialog
      title={assigning ? "分配人工请求" : "完成处理"}
      onClose={onClose}
      busy={task.pending}
    >
      <p>{item.reason}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void task.run(async () => {
            if (assigning) await api.assign(item.request_id, agent);
            else
              await api.resolve(
                item.request_id,
                resolution.trim(),
                item.assigned_to,
              );
            await cache.invalidateQueries({ queryKey: ["handoffs"] });
            onClose();
          });
        }}
      >
        {assigning ? (
          <Field label="处理客服">
            <select
              required
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
            >
              <option value="">选择已登记客服</option>
              {agents.map((person) => (
                <option key={person.agent_id} value={person.agent_id}>
                  {person.name} · {person.agent_id}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <Field label="处理结果">
            <textarea
              required
              rows={5}
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
            />
          </Field>
        )}
        <ErrorState error={task.error} />
        <div className="form-actions">
          <Button type="button" disabled={task.pending} onClick={onClose}>
            取消
          </Button>
          <Button
            tone="primary"
            type="submit"
            busy={task.pending}
            disabled={assigning ? !agent : !resolution.trim()}
          >
            {assigning ? "确认分配" : "确认完成"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
export default function HandoffPage() {
  const [status, setStatus] = useState("pending");
  const [selected, setSelected] = useState<Handoff>();
  const queue = useQuery({
    queryKey: ["handoffs", status],
    queryFn: ({ signal }) => api.handoffs(status, signal),
  });
  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: ({ signal }) => api.agents(signal),
    staleTime: 60_000,
  });
  return (
    <>
      <PageHeader
        title="人工协同"
        description="将需要人工判断的问题交给合适的人，记录最终处理结果。"
        actions={
          <Button
            onClick={() => {
              void queue.refetch();
              void agents.refetch();
            }}
            busy={queue.isFetching}
          >
            <RefreshCw size={16} />
            刷新队列
          </Button>
        }
      />
      <div className="tabs" role="group" aria-label="人工请求状态">
        {[
          ["pending", "待分配"],
          ["assigned", "处理中"],
          ["resolved", "已解决"],
          ["", "全部"],
        ].map(([value, text]) => (
          <button
            key={value}
            aria-pressed={status === value}
            onClick={() => setStatus(value)}
          >
            {text}
          </button>
        ))}
      </div>
      <div className="queue-layout">
        <section>
          <div className="section-heading">
            <h2>{status === "pending" ? "等待分配的请求" : "人工请求记录"}</h2>
            <small>当前读取最多 100 条</small>
          </div>
          <ErrorState error={queue.error} retry={() => void queue.refetch()} />
          {queue.isLoading ? (
            <Loading />
          ) : queue.data?.requests.length ? (
            <div className="queue-list">
              {queue.data.requests.map((item) => (
                <article key={item.request_id}>
                  <div>
                    <div className="queue-title">
                      <h3>{item.reason}</h3>
                      <Status value={item.priority} />
                    </div>
                    <p>
                      用户 #{item.user_id} · 提交于 {dateTime(item.created_at)}
                    </p>
                    <dl>
                      <dt>状态</dt>
                      <dd>
                        <Status value={item.status} />
                      </dd>
                      <dt>负责人</dt>
                      <dd>
                        {agents.data?.find(
                          (a) => a.agent_id === item.assigned_to,
                        )?.name ||
                          item.assigned_to ||
                          "未分配"}
                      </dd>
                      <dt>等待时间</dt>
                      <dd>
                        {item.status === "pending"
                          ? Math.max(
                              0,
                              Math.floor(
                                (Date.now() -
                                  new Date(
                                    item.created_at +
                                      (item.created_at.endsWith("Z")
                                        ? ""
                                        : "Z"),
                                  ).valueOf()) /
                                  60000,
                              ),
                            ) + " 分钟（本次读取）"
                          : "已进入处理"}
                      </dd>
                      {item.sla_deadline && (
                        <>
                          <dt>服务端截止时间</dt>
                          <dd>{dateTime(item.sla_deadline)}</dd>
                        </>
                      )}
                    </dl>
                    {item.resolution && (
                      <p className="resolution-text">{item.resolution}</p>
                    )}
                    <a href={"#/chat/" + item.session_id}>查看关联会话</a>
                  </div>
                  {["pending", "assigned"].includes(item.status) && (
                    <Button
                      disabled={
                        item.status === "pending" && !agents.data?.length
                      }
                      onClick={() => setSelected(item)}
                    >
                      {item.status === "pending" ? "分配客服" : "处理完成"}
                    </Button>
                  )}
                </article>
              ))}
            </div>
          ) : (
            queue.data && (
              <Empty
                title="当前队列没有请求"
                description="新的人工请求会在刷新后出现在这里。"
              />
            )
          )}
        </section>
        <aside className="agent-directory">
          <h2>客服登记目录</h2>
          <p className="muted">
            登记状态不代表实时在线，分配结果以服务端为准。
          </p>
          <ErrorState
            error={agents.error}
            retry={() => void agents.refetch()}
          />
          {agents.data?.map((person) => (
            <div key={person.agent_id}>
              <strong>{person.name}</strong>
              <small>{person.agent_id}</small>
              <span>{person.skills.join("、")}</span>
            </div>
          ))}
          {agents.data?.length === 0 && (
            <Notice>没有可选客服，暂时无法分配。</Notice>
          )}
        </aside>
      </div>
      {selected && (
        <HandoffAction
          key={selected.request_id}
          item={selected}
          agents={agents.data || []}
          onClose={() => setSelected(undefined)}
        />
      )}
    </>
  );
}
