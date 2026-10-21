export type Role = "admin" | "user";

export interface User {
  id: number;
  email: string;
  username: string | null;
  role: Role;
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  user: User;
}

export type DatasetStatus = "pending" | "processing" | "ready" | "failed";

export interface Dataset {
  id: number;
  name: string;
  slug: string;
  kind: string;
  source_filename: string;
  file_type: string;
  parent_upload_id: number | null;
  row_count: number;
  chunk_count: number;
  status: DatasetStatus;
  error: string | null;
  created_at: string;
}

export interface DatasetColumn {
  column_name: string;
  pg_type: string;
  sample_values: unknown[];
  description: string;
}

export interface DatasetDetail extends Dataset {
  columns: DatasetColumn[];
  preview: {
    columns?: string[];
    rows?: unknown[][];
    chunks?: { index: number; content: string; metadata: Record<string, unknown> }[];
  };
}

export type ChartType = "bar" | "line" | "area" | "pie" | "scatter" | "table" | "kpi";

export interface ChartSpec {
  type: ChartType;
  title: string;
  x: string | null;
  y: string[];
  series: string | null;
  data: { columns: string[]; rows: unknown[][] };
  source_sql?: string | null;
}

export interface ResultTable {
  step_id: string;
  title: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
}

export interface Source {
  chunk_id: number;
  source: string;
  page: number | null;
  snippet: string;
}

export interface StepInfo {
  id: string;
  agent: string;
  task: string;
  status: "ok" | "error" | "skipped";
  error: string | null;
  ms: number;
  cache_hit?: boolean;
}

export interface PlanInfo {
  intent: string;
  answerable: boolean;
  reason: string | null;
  steps: { id: string; agent: string; task: string; datasets: string[]; depends_on: string[] }[];
}

export interface QueryResponse {
  answer: string;
  answerable: boolean;
  unverified_numbers: string[];
  charts: ChartSpec[];
  tables: ResultTable[];
  sources: Source[];
  sql: string[];
  plan: PlanInfo | null;
  steps: StepInfo[];
  cache_hit: boolean;
  request_id: string;
  chat_id: number;
  turn_id: number | null;
}

export type AnswerData = Omit<QueryResponse, "chat_id" | "turn_id" | "request_id">;

export interface Pin {
  id: number;
  title: string;
  chart: ChartSpec;
  sql: string | null;
  created_by: number | null;
  created_at: string;
}

export interface AdminUser extends User {
  is_verified: boolean;
  is_active: boolean;
  created_at: string;
}

export interface AuditItem {
  id: number;
  user_email: string | null;
  question: string;
  status: string;
  error: string | null;
  latency_ms: number;
  cache_hit: boolean;
  agents_used: string[];
  sql_executed: string[];
  created_at: string;
}

export interface ChatSummary {
  id: number;
  title: string;
  updated_at: string;
  turn_count: number;
}

export interface ChatList {
  items: ChatSummary[];
  total: number;
}

export interface ChatTurnRecord {
  id: number;
  turn_index: number;
  question: string;
  response: AnswerData | null;
  status: string;
  error: string | null;
  created_at: string;
}

export interface ChatDetail {
  id: number;
  title: string;
  created_at: string;
  turns: ChatTurnRecord[];
}
