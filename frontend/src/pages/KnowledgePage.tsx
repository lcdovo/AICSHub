import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Upload } from "lucide-react";
import { api } from "../api";
import type { KnowledgeDocument } from "../api/types";
import { useTask } from "../hooks/useTask";
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
import {
  DocumentEditor,
  UploadDocument,
} from "../features/knowledge/DocumentEditor";
type Operation = {
  kind: "publish" | "delete" | "seed" | "rebuild";
  document?: KnowledgeDocument;
};
function ConfirmKnowledge({
  operation,
  onClose,
}: {
  operation: Operation;
  onClose: () => void;
}) {
  const task = useTask();
  const cache = useQueryClient();
  const doc = operation.document;
  const copy =
    operation.kind === "delete"
      ? ["删除知识文档", "将软删除“" + doc?.title + "”，该文档将不再参与检索。"]
      : operation.kind === "publish"
        ? [
            doc?.status === "published" ? "取消发布" : "发布知识文档",
            doc?.status === "published"
              ? "取消发布后，该文档不再参与回答检索。"
              : "发布后，该文档可被客服检索引用。",
          ]
        : operation.kind === "seed"
          ? [
              "导入示例知识",
              "将向当前知识库加入内置示例内容。不会清空现有知识，但可能产生重复资料。",
            ]
          : [
              "重建检索索引",
              "将从现有持久化知识重建索引。不会删除文档，过程中检索可能短暂受影响。",
            ];
  return (
    <Dialog title={copy[0]} onClose={onClose} busy={task.pending}>
      <p>{copy[1]}</p>
      <ErrorState error={task.error} />
      <div className="form-actions">
        <Button onClick={onClose} disabled={task.pending}>
          取消
        </Button>
        <Button
          tone={operation.kind === "delete" ? "danger" : "primary"}
          busy={task.pending}
          onClick={() =>
            task.run(async () => {
              if (operation.kind === "delete" && doc)
                await api.deleteDocument(doc.id);
              else if (operation.kind === "publish" && doc)
                await api.publish(doc.id, doc.status !== "published");
              else if (operation.kind === "seed") await api.seed();
              else await api.rebuild();
              await cache.invalidateQueries({ queryKey: ["documents"] });
              onClose();
            })
          }
        >
          确认{copy[0]}
        </Button>
      </div>
    </Dialog>
  );
}
export default function KnowledgePage() {
  const [editor, setEditor] = useState<KnowledgeDocument | "new">();
  const [upload, setUpload] = useState(false);
  const [operation, setOperation] = useState<Operation>();
  const [filter, setFilter] = useState("");
  const docs = useQuery({
    queryKey: ["documents"],
    queryFn: ({ signal }) => api.documents(signal),
  });
  const filtered = docs.data?.documents.filter((doc) =>
    (doc.title + doc.category + doc.source)
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <>
      <PageHeader
        title="知识文档"
        description="管理客服回答的知识来源与发布状态。"
        actions={
          <>
            <Button onClick={() => setUpload(true)}>
              <Upload size={16} />
              上传文件
            </Button>
            <Button tone="primary" onClick={() => setEditor("new")}>
              <Plus size={16} />
              新增文档
            </Button>
          </>
        }
      />
      <div className="knowledge-tools">
        <Field label="查找文档">
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="标题、分类或来源"
          />
        </Field>
        <div className="actions">
          <Button onClick={() => void docs.refetch()} busy={docs.isFetching}>
            <RefreshCw size={16} />
            刷新
          </Button>
          <Button onClick={() => setOperation({ kind: "rebuild" })}>
            重建索引
          </Button>
          <Button onClick={() => setOperation({ kind: "seed" })}>
            导入示例
          </Button>
        </div>
      </div>
      <ErrorState error={docs.error} retry={() => void docs.refetch()} />
      {docs.data?.degraded && (
        <Notice kind="warning">
          检索处于降级模式：{docs.data.degraded_reason || "服务端未提供原因"}
        </Notice>
      )}
      <div className="section-heading">
        <h2>文档目录</h2>
        <span>
          {filtered?.length ?? "未知"} 篇 · 更新时间仅在接口提供时显示
        </span>
      </div>
      {docs.isLoading ? (
        <Loading />
      ) : filtered?.length ? (
        <div className="data-table documents-table">
          <table>
            <thead>
              <tr>
                <th>文档与来源</th>
                <th>发布状态</th>
                <th className="num">分块 / 版本</th>
                <th className="num">更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((doc) => (
                <tr key={doc.id}>
                  <td data-label="文档">
                    <button
                      className="text-button row-title"
                      onClick={() => setEditor(doc)}
                    >
                      {doc.title}
                    </button>
                    <small>
                      {doc.category || "未分类"} · {doc.source || "来源未知"}
                    </small>
                  </td>
                  <td data-label="状态">
                    <Status value={doc.status} />
                  </td>
                  <td data-label="分块 / 版本" className="num">
                    {doc.chunks_count} / v{doc.version}
                  </td>
                  <td data-label="更新时间" className="num">
                    {dateTime(doc.updated_at)}
                  </td>
                  <td className="row-action">
                    <div className="actions">
                      <Button
                        tone="plain"
                        onClick={() =>
                          setOperation({ kind: "publish", document: doc })
                        }
                      >
                        {doc.status === "published" ? "取消发布" : "发布"}
                      </Button>
                      <Button
                        tone="danger"
                        onClick={() =>
                          setOperation({ kind: "delete", document: doc })
                        }
                      >
                        删除
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        docs.data && (
          <Empty
            title={filter ? "没有找到对应文档" : "知识库还没有文档"}
            description="添加经过核对的政策或服务说明，供客服检索。"
          />
        )
      )}
      {editor && (
        <DocumentEditor
          document={editor === "new" ? undefined : editor}
          onClose={() => setEditor(undefined)}
        />
      )}
      {upload && <UploadDocument onClose={() => setUpload(false)} />}
      {operation && (
        <ConfirmKnowledge
          operation={operation}
          onClose={() => setOperation(undefined)}
        />
      )}
    </>
  );
}
