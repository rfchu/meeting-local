import type { SearchHit } from "./SearchDialog";
export interface QASource extends SearchHit {
  label: string;
}
export interface QAClaim {
  text: string;
  kind: "record" | "inference" | "note";
  citations: { source: string; quote: string }[];
}
export interface QARun {
  user_query: string;
  mode: string;
  meeting_id: string | null;
  kind: string | null;
  include_personal: boolean;
  id: string;
  created: string;
  question: string;
  query: string;
  profile_id: string;
  provider: {
    name: string;
    provider_type: "local" | "cloud";
    model: string;
    base_url: string;
  };
  sources: QASource[];
  stale: boolean;
  state: string;
  answer: { status: "answered" | "insufficient"; claims: QAClaim[] } | null;
  job: {
    id: string;
    state: string;
    stage: string;
    progress: number;
    error: string;
  } | null;
  pending_index: number;
  candidate_count: number;
  evidence_chars: number;
  context_budget: number;
}
export interface QAPrepare {
  question: string;
  profile_id: string;
  query: string;
  mode: string;
  meeting_id: string | null;
  kind: string | null;
  include_personal: boolean;
}
