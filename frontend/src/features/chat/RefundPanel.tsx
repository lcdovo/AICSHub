import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, json } from "../../api/client";
import { Button, ErrorState, Field, dateTime } from "../../components/ui";
import { useTask } from "../../hooks/useTask";

type Action = { action_id: string; version: number; order_id: string; type: string; reason: string;
  amount: string; currency: string; expires_at: string; status: string };
type Application = { refund_id: string; order_id: string; amount: string; currency: string;
  status: string; status_label: string; is_simulated: boolean; review_note?: string | null };

export function RefundPanel({ sessionId, busy, admin, confirm }: {
  sessionId: string; busy: boolean; admin: boolean;
  confirm: (id: string, version: number) => Promise<void>;
}) {
  const task = useTask();
  const [note, setNote] = useState("");
  const actions = useQuery({ queryKey: ["refund-actions", sessionId],
    queryFn: () => request<Action[]>("/api/v1/refunds/actions/pending?session_id=" + encodeURIComponent(sessionId)),
    refetchInterval: busy ? false : 2000 });
  const applications = useQuery({ queryKey: ["refund-applications", sessionId],
    queryFn: () => request<Application[]>("/api/v1/refunds/?session_id=" + encodeURIComponent(sessionId)),
    refetchInterval: busy ? false : 2000 });
  if (!actions.data?.length && !applications.data?.length && !actions.error && !applications.error) return null;
  return <section className="account-section" aria-label="模拟退款动作与申请">
    <h2>模拟退换货</h2>
    <p>确认只登记待审核申请；审核通过也不会执行支付。</p>
    <ErrorState error={actions.error || applications.error || task.error} />
    {actions.data?.map(action => <div key={action.action_id} className="details-list">
      <p><strong>{action.order_id}</strong> · {action.type} · {action.amount} {action.currency}</p>
      <p>{action.reason}</p>
      <p>版本 {action.version} · 有效至 {dateTime(action.expires_at + (action.expires_at.endsWith("Z") ? "" : "Z"))}</p>
      <div className="form-actions">
        <Button disabled={busy || task.pending || admin} onClick={() => task.run(() => confirm(action.action_id, action.version))}>确认此模拟申请</Button>
        <Button disabled={busy || task.pending} onClick={() => task.run(async () => {
          await request("/api/v1/refunds/actions/" + action.action_id + "/cancel", json("POST"));
          await actions.refetch();
        })}>取消此预览</Button>
      </div>
    </div>)}
    {admin && <Field label="模拟审核意见"><input value={note} onChange={event => setNote(event.target.value)} /></Field>}
    {applications.data?.map(item => <div key={item.refund_id}>
      <p><strong>{item.order_id}</strong> · {item.amount} {item.currency} · {item.status_label}</p>
      <p className="break-word">申请号：{item.refund_id}</p>
      {item.review_note && <p>审核意见：{item.review_note}</p>}
      {admin && item.status === "pending_review" && <div className="form-actions">
        {(["approved", "rejected"] as const).map(decision => <Button key={decision} disabled={!note.trim() || task.pending}
          onClick={() => task.run(async () => {
            await request("/api/v1/refunds/" + item.refund_id + "/review", json("POST", { decision, note: note.trim() }));
            await applications.refetch();
          })}>{decision === "approved" ? "模拟审核通过（不支付）" : "拒绝模拟申请"}</Button>)}
      </div>}
    </div>)}
  </section>;
}
