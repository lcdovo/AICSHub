import { useRef, useState } from "react";
import { api } from "../../api";
import type { Handoff } from "../../api/types";
import { useTask } from "../../hooks/useTask";
import { Button, Dialog, ErrorState, Field, Notice } from "../../components/ui";
export function HandoffDialog({
  userId,
  sessionId,
  onClose,
}: {
  userId: number;
  sessionId: string;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [priority, setPriority] = useState("normal");
  const [receipt, setReceipt] = useState<Handoff>();
  const key = useRef(crypto.randomUUID());
  const task = useTask();
  return (
    <Dialog
      title={receipt ? "人工请求已提交" : "转接人工服务"}
      busy={task.pending}
      onClose={onClose}
    >
      {receipt ? (
        <>
          <Notice kind="success">请求已记录，等待管理员分配处理。</Notice>
          <dl className="details-list">
            <dt>请求编号</dt>
            <dd className="break-word">{receipt.request_id}</dd>
            <dt>状态</dt>
            <dd>待处理</dd>
          </dl>
          <p className="muted">
            当前服务暂不提供用户端队列进度查询，请保留请求编号。
          </p>
          <Button onClick={onClose}>知道了</Button>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void task.run(async () =>
              setReceipt(
                await api.requestHandoff(
                  {
                    user_id: userId,
                    session_id: sessionId,
                    reason: reason.trim(),
                    priority,
                  },
                  key.current,
                ),
              ),
            );
          }}
        >
          <p>提交后，管理员可以查看本会话及你填写的原因。</p>
          <Field label="需要人工协助的原因">
            <textarea
              required
              maxLength={2000}
              rows={4}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </Field>
          <Field label="优先级">
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            >
              <option value="low">低</option>
              <option value="normal">标准</option>
              <option value="high">高</option>
              <option value="urgent">紧急</option>
            </select>
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
              disabled={!reason.trim()}
            >
              确认提交人工请求
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
