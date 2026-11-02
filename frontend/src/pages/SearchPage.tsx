import { useRef, useState } from "react";
import { Search } from "lucide-react";
import { api } from "../api";
import type { SearchResult } from "../api/types";
import {
  Button,
  Empty,
  ErrorState,
  Field,
  Notice,
  PageHeader,
} from "../components/ui";
import { useEffect } from "react";
export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [data, setData] = useState<SearchResult>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const controller = useRef<AbortController>();
  const sequence = useRef(0);
  useEffect(
    () => () => {
      sequence.current++;
      controller.current?.abort();
    },
    [],
  );
  async function search() {
    const version = ++sequence.current;
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setPending(true);
    setError(undefined);
    setSubmitted(query.trim());
    try {
      const result = await api.search(query.trim(), next.signal);
      if (version === sequence.current) setData(result);
    } catch (e) {
      if (version === sequence.current) setError(e);
    } finally {
      if (version === sequence.current) setPending(false);
    }
  }
  return (
    <>
      <PageHeader
        title="检索测试"
        description="用实际问题核对命中片段与来源，不生成客服回答。"
      />
      <form
        className="retrieval-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) void search();
        }}
      >
        <Field label="测试问题">
          <input
            required
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="例如：退换货有哪些要求？"
          />
        </Field>
        <Button
          type="submit"
          tone="primary"
          busy={pending}
          disabled={!query.trim()}
        >
          <Search size={17} />
          检索知识
        </Button>
      </form>
      <ErrorState error={error} />
      {data?.degraded && (
        <Notice kind="warning">
          降级检索 · {data.backend || "后端未知"}：
          {data.degraded_reason || "未提供原因"}
        </Notice>
      )}
      {data ? (
        <section className="search-results" aria-busy={pending}>
          <div className="section-heading">
            <h2>命中内容</h2>
            <span>
              {data.results.length} 个片段 · {data.backend || "后端未提供名称"}
            </span>
          </div>
          <p className="muted">
            查询：{submitted}
            {pending ? " · 正在更新" : ""}
          </p>
          {data.results.length ? (
            data.results.map((hit, index) => (
              <article key={hit.id + index}>
                <header>
                  <h3>{hit.title}</h3>
                  <span className="num">
                    相关分数{" "}
                    {hit.final_score !== undefined
                      ? hit.final_score.toFixed(3)
                      : hit.score !== undefined
                        ? hit.score.toFixed(3)
                        : "未知"}
                  </span>
                </header>
                <p>{hit.content}</p>
                <footer>
                  <span>来源：{hit.source || "接口未提供"}</span>
                  <span>
                    {hit.category || "未分类"} · {hit.id}
                  </span>
                </footer>
              </article>
            ))
          ) : (
            <Empty
              title="没有命中知识片段"
              description="检查文档发布状态，或换一种问题表述。无命中不代表政策不存在。"
            />
          )}
        </section>
      ) : (
        <Empty
          title="先核对证据，再判断回答"
          description="输入真实业务问题，查看当前索引实际返回的内容。"
        />
      )}
    </>
  );
}
