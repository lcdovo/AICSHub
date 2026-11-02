import { json, request } from "./client";
import type {
  ChatResponse,
  Credentials,
  DocumentInput,
  Handoff,
  HealthResult,
  HumanAgent,
  KnowledgeDocument,
  Message,
  MetricsResult,
  MetricsSummary,
  SearchResult,
  Session,
  Ticket,
  TicketInput,
  TicketUpdate,
  Tool,
  TraceResult,
  User,
  WriteResult,
} from "./types";
export const api = {
  login: (username: string, password: string) =>
    request<Credentials>(
      "/api/v1/users/login",
      json("POST", { username, password }),
      false,
    ),
  me: (id: number, signal?: AbortSignal) =>
    request<User>("/api/v1/users/" + id, { signal }),
  logout: (refresh: string | null) =>
    request<null>(
      "/api/v1/users/logout",
      json("POST", { refresh_token: refresh }),
    ),
  sessions: (userId: number, page = 1, signal?: AbortSignal) =>
    request<Session[]>(
      "/api/v1/chat/sessions/" +
        userId +
        "?include_closed=true&page_size=50&page=" +
        page,
      { signal },
    ),
  allSessions: (page = 1, signal?: AbortSignal) =>
    request<Session[]>("/api/v1/sessions/?page_size=50&page=" + page, {
      signal,
    }),
  session: (id: string, signal?: AbortSignal) =>
    request<Session>("/api/v1/sessions/" + id, { signal }),
  createSession: (userId: number) =>
    request<Session>("/api/v1/chat/sessions?user_id=" + userId, json("POST")),
  closeSession: (id: string) =>
    request("/api/v1/chat/sessions/" + id + "/close", json("PATCH")),
  deleteSession: (id: string) =>
    request("/api/v1/chat/sessions/" + id, json("DELETE")),
  history: (id: string, limit = 50, signal?: AbortSignal) =>
    request<{ session_id: string; messages: Message[] }>(
      "/api/v1/chat/history/" + id + "?limit=" + limit,
      { signal },
    ),
  send: (userId: number, message: string, sessionId: string) =>
    request<ChatResponse>(
      "/api/v1/chat/send",
      json("POST", { user_id: userId, message, session_id: sessionId }),
    ),
  tools: (signal?: AbortSignal) =>
    request<Tool[]>("/api/v1/chat/tools", { signal }),
  trace: (id: string, signal?: AbortSignal) =>
    request<TraceResult>("/api/v1/analytics/traces/" + encodeURIComponent(id), {
      signal,
    }),
  tickets: (
    page: number,
    status: string,
    priority: string,
    signal?: AbortSignal,
  ) =>
    request<{ total: number; tickets: Ticket[] }>(
      "/api/v1/tickets?" +
        new URLSearchParams({
          page: String(page),
          page_size: "20",
          ...(status ? { status } : {}),
          ...(priority ? { priority } : {}),
        }),
      { signal },
    ),
  ticket: (id: string, signal?: AbortSignal) =>
    request<Ticket>("/api/v1/tickets/" + id, { signal }),
  createTicket: (payload: TicketInput, key: string) =>
    request<Ticket>("/api/v1/tickets", {
      ...json("POST", payload),
      headers: { "Idempotency-Key": key },
    }),
  updateTicket: (id: string, payload: TicketUpdate) =>
    request<Ticket>("/api/v1/tickets/" + id, json("PUT", payload)),
  handoffs: (status: string, signal?: AbortSignal) =>
    request<{ total: number; requests: Handoff[] }>(
      "/api/v1/handoff/requests?limit=100" +
        (status ? "&status=" + status : ""),
      { signal },
    ),
  agents: (signal?: AbortSignal) =>
    request<HumanAgent[]>("/api/v1/agents", { signal }),
  assign: (id: string, agent: string) =>
    request(
      "/api/v1/handoff/" +
        id +
        "/assign?" +
        new URLSearchParams({ agent_id: agent }),
      json("POST"),
    ),
  resolve: (id: string, resolution: string, agent?: string | null) =>
    request(
      "/api/v1/handoff/" +
        id +
        "/resolve?" +
        new URLSearchParams({
          resolution,
          ...(agent ? { agent_id: agent } : {}),
        }),
      json("POST"),
    ),
  requestHandoff: (
    payload: {
      user_id: number;
      session_id: string;
      reason: string;
      priority: string;
    },
    key: string,
  ) =>
    request<Handoff>("/api/v1/handoff", {
      ...json("POST", payload),
      headers: { "Idempotency-Key": key },
    }),
  documents: (signal?: AbortSignal) =>
    request<{
      documents: KnowledgeDocument[];
      total_documents?: number;
      total_chunks?: number;
      degraded?: boolean;
      degraded_reason?: string;
    }>("/api/v1/knowledge/documents", { signal }),
  addDocument: (payload: DocumentInput) =>
    request<WriteResult>(
      "/api/v1/knowledge/documents/text",
      json("POST", payload),
    ),
  updateDocument: (id: string, payload: Partial<DocumentInput>) =>
    request<WriteResult>(
      "/api/v1/knowledge/documents/" + id,
      json("PATCH", payload),
    ),
  upload: (data: FormData) =>
    request<WriteResult>("/api/v1/knowledge/documents/upload", {
      method: "POST",
      body: data,
    }),
  publish: (id: string, value: boolean) =>
    request(
      "/api/v1/knowledge/documents/" + id + "/publish?published=" + value,
      json("POST"),
    ),
  deleteDocument: (id: string) =>
    request("/api/v1/knowledge/documents/" + id, json("DELETE")),
  seed: () =>
    request<WriteResult>("/api/v1/knowledge/documents/seed", json("POST")),
  rebuild: () =>
    request<WriteResult>("/api/v1/knowledge/rebuild-index", json("POST")),
  search: (query: string, signal?: AbortSignal) =>
    request<SearchResult>("/api/v1/knowledge/search", {
      ...json("POST", { query, top_k: 5, similarity_threshold: 0.3 }),
      signal,
    }),
  summary: (signal?: AbortSignal) =>
    request<MetricsSummary>("/api/v1/analytics/metrics/summary", { signal }),
  metrics: (signal?: AbortSignal) =>
    request<MetricsResult>("/api/v1/analytics/metrics", { signal }),
  health: (signal?: AbortSignal) =>
    request<HealthResult>("/health", { signal }),
  feedback: (
    userId: number,
    sessionId: string,
    message: Message,
    type: "like" | "dislike",
  ) =>
    request(
      "/api/v1/feedback/submit",
      json("POST", {
        user_id: userId,
        session_id: sessionId,
        message_id: message.id,
        feedback_type: type,
        content: message.content,
      }),
    ),
  users: (page: number, signal?: AbortSignal) =>
    request<User[]>("/api/v1/users/?page_size=50&page=" + page, { signal }),
  createUser: (payload: {
    username: string;
    password: string;
    nickname: string;
    level: string;
  }) => request<User>("/api/v1/users/", json("POST", payload)),
  updateUser: (id: number, payload: { nickname: string; level?: string }) =>
    request("/api/v1/users/" + id, json("PUT", payload)),
  disableUser: (id: number) => request("/api/v1/users/" + id, json("DELETE")),
};
