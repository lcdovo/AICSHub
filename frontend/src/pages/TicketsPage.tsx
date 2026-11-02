import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Plus, RefreshCw } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../state/auth";
import { navigate, useRoute } from "../app/router";
import {
  Button,
  Empty,
  ErrorState,
  Field,
  Loading,
  PageHeader,
  Pagination,
  Status,
  categories,
  dateTime,
  statusLabels,
} from "../components/ui";
import {
  CreateTicket,
  EditTicket,
  PriorityOptions,
} from "../features/tickets/TicketForms";
export default function TicketsPage() {
  const user = useAuth((s) => s.user)!;
  const admin = user.role === "admin";
  const { id } = useRoute();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [create, setCreate] = useState(false);
  const [edit, setEdit] = useState(false);
  const tickets = useQuery({
    queryKey: ["tickets", page, status, priority],
    queryFn: ({ signal }) => api.tickets(page, status, priority, signal),
  });
  const detail = useQuery({
    queryKey: ["ticket", id],
    queryFn: ({ signal }) => api.ticket(id!, signal),
    enabled: !!id,
  });
  const ticket = detail.data;
  return (
    <>
      <PageHeader
        title={id ? "工单详情" : admin ? "工单管理" : "我的工单"}
        description={
          id
            ? "查看问题描述、负责人和处理结果。"
            : admin
              ? "按状态与优先级安排处理，保留问题的每一步进展。"
              : "查看已提交的问题和处理进展。"
        }
        actions={
          id ? (
            <Button onClick={() => navigate("tickets")}>
              <ArrowLeft size={16} />
              返回列表
            </Button>
          ) : (
            <>
              <Button
                onClick={() => void tickets.refetch()}
                busy={tickets.isFetching}
              >
                <RefreshCw size={16} />
                刷新
              </Button>
              <Button tone="primary" onClick={() => setCreate(true)}>
                <Plus size={16} />
                创建工单
              </Button>
            </>
          )
        }
      />
      {id ? (
        <>
          <ErrorState
            error={detail.error}
            retry={() => void detail.refetch()}
          />
          {detail.isLoading ? (
            <Loading />
          ) : (
            ticket && (
              <div className="detail-layout">
                <section>
                  <div className="section-heading">
                    <h2>{categories[ticket.category] || ticket.category}</h2>
                    <Status value={ticket.status} />
                  </div>
                  <p className="ticket-content">{ticket.content}</p>
                  <section className="resolution">
                    <h2>处理结果</h2>
                    <p>{ticket.resolution || "尚未填写处理结果。"}</p>
                  </section>
                  {admin && (
                    <Button tone="primary" onClick={() => setEdit(true)}>
                      编辑处理状态
                    </Button>
                  )}
                </section>
                <aside className="detail-meta">
                  <h2>工单信息</h2>
                  <dl className="details-list">
                    <dt>工单编号</dt>
                    <dd className="break-word">{ticket.ticket_id}</dd>
                    <dt>优先级</dt>
                    <dd>
                      <Status value={ticket.priority} />
                    </dd>
                    <dt>负责人</dt>
                    <dd>{ticket.assigned_to || "未分配"}</dd>
                    <dt>创建时间</dt>
                    <dd>{dateTime(ticket.created_at)}</dd>
                    <dt>更新时间</dt>
                    <dd>{dateTime(ticket.updated_at)}</dd>
                    {admin && (
                      <>
                        <dt>提交用户</dt>
                        <dd>#{ticket.user_id}</dd>
                      </>
                    )}
                    {ticket.session_id && (
                      <>
                        <dt>关联会话</dt>
                        <dd>
                          <a href={"#/chat/" + ticket.session_id}>查看会话</a>
                        </dd>
                      </>
                    )}
                  </dl>
                </aside>
              </div>
            )
          )}
        </>
      ) : (
        <>
          <div className="filter-bar">
            <Field label="状态">
              <select
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部状态</option>
                {[
                  "pending",
                  "processing",
                  "escalated",
                  "resolved",
                  "closed",
                ].map((value) => (
                  <option key={value} value={value}>
                    {statusLabels[value]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="优先级">
              <select
                value={priority}
                onChange={(e) => {
                  setPriority(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部优先级</option>
                <PriorityOptions />
              </select>
            </Field>
            <span>
              {tickets.data
                ? "共 " + tickets.data.total + " 条工单"
                : "等待工单数据"}
            </span>
          </div>
          <ErrorState
            error={tickets.error}
            retry={() => void tickets.refetch()}
          />
          {tickets.isLoading ? (
            <Loading />
          ) : tickets.data?.tickets.length ? (
            <div className="data-table">
              <table>
                <thead>
                  <tr>
                    <th>问题与类别</th>
                    <th>优先级</th>
                    <th>状态</th>
                    <th>负责人</th>
                    <th className="num">更新时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {tickets.data.tickets.map((item) => (
                    <tr key={item.ticket_id}>
                      <td data-label="问题">
                        <a
                          href={"#/tickets/" + item.ticket_id}
                          className="row-title"
                        >
                          {item.content}
                        </a>
                        <small>
                          {categories[item.category] || item.category} ·{" "}
                          {item.ticket_id.slice(0, 8)}
                        </small>
                      </td>
                      <td data-label="优先级">
                        <Status value={item.priority} />
                      </td>
                      <td data-label="状态">
                        <Status value={item.status} />
                      </td>
                      <td data-label="负责人">
                        {item.assigned_to || "未分配"}
                      </td>
                      <td data-label="更新时间" className="num">
                        {dateTime(item.updated_at)}
                      </td>
                      <td className="row-action">
                        <a href={"#/tickets/" + item.ticket_id}>查看详情</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            tickets.data && (
              <Empty
                title="没有符合条件的工单"
                description="可以调整筛选，或创建新的问题工单。"
              />
            )
          )}
          <Pagination
            page={page}
            total={tickets.data?.total}
            hasNext={!!tickets.data && page * 20 < tickets.data.total}
            onChange={setPage}
          />
        </>
      )}
      {create && (
        <CreateTicket userId={user.id} onClose={() => setCreate(false)} />
      )}
      {edit && ticket && (
        <EditTicket ticket={ticket} onClose={() => setEdit(false)} />
      )}
    </>
  );
}
