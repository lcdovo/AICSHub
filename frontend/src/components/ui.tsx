import { cloneElement, isValidElement, useEffect, useId, useRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  Loader2,
  X,
} from "lucide-react";
import { ApiError, errorText } from "../api/client";
export function Button({
  tone = "secondary",
  busy,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "secondary" | "plain" | "danger";
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      aria-busy={busy || undefined}
      className={"button " + tone + " " + className}
    >
      {busy && <Loader2 className="spin" size={16} />}
      {children}
    </button>
  );
}
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand">
      <svg
        width="32"
        height="32"
        viewBox="0 0 32 32"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M5 6h22v14H15l-7 6v-6H5V6Z"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="M10 11h12M10 15h8"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
      <span>
        <strong>AICSHub</strong>
        {!compact && <small>智能客服</small>}
      </span>
    </div>
  );
}
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}
export function Notice({
  children,
  kind = "info",
}: {
  children: ReactNode;
  kind?: "info" | "error" | "success" | "warning";
}) {
  return (
    <div
      className={"notice " + kind}
      role={kind === "error" ? "alert" : "status"}
    >
      {kind === "success" ? <Check size={16} /> : <AlertCircle size={16} />}
      <div>{children}</div>
    </div>
  );
}
export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <Notice kind="error">
      <p>{errorText(error)}</p>
      {error instanceof ApiError && error.traceId && (
        <small>请求标识：{error.traceId}</small>
      )}
      {retry && (
        <Button tone="plain" onClick={retry}>
          重试
        </Button>
      )}
    </Notice>
  );
}
export function Empty({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
export function Loading({
  label = "正在加载",
  rows = 3,
}: {
  label?: string;
  rows?: number;
}) {
  return (
    <div className="loading" role="status" aria-label={label}>
      <span>{label}…</span>
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton" key={i} />
      ))}
    </div>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  const id = useId();
  const hintId = id + "-hint";
  const control = isValidElement<{ id?: string; "aria-describedby"?: string }>(
    children,
  )
    ? cloneElement(children, {
        id,
        ...(hint ? { "aria-describedby": hintId } : {}),
      })
    : children;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {control}
      {hint && <small id={hintId}>{hint}</small>}
    </div>
  );
}
export function Dialog({
  title,
  children,
  onClose,
  busy = false,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    el?.showModal();
    return () => {
      el?.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className={"dialog" + (wide ? " dialog-wide" : "")}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="dialog-content">
        <header>
          <h2 id={titleId}>{title}</h2>
          <Button
            tone="plain"
            aria-label="关闭弹窗"
            onClick={onClose}
            disabled={busy}
          >
            <X size={20} />
          </Button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
export const statusLabels: Record<string, string> = {
  pending: "待处理",
  processing: "处理中",
  assigned: "已分配",
  escalated: "已升级",
  resolved: "已解决",
  closed: "已关闭",
  active: "进行中",
  published: "已发布",
  draft: "未发布",
  low: "低",
  medium: "标准",
  normal: "标准",
  high: "高",
  urgent: "紧急",
  healthy: "正常",
  degraded: "已降级",
  configured: "已配置，未实测",
  disabled: "未启用",
  optional: "可选依赖",
  unknown: "未知",
  unhealthy: "异常",
};
export const intentLabels: Record<string, string> = {
  query_order: "订单查询",
  refund: "退换货",
  complaint: "投诉建议",
  technical: "技术咨询",
  promotion: "活动咨询",
  human: "转人工",
  general: "通用咨询",
};
export const categories: Record<string, string> = {
  general: "通用咨询",
  order: "订单问题",
  refund: "退换货",
  complaint: "投诉建议",
  technical: "技术咨询",
};
export function Status({ value }: { value?: string | null }) {
  const tone = ["resolved", "published", "healthy"].includes(value || "")
    ? "positive"
    : ["unhealthy", "urgent"].includes(value || "")
      ? "negative"
      : ["degraded", "escalated", "high"].includes(value || "")
        ? "warning"
        : "neutral";
  return (
    <span className={"status " + tone}>
      {value ? statusLabels[value] || value : "未知"}
    </span>
  );
}
export function dateTime(value?: string | null) {
  if (!value) return "未知";
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + "Z";
  const d = new Date(normalized);
  return Number.isNaN(d.valueOf())
    ? "未知"
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Shanghai",
      }).format(d);
}
export function Pagination({
  page,
  hasNext,
  total,
  onChange,
}: {
  page: number;
  hasNext: boolean;
  total?: number;
  onChange: (page: number) => void;
}) {
  return (
    <nav className="pagination" aria-label="分页">
      <span>
        第 {page} 页{total !== undefined ? " · 共 " + total + " 条" : ""}
      </span>
      <div>
        <Button disabled={page <= 1} onClick={() => onChange(page - 1)}>
          <ArrowLeft size={16} />
          上一页
        </Button>
        <Button disabled={!hasNext} onClick={() => onChange(page + 1)}>
          下一页
          <ArrowRight size={16} />
        </Button>
      </div>
    </nav>
  );
}
