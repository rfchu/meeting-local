export type ManagementPage =
  "overview" | "models" | "defaults" | "environment" | "tasks" | "index";
export interface RuntimeOverview {
  checked_at: number;
  passive: boolean;
  issues: string[];
  engines: { id: string; label: string; ready: boolean; model: string }[];
  native: {
    available?: boolean;
    supported?: boolean;
    screen_permission?: boolean;
    microphone_permission?: boolean;
  };
  capture_test_active: boolean;
  qwen: {
    loaded: boolean;
    loading: boolean;
    busy: boolean;
    peak_memory_gb: number;
  };
  dependencies: { ffmpeg: boolean; diarization: boolean; punctuation: boolean };
  storage: {
    data_dir: string;
    writable: boolean;
    free_bytes: number | null;
    total_bytes: number | null;
  };
  llm: {
    connection_checked: boolean;
    profiles: {
      id: string;
      name: string;
      model: string;
      provider_type: string;
      is_default: number;
    }[];
  };
  sessions: {
    recording_id: string;
    engine: string;
    source: string;
    status: string;
    confirmed_until: number;
  }[];
  jobs: {
    id: string;
    kind: string;
    state: string;
    stage: string | null;
    progress: number;
  }[];
  job_counts: Record<string, number>;
}
