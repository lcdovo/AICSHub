import type { ApiEnvelope, ChatResponse, Document, Handoff, Message, Session, Ticket, Tool, User } from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';
export const tokenStore = {
  get access() { return localStorage.getItem('cs_access_token'); },
  get refresh() { return localStorage.getItem('cs_refresh_token'); },
  set(access: string, refresh: string) { localStorage.setItem('cs_access_token', access); localStorage.setItem('cs_refresh_token', refresh); },
  clear() { localStorage.removeItem('cs_access_token'); localStorage.removeItem('cs_refresh_token'); localStorage.removeItem('cs_user'); },
};

export async function apiFetch<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (tokenStore.access) headers.set('Authorization', `Bearer ${tokenStore.access}`);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const payload = await response.json().catch(() => ({})) as ApiEnvelope<T> & { detail?: string; message?: string };
  if (response.status === 401 && retry && tokenStore.refresh && !path.endsWith('/login')) {
    try {
      const refreshed = await apiFetch<{ access_token: string; refresh_token: string }>('/api/v1/users/refresh', { method: 'POST', body: JSON.stringify({ refresh_token: tokenStore.refresh }) }, false);
      tokenStore.set(refreshed.access_token, refreshed.refresh_token);
      return apiFetch<T>(path, options, false);
    } catch { tokenStore.clear(); window.dispatchEvent(new Event('auth-expired')); }
  }
  if (!response.ok || (payload.code !== undefined && payload.code !== 0)) throw new Error(payload.detail || payload.message || `请求失败（${response.status}）`);
  return payload.data !== undefined ? payload.data : payload as unknown as T;
}

export const api = {
  login: (username: string, password: string) => apiFetch<User & { access_token: string; refresh_token: string }>('/api/v1/users/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => apiFetch<null>('/api/v1/users/logout', { method: 'POST', body: JSON.stringify({ refresh_token: tokenStore.refresh }) }),
  sessions: (userId: number) => apiFetch<Session[]>(`/api/v1/chat/sessions/${userId}?page=1&page_size=50&include_closed=true`),
  createSession: (userId: number) => apiFetch<{ id: string }>(`/api/v1/chat/sessions?user_id=${userId}`, { method: 'POST' }),
  closeSession: (id: string) => apiFetch(`/api/v1/chat/sessions/${id}/close`, { method: 'PATCH' }),
  deleteSession: (id: string) => apiFetch(`/api/v1/chat/sessions/${id}`, { method: 'DELETE' }),
  history: (id: string) => apiFetch<{ session_id: string; messages: Message[] }>(`/api/v1/chat/history/${id}`),
  send: (userId: number, message: string, sessionId?: string) => apiFetch<ChatResponse>('/api/v1/chat/send', { method: 'POST', body: JSON.stringify({ user_id: userId, message, session_id: sessionId }) }),
  tools: () => apiFetch<Tool[]>('/api/v1/chat/tools'),
  tickets: (query = '') => apiFetch<{ total: number; tickets: Ticket[] }>(`/api/v1/tickets?page=1&page_size=50${query}`),
  ticketStats: () => apiFetch<{ total_tickets: number; status_distribution: Record<string, number>; resolution_rate: number }>('/api/v1/tickets/stats'),
  createTicket: (payload: Record<string, unknown>) => apiFetch<Ticket>('/api/v1/tickets', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(payload) }),
  updateTicket: (id: string, payload: Record<string, unknown>) => apiFetch<Ticket>(`/api/v1/tickets/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  handoffs: () => apiFetch<{ total: number; requests: Handoff[] }>('/api/v1/handoff/requests?limit=50'),
  agents: () => apiFetch<Record<string, unknown>[]>('/api/v1/agents'),
  assignHandoff: (id: string, agentId: string) => apiFetch(`/api/v1/handoff/${id}/assign?agent_id=${encodeURIComponent(agentId)}`, { method: 'POST' }),
  resolveHandoff: (id: string, resolution: string, agentId?: string) => apiFetch(`/api/v1/handoff/${id}/resolve?resolution=${encodeURIComponent(resolution)}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ''}`, { method: 'POST' }),
  requestHandoff: (payload: Record<string, unknown>) => apiFetch<Handoff>('/api/v1/handoff', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(payload) }),
  documents: () => apiFetch<{ documents: Document[]; total_documents?: number; total_chunks?: number }>('/api/v1/knowledge/documents'),
  addDocument: (payload: Record<string, unknown>) => apiFetch<Document>('/api/v1/knowledge/documents/text', { method: 'POST', body: JSON.stringify(payload) }),
  uploadDocument: (form: FormData) => apiFetch('/api/v1/knowledge/documents/upload', { method: 'POST', body: form }),
  deleteDocument: (id: string) => apiFetch(`/api/v1/knowledge/documents/${id}`, { method: 'DELETE' }),
  publishDocument: (id: string, published: boolean) => apiFetch(`/api/v1/knowledge/documents/${id}/publish?published=${published}`, { method: 'POST' }),
  seedDocuments: () => apiFetch('/api/v1/knowledge/documents/seed', { method: 'POST' }),
  rebuildIndex: () => apiFetch('/api/v1/knowledge/rebuild-index', { method: 'POST' }),
  searchKnowledge: (query: string) => apiFetch<{ results: { id: string; title: string; content: string; final_score?: number }[] }>('/api/v1/knowledge/search', { method: 'POST', body: JSON.stringify({ query, top_k: 5, similarity_threshold: 0.3 }) }),
  metrics: () => apiFetch<{ metrics: Record<string, any>; alerts: any[] }>('/api/v1/analytics/metrics'),
  summary: () => apiFetch<Record<string, any>>('/api/v1/analytics/metrics/summary'),
  feedback: (payload: Record<string, unknown>) => apiFetch('/api/v1/feedback/submit', { method: 'POST', body: JSON.stringify(payload) }),
  users: () => apiFetch<User[]>('/api/v1/users/?page=1&page_size=100'),
  createUser: (payload: Record<string, unknown>) => apiFetch<User>('/api/v1/users/', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: Record<string, unknown>) => apiFetch(`/api/v1/users/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteUser: (id: number) => apiFetch(`/api/v1/users/${id}`, { method: 'DELETE' }),
};

export function websocketUrl(): string {
  const base = API_BASE || window.location.origin;
  const url = new URL('/api/v1/chat/stream', base);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  if (tokenStore.access) url.searchParams.set('access_token', tokenStore.access);
  return url.toString();
}
