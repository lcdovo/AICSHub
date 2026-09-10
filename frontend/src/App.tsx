import { useEffect, useState } from 'react';
import type React from 'react';
import { BookOpen, Bot, Check, ChevronRight, Headphones, LayoutDashboard, LogOut, MessageSquare, PhoneCall, ShieldCheck, Ticket, Users } from 'lucide-react';
import { api, tokenStore } from './api';
import { useAuth } from './store';
import ChatPage from './pages/ChatPage';
import TicketsPage from './pages/TicketsPage';
import HandoffPage from './pages/HandoffPage';
import KnowledgePage from './pages/KnowledgePage';
import AnalyticsPage from './pages/AnalyticsPage';
import UsersPage from './pages/UsersPage';

type Page = 'chat' | 'tickets' | 'handoff' | 'knowledge' | 'analytics' | 'users';

function App() {
  const auth = useAuth();
  const [page, setPage] = useState<Page>('chat');
  const [notice, setNotice] = useState<{ text: string; type: 'ok' | 'error' } | null>(null);
  useEffect(() => { auth.hydrate(); const onExpire = () => auth.clear(); window.addEventListener('auth-expired', onExpire); return () => window.removeEventListener('auth-expired', onExpire); }, []);
  const flash = (text: string, type: 'ok' | 'error' = 'ok') => { setNotice({ text, type }); window.setTimeout(() => setNotice(null), 3500); };
  if (!auth.hydrated) return <div className="boot">正在加载工作台…</div>;
  if (!auth.user || !tokenStore.access) return <Login onSuccess={(u, a, r) => auth.setSession(u, a, r)} onError={(e) => flash(e, 'error')} />;
  const isAdmin = auth.user.role === 'admin';
  const effectivePage = !isAdmin && !['chat', 'tickets'].includes(page) ? 'chat' : page;
  return <div className={`shell ${isAdmin ? 'admin-shell' : 'user-shell'}`}><Sidebar page={effectivePage} setPage={setPage} user={auth.user} onLogout={async () => { try { await api.logout(); } finally { auth.clear(); } }} />
    <main className="content"><Topbar page={effectivePage} user={auth.user} isAdmin={isAdmin} />{effectivePage === 'chat' && <ChatPage userId={auth.user.id} flash={flash} isAdmin={isAdmin} />}{effectivePage === 'tickets' && <TicketsPage userId={auth.user.id} flash={flash} isAdmin={isAdmin} />}{effectivePage === 'handoff' && isAdmin && <HandoffPage flash={flash} />}{effectivePage === 'knowledge' && isAdmin && <KnowledgePage flash={flash} isAdmin />}{effectivePage === 'analytics' && isAdmin && <AnalyticsPage />}{effectivePage === 'users' && isAdmin && <UsersPage flash={flash} />}</main>
    {notice && <div className={`toast ${notice.type}`}><Check size={16} />{notice.text}</div>}</div>;
}

function Login({ onSuccess, onError }: { onSuccess: (u: any, a: string, r: string) => void; onError: (e: string) => void }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState(''); const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  async function submit(e: React.FormEvent) { e.preventDefault(); setError(''); setLoading(true); try { const data = await api.login(username, password); onSuccess(data, data.access_token, data.refresh_token); } catch (err) { const message = (err as Error).message; setError(message); onError(message); } finally { setLoading(false); } }
  return <div className="login"><div className="login-card"><div className="brand-mark"><Bot size={28} /></div><p className="eyebrow">AI APPLICATION STUDIO</p><h1>AI 客服工作台</h1><p className="muted">连接 Agent、知识库与人工协同的智能服务体验</p><form onSubmit={submit}><label>用户名<input required minLength={3} value={username} onChange={(e) => setUsername(e.target.value)} placeholder="输入用户名" /></label><label>密码<input required minLength={8} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="输入密码" /></label>{error && <div className="login-error" role="alert">{error}。请确认账号密码，或检查本地演示账号是否已启用。</div>}<button className="primary wide" disabled={loading}>{loading ? '登录中…' : '进入工作台'}<ChevronRight size={17} /></button></form><div className="demo-hint">本地演示：普通用户 <code>user001 / user12345</code>；管理员 <code>admin / admin12345</code></div></div></div>;
}

function Sidebar({ page, setPage, user, onLogout }: { page: Page; setPage: (p: Page) => void; user: any; onLogout: () => void }) {
  const isAdmin = user.role === 'admin';
  const nav: [Page, string, React.ElementType][] = isAdmin
    ? [['chat', '对话监控', MessageSquare], ['tickets', '工单管理', Ticket], ['handoff', '人工协同', Headphones], ['knowledge', '知识库管理', BookOpen], ['analytics', '运行分析', LayoutDashboard], ['users', '用户管理', Users]]
    : [['chat', '我的对话', MessageSquare], ['tickets', '我的工单', Ticket]];
  return <aside className="sidebar"><div className="logo"><div className="logo-icon"><Bot size={22} /></div><div><strong>{isAdmin ? 'AI 客服管理台' : 'AI 客服服务台'}</strong><span>{isAdmin ? '运营与 Agent 控制中心' : '专属智能服务'}</span></div></div><div className={`workspace-pill ${isAdmin ? 'admin-pill' : ''}`}><span className="live-dot" />{isAdmin ? '管理员模式' : '服务在线'}</div><nav>{nav.map(([key, text, Icon]) => <button key={key} className={page === key ? 'nav active' : 'nav'} onClick={() => setPage(key)}><Icon size={18} />{text}<ChevronRight size={15} className="nav-arrow" /></button>)}</nav>{!isAdmin && <div className="user-help"><PhoneCall size={15} /><span>需要人工帮助？<button onClick={() => setPage('chat')}>去对话</button></span></div>}<div className="sidebar-bottom"><div className="profile"><div className="avatar">{(user.nickname || user.username)[0].toUpperCase()}</div><div><strong>{user.nickname || user.username}</strong><span>{isAdmin ? '管理员' : '普通用户'}</span></div></div><button className="logout" onClick={onLogout}><LogOut size={16} />退出登录</button></div></aside>;
}
function Topbar({ page, user, isAdmin }: { page: Page; user: any; isAdmin: boolean }) { const titles: Record<Page, string> = { chat: isAdmin ? '对话监控' : '我的智能客服', tickets: isAdmin ? '工单管理' : '我的工单', handoff: '人工协同', knowledge: '知识库管理', analytics: '运行分析', users: '用户管理' }; return <header className="topbar"><div><p className="eyebrow">{isAdmin ? 'ADMIN CONSOLE' : 'CUSTOMER SERVICE'} / {page.toUpperCase()}</p><h2>{titles[page]}</h2></div><div className="top-actions"><span className="secure"><ShieldCheck size={15} />{isAdmin ? '管理员权限已验证' : '会话安全保护'}</span><div className="top-avatar">{(user.nickname || user.username)[0].toUpperCase()}</div></div></header>; }

export default App;
