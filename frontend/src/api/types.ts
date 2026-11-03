export type User = {
  id: number;
  username: string;
  nickname: string;
  level: string;
  role: "user" | "admin";
  status: boolean;
  avatar_url?: string | null;
  tags?: string[];
};
export type Credentials = User & {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};
export type Session = {
  id: string;
  user_id?: number;
  status: string;
  message_count: number;
  last_intent?: string | null;
  title?: string;
  preview?: string;
  updated_at?: string | null;
  created_at?: string | null;
  summary?: string | null;
};
export type Message = {
  id: number;
  session_id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  response_time_ms?: number | null;
  created_at?: string;
  pending?: boolean;
};
export type Priority = "low" | "medium" | "high" | "urgent";
export type TicketStatus =
  "pending" | "processing" | "escalated" | "resolved" | "closed";
export type Ticket = {
  ticket_id: string;
  user_id: number;
  category: string;
  content: string;
  priority: Priority;
  status: TicketStatus;
  assigned_to: string | null;
  session_id?: string | null;
  created_at: string;
  updated_at?: string | null;
  resolution?: string | null;
};
export type TicketInput = {
  user_id: number;
  category: string;
  content: string;
  priority: Priority;
  session_id?: string;
};
export type TicketUpdate = Partial<
  Pick<Ticket, "status" | "assigned_to" | "resolution" | "priority">
>;
export type Handoff = {
  request_id: string;
  user_id: number;
  session_id: string;
  reason: string;
  priority: string;
  context: Record<string, unknown>;
  status: string;
  assigned_to?: string | null;
  resolution?: string | null;
  created_at: string;
  assigned_at?: string | null;
  resolved_at?: string | null;
  sla_deadline?: string | null;
};
export type HumanAgent = {
  agent_id: string;
  name: string;
  skills: string[];
  current_load: number;
  max_load: number;
  status: string;
};
export type Tool = {
  name: string;
  description: string;
  parameters: {
    name: string;
    type: string;
    description: string;
    required: boolean;
  }[];
};
export type KnowledgeDocument = {
  id: string;
  title: string;
  category: string;
  keywords: string[];
  source: string;
  status: "published" | "draft" | "indexing" | "ready" | "failed" | "archived" | "legacy_unverified";
  published_version?: number | null;
  error_code?: string | null;
  version: number;
  created_at?: string;
  updated_at?: string;
  chunks_count: number;
};
export type DocumentInput = {
  expected_version?: number;
  is_published?: boolean;
  title: string;
  content: string;
  category: string;
  keywords: string[];
  source: string;
};
export type WriteResult = {
  success?: boolean;
  message?: string;
  document_id?: string;
  chunks_count?: number;
  status?: string;
};
export type SearchHit = {
  id: string;
  title: string;
  content: string;
  source?: string;
  category?: string;
  final_score?: number;
  score?: number;
};
export type SearchResult = {
  results: SearchHit[];
  backend?: string;
  degraded?: boolean;
  degraded_reason?: string | null;
  query?: string;
  total?: number;
};
export type ChatResponse = {
  message_id: number;
  session_id: string;
  reply: string;
  intent?: string | null;
  response_time_ms: number;
  trace_id?: string | null;
};
export type TraceSpan = {
  span_id: string;
  node: string;
  node_order: number;
  duration_ms: number;
  success: boolean;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
};
export type TraceResult = {
  trace_id: string;
  spans: TraceSpan[];
  span_count: number;
};
export type MetricsSummary = {
  total_requests?: number;
  success_rate?: number;
  avg_response_time_ms?: number;
  p95_response_time_ms?: number;
  intent_distribution?: Record<string, number>;
  tool_statistics?: Record<
    string,
    { total?: number; success?: number; success_rate?: number }
  >;
  session_count?: number;
};
export type Alert = {
  rule_name?: string;
  name?: string;
  message?: string;
  severity?: string;
  timestamp?: string;
};
export type MetricsResult = {
  metrics: Record<string, unknown>;
  alerts: Alert[];
};
export type DependencyHealth = {
  status: string;
  backend?: string;
  reason?: string | null;
};
export type HealthResult = {
  status: string;
  dependencies?: Record<string, DependencyHealth>;
  [key: string]: unknown;
};
