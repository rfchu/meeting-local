export interface OverviewEvidence {
  segment_id: string;
  quote: string;
  start: number;
  range_id?: string;
  segment_index?: number;
}
export interface OverviewItem {
  id: string;
  kind: "topic" | "point" | "question" | "action";
  content: string;
  evidence: OverviewEvidence[];
  note_ids: string[];
  decision: "pending" | "confirmed" | "dismissed";
  review_version: number;
  stale: boolean;
  fingerprint: string;
}
export interface OverviewStatus {
  consent_scope?: string;
  filtered_count?: number;
  failures?: number;
  elapsed_seconds?: number;
  retry_in_seconds?: number;
  timeout_seconds?: number;
  confirmed_until?: number;
  enabled: boolean;
  state: string;
  error: string;
  version: number;
  updated: string | null;
  covered_count: number;
  confirmed_count: number;
  gap_count: number;
  historical: boolean;
  published: boolean;
  items: OverviewItem[];
  provider: {
    id: string;
    name: string;
    provider_type: "local" | "cloud";
    base_url: string;
    model: string;
    engine: "qwen" | "whisper";
  } | null;
  snapshot_provider?: OverviewStatus["provider"];
  history: { version: number; cursor: number; created: string }[];
}
