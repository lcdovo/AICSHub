import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import type { Room } from "./protocol";
import {
  Button,
  Dialog,
  Empty,
  ErrorState,
  Field,
  Loading,
  intentLabels,
} from "../../components/ui";
export function Diagnostics({
  room,
  onClose,
}: {
  room?: Room;
  onClose: () => void;
}) {
  const [trace, setTrace] = useState(room?.traceId || "");
  const [query, setQuery] = useState("");
  const tools = useQuery({
    queryKey: ["tools"],
    queryFn: ({ signal }) => api.tools(signal),
    staleTime: 300_000,
  });
  const result = useQuery({
    queryKey: ["trace", query],
    queryFn: ({ signal }) => api.trace(query, signal),
    enabled: !!query,
  });
  return (
    <Dialog title="会话诊断" onClose={onClose} wide>
      <section>
        <h3>本轮响应</h3>
        <dl className="details-list">
          <dt>意图</dt>
          <dd>
            {room?.intent ? intentLabels[room.intent] || room.intent : "未知"}
          </dd>
          <dt>响应耗时</dt>
          <dd>{room?.latency !== undefined ? room.latency + " ms" : "未知"}</dd>
          <dt>Trace ID</dt>
          <dd className="break-word">{room?.traceId || "历史记录未提供"}</dd>
        </dl>
        <h3>本轮已调用工具</h3>
        {room?.calls.length ? (
          <ul className="plain-list">
            {room.calls.map((call, i) => (
              <li key={call.tool + i}>
                <span>{call.tool}</span>
                <strong>
                  {call.success === undefined
                    ? "已收到调用事件"
                    : call.success
                      ? "成功"
                      : "失败"}
                </strong>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">
            没有本轮工具调用记录。目录中的工具不代表已被调用。
          </p>
        )}
      </section>
      <section>
        <h3>查询请求追踪</h3>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(trace.trim());
          }}
        >
          <Field label="Trace ID">
            <input
              value={trace}
              onChange={(e) => setTrace(e.target.value)}
              required
            />
          </Field>
          <Button type="submit" busy={result.isFetching}>
            查询追踪
          </Button>
        </form>
        <ErrorState error={result.error} />
        {result.isLoading && <Loading />}
        {result.data &&
          (result.data.spans.length ? (
            <ol className="trace-list">
              {result.data.spans.map((span) => (
                <li key={span.span_id}>
                  <strong>{span.node}</strong>
                  <span>
                    {span.duration_ms} ms · {span.success ? "成功" : "失败"}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <Empty
              title="没有找到持久化 Span"
              description="请求可能尚未落库，或没有对应追踪记录。"
            />
          ))}
      </section>
      <details>
        <summary>可用工具目录</summary>
        <ErrorState error={tools.error} />
        {tools.data?.map((tool) => (
          <div className="tool-description" key={tool.name}>
            <strong>{tool.name}</strong>
            <p>{tool.description}</p>
          </div>
        ))}
      </details>
    </Dialog>
  );
}
