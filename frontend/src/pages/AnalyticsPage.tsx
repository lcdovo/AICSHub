import { useEffect, useState } from 'react';
import { CircleAlert, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { Empty, Stat, labels } from '../components/common';

export default function AnalyticsPage() {
  const [summary, setSummary] = useState<any>(); const [metrics, setMetrics] = useState<any>(); const [error, setError] = useState('');
  const load = () => Promise.all([api.summary(), api.metrics()]).then(([s, m]) => { setSummary(s); setMetrics(m); }).catch((e) => setError(e.message)); useEffect(() => { void load(); }, []);
  const intents = Object.entries(summary?.intent_distribution || {});
  return <div className="page-body"><div className="page-actions"><div><p className="eyebrow">OBSERVABILITY & EVALUATION</p><h3>运行分析</h3><p className="muted">查看 Agent 质量、延迟与工具调用指标。</p></div><button className="secondary" onClick={load}><RefreshCw size={15} />刷新数据</button></div>{error ? <div className="panel error-box"><CircleAlert size={18} />{error}</div> : <><div className="stat-row"><Stat label="总请求" value={summary?.total_requests ?? '—'} /><Stat label="成功率" value={summary ? `${summary.success_rate}%` : '—'} /><Stat label="平均耗时" value={summary ? `${summary.avg_response_time_ms} ms` : '—'} /><Stat label="P95 耗时" value={summary ? `${summary.p95_response_time_ms} ms` : '—'} /></div><div className="analytics-grid"><div className="panel chart-panel"><div className="panel-head"><div><h3>意图分布</h3><span className="muted">近 24 小时</span></div></div>{intents.length ? intents.map(([key, value]) => <div className="bar-row" key={key}><span>{labels[key] || key}</span><div className="bar"><i style={{ width: `${Math.min(100, Number(value) / Math.max(...intents.map(([, v]) => Number(v))) * 100)}%` }} /></div><strong>{String(value)}</strong></div>) : <Empty text="暂无指标数据" />}</div><div className="panel chart-panel"><div className="panel-head"><div><h3>系统状态</h3><span className="muted">运行时诊断</span></div></div><div className="health"><div><span className="live-dot" />服务在线</div><p>活跃会话：{summary?.session_count ?? '—'}</p><p>工具调用：{Object.keys(summary?.tool_statistics || {}).length}</p><p>告警：{metrics?.alerts?.length || 0}</p></div></div></div></>}</div>;
}
