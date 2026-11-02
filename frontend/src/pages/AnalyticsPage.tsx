import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { api } from "../api";
import type { DependencyHealth } from "../api/types";
import {
  Button,
  Empty,
  ErrorState,
  Loading,
  Notice,
  PageHeader,
  Status,
  dateTime,
  intentLabels,
} from "../components/ui";
const metric = (value?: number, unit = "") =>
  value === undefined || !Number.isFinite(value)
    ? "未知"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(
        value,
      ) + unit;
export default function AnalyticsPage() {
  const summary = useQuery({
    queryKey: ["metrics-summary"],
    queryFn: ({ signal }) => api.summary(signal),
  });
  const metrics = useQuery({
    queryKey: ["metrics"],
    queryFn: ({ signal }) => api.metrics(signal),
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => api.health(signal),
  });
  const intents = Object.entries(summary.data?.intent_distribution || {}).sort(
    (a, b) => b[1] - a[1],
  );
  const maximum = Math.max(1, ...intents.map(([, value]) => value));
  const dependencies = health.data?.dependencies || {};
  const tools = Object.entries(summary.data?.tool_statistics || {});
  const hasRequests = (summary.data?.total_requests ?? 0) > 0;
  return (
    <>
      <PageHeader
        title="运行分析"
        description="当前进程累计指标；未提供历史时间序列。数据按需刷新。"
        actions={
          <Button
            onClick={() => {
              void summary.refetch();
              void metrics.refetch();
              void health.refetch();
            }}
            busy={summary.isFetching || metrics.isFetching || health.isFetching}
          >
            <RefreshCw size={16} />
            刷新数据
          </Button>
        }
      />
      <p className="freshness">
        指标读取：
        {summary.dataUpdatedAt
          ? dateTime(new Date(summary.dataUpdatedAt).toISOString())
          : "尚未完成"}
      </p>
      <ErrorState error={summary.error} retry={() => void summary.refetch()} />
      <div className="metric-strip">
        {[
          ["请求量", metric(summary.data?.total_requests, " 次")],
          [
            "成功率",
            hasRequests ? metric(summary.data?.success_rate, "%") : "未知",
          ],
          [
            "平均响应",
            hasRequests
              ? metric(summary.data?.avg_response_time_ms, " ms")
              : "未知",
          ],
          [
            "P95 响应",
            hasRequests
              ? metric(summary.data?.p95_response_time_ms, " ms")
              : "未知",
          ],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{summary.isLoading ? "加载中" : value}</strong>
          </div>
        ))}
      </div>
      <div className="analytics-layout">
        <section className="analytics-section">
          <div className="section-heading">
            <h2>意图分布</h2>
            <span>累计识别次数</span>
          </div>
          {intents.length ? (
            <div className="bar-chart" role="img" aria-label="意图识别次数分布">
              {intents.map(([intent, value]) => (
                <div key={intent}>
                  <span>{intentLabels[intent] || intent}</span>
                  <div className="bar-track">
                    <i style={{ width: (value / maximum) * 100 + "%" }} />
                  </div>
                  <strong className="num">{metric(value)}</strong>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title={summary.isLoading ? "正在读取意图" : "没有意图统计"}
              description="仅展示服务端已记录的数据。"
            />
          )}
        </section>
        <section className="analytics-section">
          <h2>依赖与服务状态</h2>
          <ErrorState
            error={health.error}
            retry={() => void health.refetch()}
          />
          {health.isLoading && <Loading rows={2} />}
          {Object.entries(dependencies).length ? (
            <ul className="dependency-list">
              {Object.entries(dependencies).map(([name, dependency]) => (
                <li key={name}>
                  <div>
                    <strong>
                      {{
                        database: "数据库",
                        redis: "会话缓存",
                        milvus: "向量检索",
                        llm: "语言模型",
                      }[name] || name}
                    </strong>
                    <small>{dependency.backend || "后端未知"}</small>
                  </div>
                  <Status value={(dependency as DependencyHealth).status} />
                  {dependency.reason && <p>{dependency.reason}</p>}
                </li>
              ))}
            </ul>
          ) : (
            health.data && (
              <Notice>
                健康接口未返回依赖明细。整体状态：{health.data.status}
              </Notice>
            )
          )}
          <p className="muted">
            存活检查不等于回答质量；“已配置”不代表真实模型调用成功。
          </p>
        </section>
        <section className="analytics-section">
          <div className="section-heading">
            <h2>工具统计</h2>
            <span>进程累计</span>
          </div>
          {tools.length ? (
            <ul className="plain-list">
              {tools.map(([name, value]) => (
                <li key={name}>
                  <strong>{name}</strong>
                  <span>
                    调用 {metric(value.total)} · 成功率{" "}
                    {metric(value.success_rate, "%")}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="没有工具统计" />
          )}
        </section>
        <section className="analytics-section">
          <h2>当前告警</h2>
          <ErrorState
            error={metrics.error}
            retry={() => void metrics.refetch()}
          />
          {metrics.data ? (
            metrics.data.alerts.length ? (
              metrics.data.alerts.map((alert, i) => (
                <Notice key={i} kind="warning">
                  {alert.message ||
                    alert.rule_name ||
                    alert.name ||
                    "服务端返回未命名告警"}
                </Notice>
              ))
            ) : (
              <p className="muted">本次读取没有活动告警。</p>
            )
          ) : (
            !metrics.error && <Loading rows={1} />
          )}
        </section>
      </div>
    </>
  );
}
