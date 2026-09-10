import type React from 'react';
import { Search, X } from 'lucide-react';

export const labels: Record<string, string> = { query_order: '订单查询', refund: '退换货', complaint: '投诉', technical: '技术咨询', promotion: '活动咨询', human: '转人工', general: '通用对话' };
export const ticketLabels: Record<string, string> = { pending: '待处理', processing: '处理中', escalated: '已升级', resolved: '已解决', closed: '已关闭' };

export function formatTime(value?: string) { if (!value) return ''; const date = new Date(value); return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }); }

export function Empty({ text }: { text: string }) { return <div className="empty"><Search size={19} /><span>{text}</span></div>; }

export function Metric({ label, value }: { label: string; value: string }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div>; }

export function Stat({ label, value }: { label: string; value: string | number }) { return <div className="stat"><span>{label}</span><strong>{value}</strong></div>; }

export function Badge({ children, tone = 'blue' }: { children: React.ReactNode; tone?: string }) { return <span className={`badge ${tone}`}>{children}</span>; }

export function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) { return <div className="modal-backdrop"><div className="modal"><div className="modal-head"><h3>{title}</h3><button className="icon-btn" onClick={onClose}><X size={17} /></button></div>{children}</div></div>; }
