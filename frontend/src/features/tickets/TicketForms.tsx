import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { Priority, Ticket, TicketStatus } from "../../api/types";
import { useTask } from "../../hooks/useTask";
import {
  Button,
  Dialog,
  ErrorState,
  Field,
  categories,
  statusLabels,
} from "../../components/ui";
export const transitions: Record<TicketStatus, TicketStatus[]> = {
  pending: ["processing", "escalated"],
  processing: ["resolved", "escalated"],
  escalated: ["processing", "resolved"],
  resolved: ["closed"],
  closed: [],
};
export function PriorityOptions() {
  return (
    <>
      {(["low", "medium", "high", "urgent"] as const).map((value) => (
        <option key={value} value={value}>
          {statusLabels[value]}
        </option>
      ))}
    </>
  );
}
export function CreateTicket({
  userId,
  onClose,
}: {
  userId: number;
  onClose: () => void;
}) {
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("general");
  const [priority, setPriority] = useState<Priority>("medium");
  const key = useRef(crypto.randomUUID());
  const task = useTask();
  const cache = useQueryClient();
  return (
    <Dialog title="创建工单" onClose={onClose} busy={task.pending}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void task.run(async () => {
            await api.createTicket(
              { user_id: userId, category, content: content.trim(), priority },
              key.current,
            );
            await cache.invalidateQueries({ queryKey: ["tickets"] });
            onClose();
          });
        }}
      >
        <p>提交后由客服团队处理。请确认问题描述与优先级。</p>
        <div className="form-grid">
          <Field label="问题类别">
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              {Object.entries(categories).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="优先级">
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as Priority)}
            >
              <PriorityOptions />
            </select>
          </Field>
        </div>
        <Field label="问题描述">
          <textarea
            required
            rows={6}
            maxLength={10000}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="发生了什么？希望我们如何协助？"
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
            disabled={!content.trim()}
          >
            确认创建工单
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
export function EditTicket({
  ticket,
  onClose,
}: {
  ticket: Ticket;
  onClose: () => void;
}) {
  const [status, setStatus] = useState(ticket.status);
  const [priority, setPriority] = useState(ticket.priority);
  const [assigned, setAssigned] = useState(ticket.assigned_to || "");
  const [resolution, setResolution] = useState(ticket.resolution || "");
  const task = useTask();
  const cache = useQueryClient();
  return (
    <Dialog title="处理工单" onClose={onClose} busy={task.pending}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void task.run(async () => {
            await api.updateTicket(ticket.ticket_id, {
              status,
              priority,
              assigned_to: assigned.trim(),
              resolution: resolution.trim(),
            });
            await cache.invalidateQueries({ queryKey: ["tickets"] });
            await cache.invalidateQueries({
              queryKey: ["ticket", ticket.ticket_id],
            });
            onClose();
          });
        }}
      >
        <div className="form-grid">
          <Field label="处理状态">
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as TicketStatus)}
            >
              {[ticket.status, ...transitions[ticket.status]].map((value) => (
                <option value={value} key={value}>
                  {statusLabels[value]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="优先级">
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as Priority)}
            >
              <PriorityOptions />
            </select>
          </Field>
        </div>
        <Field label="负责人标识" hint="按服务端保存的负责人标识填写。">
          <input
            value={assigned}
            onChange={(e) => setAssigned(e.target.value)}
          />
        </Field>
        <Field label="处理结果">
          <textarea
            rows={5}
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
            required={["resolved", "closed"].includes(status)}
          />
        </Field>
        <ErrorState error={task.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose} disabled={task.pending}>
            取消
          </Button>
          <Button type="submit" tone="primary" busy={task.pending}>
            确认保存处理
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
