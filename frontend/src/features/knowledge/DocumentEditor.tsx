import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { DocumentInput, KnowledgeDocument } from "../../api/types";
import { useTask } from "../../hooks/useTask";
import { Button, Dialog, ErrorState, Field, Notice } from "../../components/ui";
export function DocumentEditor({
  document,
  onClose,
}: {
  document?: KnowledgeDocument;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(document?.title || "");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState(document?.category || "");
  const [source, setSource] = useState(document?.source || "");
  const [keywords, setKeywords] = useState(document?.keywords.join("，") || "");
  const task = useTask();
  const cache = useQueryClient();
  return (
    <Dialog
      title={document ? "编辑知识文档" : "新增知识文档"}
      onClose={onClose}
      busy={task.pending}
      wide
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void task.run(async () => {
            const payload: Partial<DocumentInput> = {
              title: title.trim(),
              ...(document ? { expected_version: document.version } : {}),
              category,
              source,
              keywords: keywords
                .split(/[,，]/)
                .map((s) => s.trim())
                .filter(Boolean),
              ...(content.trim() ? { content: content.trim() } : {}),
            };
            const result = document
              ? await api.updateDocument(document.id, payload)
              : await api.addDocument(payload as DocumentInput);
            if (result.success === false)
              throw new Error(result.message || "保存失败");
            await cache.invalidateQueries({ queryKey: ["documents"] });
            onClose();
          });
        }}
      >
        <Notice>
          {document
            ? "列表接口不返回原文。正文留空会保留原内容；填写后将替换完整正文并生成新版本。"
            : "保存后排队索引，验证成功才发布。任务失败时保留原发布版本。"}
        </Notice>
        <Field label="文档标题">
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
          />
        </Field>
        <div className="form-grid">
          <Field label="分类">
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </Field>
          <Field label="来源">
            <input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="来源名称或 URL"
            />
          </Field>
        </div>
        <Field label="关键词" hint="使用中文或英文逗号分隔。">
          <input
            value={keywords}
            onChange={(e) => setKeywords(e.target.value)}
          />
        </Field>
        <Field label={document ? "替换正文（可选）" : "知识正文"}>
          <textarea
            required={!document}
            rows={9}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        </Field>
        <ErrorState error={task.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose} disabled={task.pending}>
            取消
          </Button>
          <Button
            type="submit"
            tone="primary"
            busy={task.pending}
            disabled={!title.trim() || (!document && !content.trim())}
          >
            {document ? "确认更新版本" : "保存并排队索引"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
export function UploadDocument({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File>();
  const [category, setCategory] = useState("");
  const task = useTask();
  const cache = useQueryClient();
  return (
    <Dialog title="上传知识文件" onClose={onClose} busy={task.pending}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!file) return;
          void task.run(async () => {
            const form = new FormData();
            form.set("file", file);
            form.set("category", category);
            const result = await api.upload(form);
            if (result.success === false)
              throw new Error(result.message || "导入未完成");
            await cache.invalidateQueries({ queryKey: ["documents"] });
            onClose();
          });
        }}
      >
        <Notice>上传后排队索引，列表会显示草稿、索引中、失败或已发布。</Notice>
        <Field
          label="文件"
          hint="支持 txt、md、json、csv；大小限制以服务端为准。"
        >
          <input
            type="file"
            accept=".txt,.md,.markdown,.json,.csv"
            required
            onChange={(e) => setFile(e.target.files?.[0])}
          />
        </Field>
        <Field label="分类">
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
        </Field>
        <ErrorState error={task.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose} disabled={task.pending}>
            取消
          </Button>
          <Button
            type="submit"
            tone="primary"
            busy={task.pending}
            disabled={!file}
          >
            确认上传并导入
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
