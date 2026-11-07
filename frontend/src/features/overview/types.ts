import type { SourceTrack } from "../../types";

export interface OverviewEvidence {
  source_track?: SourceTrack | null;
  segment_id: string;
  quote: string;
  start: number;
  range_id?: string;
  segment_index?: number;
  stale?: boolean;
}
export interface OverviewItem {
  id: string;
  topic_ref?: string;
  topic_start?: number;
  previous_decision?: { content: string; evidence: OverviewEvidence[] };
  kind: "topic" | "point" | "decision" | "question" | "action";
  content: string;
  evidence: OverviewEvidence[];
  note_ids: string[];
  decision: "pending" | "confirmed" | "dismissed";
  review_version: number;
  stale: boolean;
  stale_reasons?: ("source" | "note")[];
  review_trace?: {
    state: OverviewItem["decision"];
    updated: string;
    version: number;
    snapshot_version?: number | null;
  };
  fingerprint: string;
}
export interface OverviewTerm {
  original: string;
  name: string;
  category: string;
  evidence: OverviewEvidence;
}
export interface OverviewStatus {
  agenda?: OverviewItem[];
  agenda_terms?: OverviewTerm[];
  covered_until?: number | null;
  consent_scope?: string;
  filtered_count?: number;
  quality?: { duplicates_removed?: number; overflow_count?: number };
  failures?: number;
  elapsed_seconds?: number;
  retry_in_seconds?: number;
  timeout_seconds?: number;
  confirmed_until?: number;
  enabled: boolean;
  state: string;
  error: string;
  version: number;
  latest_version?: number;
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
  topics?: {
    version: number;
    id: string;
    title: string;
    start: number | null;
  }[];
  history: { version: number; cursor: number; created: string }[];
}
