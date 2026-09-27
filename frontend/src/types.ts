export type Workspace = "transcript" | "analysis" | "minutes";
export type JobState =
  "queued" | "running" | "completed" | "failed" | "cancelled";
export type MinutesStyle = "formal" | "brief" | "internal" | "ai";

export interface Segment {
  id: string;
  start: number;
  end: number;
  text: string;
  speaker: string;
  boundary_review: boolean;
}

export interface Job {
  id: string;
  meeting_id: string;
  state: JobState;
  stage: string;
  progress: number;
  error: string;
  config: string;
  kind?: "transcription" | "analysis";
}

export interface Revision {
  revision: number;
  created: string;
  source: string;
  segments?: Segment[];
}
export interface AnalysisItem {
  content: string;
  source_segment_ids: string[];
  owner?: string | null;
  due_date?: string | null;
}
export interface MeetingAnalysis {
  summary: AnalysisItem[];
  key_points: AnalysisItem[];
  decisions: AnalysisItem[];
  action_items: AnalysisItem[];
  open_questions: AnalysisItem[];
}

export interface MinutesItem extends AnalysisItem {
  status?: string | null;
}
export interface MeetingMinutes {
  title: string;
  meeting_date: string;
  meeting_form: string;
  location: string;
  attendees: string;
  recorder: string;
  purpose: string;
  style: MinutesStyle;
  basic_info_source_segment_ids: string[];
  overview: MinutesItem[];
  conclusions: MinutesItem[];
  decisions: MinutesItem[];
  action_items: MinutesItem[];
  open_questions: MinutesItem[];
}

export interface Artifact<T = unknown> {
  id: string;
  meeting_id: string;
  revision: number;
  kind: "transcript_candidate" | "meeting_analysis" | "meeting_minutes";
  payload: T;
  provenance: Record<string, unknown>;
  created: string;
  stale: boolean;
}

export interface MeetingSummary {
  id: string;
  title: string;
  filename: string;
  created: string;
  duration: number;
  revision: number;
  job?: Job;
}

export interface Meeting extends MeetingSummary {
  segments: Segment[];
  jobs: Job[];
  revisions: Revision[];
  artifacts: Artifact[];
}

export interface EngineInfo {
  id: "whisper" | "qwen";
  label: string;
  ready: boolean;
}
export interface LLMConfig {
  id: string;
  name: string;
  provider_type: "local" | "cloud";
  base_url: string;
  model: string;
  context_window: number;
  analysis_chunk_mode: "auto" | "quality" | "speed" | "custom";
  analysis_chunk_chars: number;
  analysis_max_chars: number;
  has_api_key: boolean;
  is_default: boolean;
}
export interface Environment {
  engines: EngineInfo[];
  default_engine: string;
  diarization_ready: boolean;
  punctuation_ready: boolean;
  ffmpeg_ready: boolean;
  data_dir: string;
  analysis: {
    ready: boolean;
    models: string[];
    error: string;
    profiles: LLMConfig[];
    default_profile_id?: string | null;
  };
}

export interface LLMConfigInput {
  config_id?: string | null;
  name: string;
  provider_type: "local" | "cloud";
  base_url: string;
  model: string;
  context_window: number;
  analysis_chunk_mode: "auto" | "quality" | "speed" | "custom";
  analysis_chunk_chars: number;
  api_key?: string;
  is_default: boolean;
  clear_api_key?: boolean;
}
