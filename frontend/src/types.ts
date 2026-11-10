export type SourceTrack = "system" | "microphone";
export type SourceLanguage = "auto" | "zh" | "en" | "ja";
export type TranscriptionLanguage = SourceLanguage;
export type OutputLanguage = "zh" | "en";
export type Workspace =
  "prepare" | "transcript" | "notes" | "analysis" | "minutes" | "copilot";
export type JobState =
  "queued" | "running" | "completed" | "failed" | "cancelled";
export type MinutesStyle = "formal" | "brief" | "internal" | "ai";

export interface Segment {
  source_track?: SourceTrack | null;
  continuation_id?: string;
  continues_previous?: boolean;
  continues_next?: boolean;
  boundary_reason?: string;

  id: string;
  start: number;
  end: number;
  text: string;
  speaker: string;
  boundary_review: boolean;
  timestamp_precision?: "chunk" | "aligned" | null;
  alignment_warning?: string;
  speaker_locked?: boolean;
}

export interface Job {
  recorded_retries?: number;
  id: string;
  meeting_id: string;
  state: JobState;
  stage: string;
  progress: number;
  error: string;
  config: string;
  kind?:
    "transcription" | "analysis" | "note_expansion" | "minutes" | "terminology";
}

export interface Revision {
  revision: number;
  created: string;
  source: string;
  segments?: Segment[];
}
export type MeetingNoteType =
  "note" | "highlight" | "question" | "decision" | "action";
export interface InterviewSpeakerRoles {
  revision: number;
  candidate: string;
  interviewers: string[];
}
export interface InterviewPreparation {
  speaker_roles?: InterviewSpeakerRoles | null;
  candidate: string;
  role: string;
  round: string;
  interviewers: string;
  focus: string;
  questions: string;
  job_description: string;
}
export interface InterviewEvidence {
  span_segment_ids?: string[];
  segment_id: string;
  quote: string;
}
export interface InterviewItem {
  content: string;
  evidence: InterviewEvidence[];
}
export interface InterviewResult {
  background: InterviewItem[];
  qa: (InterviewItem & {
    question: string;
    question_evidence?: InterviewEvidence[];
  })[];
  competencies: (InterviewItem & {
    dimension: string;
    coverage: "covered" | "needs_examples" | "not_discussed" | "unclear";
  })[];
  concerns: (InterviewItem & { reason: string; followup: string })[];
  followups: (InterviewItem & {
    owner: string | null;
    due_date: string | null;
  })[];
  summary: InterviewItem[];
}
export interface MeetingBrief {
  source_language?: SourceLanguage | null;
  output_language?: OutputLanguage | null;
  interview?: InterviewPreparation | null;
  title: string;
  purpose: string;
  agenda: string;
  participants: string;
  questions: string;
  previous_meeting_id: string | null;
}
export interface AttentionDisposition {
  decision: "meeting" | "personal" | "deferred";
  content: string;
  source_segment_ids: string[];
  stale: boolean;
  review_key: string;
}
export interface MeetingFlow {
  brief: MeetingBrief;
  counts: Record<string, number>;
  attention: {
    note_id: string;
    state: string;
    adopted: boolean;
    related: boolean;
    review: AttentionDisposition | null;
  }[];
  has_current_minutes: boolean;
  previous: { id: string; title: string; todos: NoteTodo[] } | null;
}
export interface MeetingNoteInput {
  client_id?: string;
  overview_focus?: boolean;
  note_scope?: "personal" | "meeting";
  is_marker?: boolean;
  captured_at: number;
  note_type: MeetingNoteType;
  raw_text: string;
}
export interface SpeculativeCandidate {
  candidate_id: string;
  kind: "todo" | "decision";
  title: string;
  content: string;
  owner?: string;
  due_date?: string;
  confidence: number;
  speaker?: string;
  timestamp: number;
  anchor_segments?: string[];
}

export interface MeetingNote extends MeetingNoteInput {
  id: string;
  meeting_id: string;
  adopted_to_minutes?: boolean;
  expansion_revision: number;
  expansion_stale: boolean;
  expansion?: Artifact<NoteExpansion> | null;
  expanded_text: string;
  accepted_text: string;
  synthesized_text?: string;
  shorthand_quote?: string;
  shorthand_track?: SourceTrack | "";
  anchor_start?: number;
  anchor_end?: number;
  speaker_hint?: string;
  status:
    | "raw"
    | "expanded"
    | "accepted"
    | "ignored"
    | "synthesizing"
    | "ready"
    | "failed";
  source_segment_ids: string[];
  created: string;
  updated: string;
}
export interface NoteExpansion {
  note_id: string;
  raw_text: string;
  note_type: MeetingNoteType;
  captured_at: number;
  status: "supported" | "insufficient";
  text: string;
  reason: string;
  evidence: { segment_id: string; quote: string }[];
}
export interface MinutesDelivery {
  sent_status?: "sent" | "unmarked" | "unknown";
  template_version?: number;
  sent_at?: string | null;
  audience?: "internal" | "external";
  id: string;
  artifact_id: string;
  created: string;
  title: string;
  revision: number;
  include_citations: boolean;
  task_status: {
    action_index: number;
    task_id: string;
    content: string;
    status: string;
    owner: string;
    due_date: string;
  }[];
}
export interface NoteTodo {
  source_segment_ids?: string[];
  meeting_id?: string;
  carried?: boolean;
  source_meeting_title?: string;
  source_kind?: "note" | "minutes";
  source_artifact_id?: string;
  source_index?: number;
  id: string;
  note_id: string;
  content: string;
  status: "pending" | "done";
  owner: string;
  due_date: string;
  note_scope: string;
  revision: number;
  stale: boolean;
}
export interface NoteReview {
  fingerprint: string;
  revision: number;
  segments: Segment[];
}
export interface LiveCorrection {
  id: string;
  range_id: string;
  segment_index: number;
  profile_id: string;
  model: string;
  original_text: string;
  edits: { original: string; replacement: string; reason: string }[];
  state: "suggested" | "accepted" | "rejected" | "unchanged";
}
export type CopilotNote = Pick<
  MeetingNote,
  | "id"
  | "raw_text"
  | "captured_at"
  | "note_type"
  | "status"
  | "synthesized_text"
  | "anchor_start"
  | "anchor_end"
  | "speaker_hint"
  | "shorthand_quote"
  | "shorthand_track"
> & { owner?: string; due_date?: string };

export interface LiveDraftSegment {
  possible_echo?: boolean;
  tracks?: LiveDraftSegment[];
  source_track?: SourceTrack | null;
  continuation_id?: string;
  continues_previous?: boolean;
  continues_next?: boolean;
  boundary_reason?: string;

  stable_text?: string;
  live_range_id?: string;
  live_segment_index?: number;
  asr_original_text?: string;
  live_correction_id?: string;
  start: number;
  end: number;
  text: string;
}
export interface RecordingSession {
  source_language?: SourceLanguage;
  capture_transport?: string;
  include_microphone?: boolean;
  microphone_only?: boolean;
  brief?: MeetingBrief;
  id: string;
  mime_type: string;
  created: number;
  updated: number;
  duration: number;
  notes: MeetingNoteInput[];
  chunk_count: number;
  saved_chunks?: number;
  state: string;
  source?: "macos";
  native_audio_saved?: boolean;
  draft_engine?: "whisper" | "qwen";
  health?: RecordingHealth;
}
export interface RecordingHealth {
  runtime?: RecordingRuntime;
  lifecycle?: { available: boolean; events: LifecycleEvent[] };
  empty_session?: boolean;
  recovery_archived?: boolean;
  capture_failure_code?:
    "permission_required" | "capture_failed" | "storage_failed" | "";
  schema_version: number;
  source: "browser" | "native";
  state: string;
  duration_seconds: number;
  native_running: boolean;
  metadata_age_seconds: number;
  audio: {
    saved_bytes: number;
    saved_chunks: number;
    expected_chunks: number;
    missing_chunk_count: number;
    missing_chunk_indices: number[];
    contiguous_chunks: number;
    tracks: { name: string; saved_bytes: number; saved_seconds: number }[];
    recoverable: boolean;
  };
  notes_count: number;
  asr: Record<
    string,
    {
      covered_seconds: number;
      failed_attempts: number;
      running_attempts: number;
    }
  >;
  warning_codes: string[];
}
export interface LifecycleEvent {
  seq: number;
  at: number;
  code: string;
  metrics: Record<string, number | boolean | null>;
}
export interface RecordingRuntime {
  control?: {
    version: number;
    status: "idle" | "unconfirmed";
    pending_command: string | null;
  };
  schema_version?: number;
  recording_id?: string;
  source?: "native" | "browser";
  identity?: {
    recording_id: string;
    live_session_id: string | null;
    meeting_id: string | null;
  };
  ownership?: {
    capture: "native_service" | "browser_page";
    storage: "local_service";
    recognition: "local_service";
  };
  snapshot?: { scope_id: string; sequence: number };
  phase: string;
  observed_at: number;
  capture: {
    native_diagnostics?: NativeCaptureDiagnostics;
    stop_confirmed?: boolean;
    state: string;
    heartbeat_age_seconds: number | null;
    tracks?: {
      name: string;
      state: string;
      saved_seconds: number;
      reported_seconds?: number;
      file_state?: string;
      file_age_seconds?: number | null;
    }[];
  };
  storage: {
    state: string;
    saved_bytes: number;
    last_saved_seconds: number | null;
    saved_chunks: number;
    missing_chunks: number;
  };
  asr: {
    state: string;
    engine: string | null;
    covered_seconds: number;
    gap_count: number;
  };
}
export interface CaptureOwnership {
  protocol_version: number;
  lease: "free" | "held" | "unknown";
  decision: string;
  reattach_supported: boolean;
  controller_generation?: number;
  can_recover_control?: boolean;
  control_required?: boolean;
}
export interface NativeCapability {
  ownership_protocol_version?: number;
  ownership?: CaptureOwnership;
  available: boolean;
  supported: boolean;
  screen_permission: boolean;
  microphone_only_supported?: boolean;
  microphone_permission: boolean;
  build_command: string;
  diagnostics_version?: number;
  helper_build?: number;
  error?: string;
}
export interface NativeCaptureStatus {
  ownership?: CaptureOwnership;
  diagnostics?: NativeCaptureDiagnostics;
  state: string;
  duration: number;
  error: string;
  health?: {
    stalled: boolean;
    heartbeat_age_seconds?: number | null;
    tracks: {
      name: "system" | "microphone";
      state:
        | "paused"
        | "inactive"
        | "stalled"
        | "no_data"
        | "quiet"
        | "receiving"
        | "waiting";
      saved_seconds: number;
      reported_seconds?: number;
      file_state?: string;
      file_age_seconds?: number | null;
    }[];
  };
  system_frames?: number;
  microphone_frames?: number;
  system_levels?: number[];
  microphone_levels?: number[];
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
export type MinutesSectionSource =
  | "overview"
  | "conclusions"
  | "decisions"
  | "action_items"
  | "open_questions"
  | "summary";
export interface MinutesTemplateSection {
  id: string;
  title: string;
  source: MinutesSectionSource;
  kind: "list" | "decision_table" | "action_table";
  required: boolean;
  evidence_required: boolean;
  max_items: number;
}
export interface MinutesTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  version: number;
  builtin: boolean;
  generation_instruction: string;
  sections: MinutesTemplateSection[];
}
export interface MeetingMinutes {
  output_language?: OutputLanguage;
  interview?: InterviewResult | null;
  interview_context?: InterviewPreparation | null;
  interview_reviews?: Record<string, "kept" | "ignored">;
  interviewer_evaluation?: string;
  interview_evaluation_needs_review?: boolean;
  title: string;
  meeting_date: string;
  meeting_form: string;
  location: string;
  attendees: string;
  recorder: string;
  purpose: string;
  style: MinutesStyle;
  template_id: string;
  template_version: number;
  template_snapshot?: MinutesTemplate | null;
  basic_info_source_segment_ids: string[];
  overview: MinutesItem[];
  conclusions: MinutesItem[];
  decisions: MinutesItem[];
  action_items: MinutesItem[];
  open_questions: MinutesItem[];
  summary: MinutesItem[];
}

export interface Artifact<T = unknown> {
  id: string;
  meeting_id: string;
  revision: number;
  kind:
    | "transcript_candidate"
    | "meeting_analysis"
    | "meeting_minutes"
    | "speaker_review"
    | "note_expansion";
  payload: T;
  provenance: Record<string, unknown>;
  created: string;
  stale: boolean;
}
export interface SpeakerReviewResult {
  suggestions: {
    segment_id: string;
    original_speaker: string;
    proposed_speaker: string;
    reason: string;
    evidence_ids: string[];
    strength: "supported" | "review" | "conflict";
  }[];
  chunks: number;
}

export interface MeetingOrganization {
  group_name: string;
  group_kind: "project" | "customer" | "series";
  archived: number;
}

export interface MeetingSummary {
  library?: MeetingOrganization | null;
  id: string;
  title: string;
  filename: string;
  created: string;
  duration: number;
  revision: number;
  job?: Job;
}

export interface MinutesGenerationStatus {
  message?: string;
  id: string;
  state: "running" | "waiting" | "completed" | "failed" | "cancelled";
  revision: number;
  elapsed_seconds: number;
  error: string;
  artifact_id?: string;
}
export interface TaskFocus {
  meetingId: string;
  jobId: string;
}
export interface MeetingHandoff {
  version: 1;
  source_version: string;
  revision: number;
  title: string;
  review_required: number;
  stages: {
    key: string;
    label: string;
    state: "ready" | "review" | "stale" | "processing" | "optional" | "missing";
    summary: string;
    counts: Record<string, number>;
    source: {
      artifact_id?: string | null;
      revision?: number | null;
      reason_codes?: string[];
      [key: string]: unknown;
    };
  }[];
  primary: {
    job_id?: string;
    code: string;
    target:
      | "transcribe"
      | "transcript"
      | "speakers"
      | "notes"
      | "overview"
      | "minutes"
      | "actions"
      | "preview"
      | "tasks"
      | "recording";
    label: string;
    summary: string;
  };
  edges: {
    from: string;
    to: string;
    relation:
      | "reused_audio_ranges"
      | "context"
      | "explicit_adoption"
      | "confirmed_tracking";
    count: number;
  }[];
}
export interface Meeting extends MeetingSummary {
  handoff?: MeetingHandoff;
  live_overview_available?: boolean;
  active_minutes_id?: string | null;
  action_tasks?: Record<string, Record<string, string>>;
  deliveries?: MinutesDelivery[];
  minutes_generation?: MinutesGenerationStatus | null;
  flow?: MeetingFlow;
  review?: MeetingReview;
  recording_quality?: Omit<RecordingQuality, "items">;
  segments: Segment[];
  jobs: Job[];
  revisions: Revision[];
  artifacts: Artifact[];
  notes: MeetingNote[];
  note_todos: NoteTodo[];
  audio_tracks?: string[];
}
export type ReviewStep = "transcript" | "speakers" | "notes" | "minutes";
export interface MeetingReview {
  revision: number;
  title: string;
  minutes_id: string | null;
  notes_count: number;
  speaker_count: number;
  has_current_minutes: boolean;
  steps: Record<
    ReviewStep,
    {
      state: "pending" | "done" | "stale" | "blocked" | "not_needed";
      fingerprint: string;
      reviewed_at: string | null;
      decision: "confirmed" | "skipped" | null;
    }
  >;
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
  auto_minutes_ready?: boolean;
  qa_ready?: boolean;
  meeting_flow_ready?: boolean;
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

export type RecordingReviewStatus = "pending" | "clear" | "gap" | "uncertain";
export interface RecordingQuality {
  recorded: boolean;
  available: boolean;
  monitored: boolean;
  total_count: number;
  retained_count?: number;
  reviewed_count?: number;
  gap_count?: number;
  pending_count?: number;
  offset?: number;
  pending_only?: boolean;
  items: {
    id: number;
    component: string;
    state: string;
    observed_at: number;
    last_observed_at: number;
    closed_at: number | null;
    start_seconds: number | null;
    end_seconds: number | null;
    end_state: string | null;
    review_status?: RecordingReviewStatus;
    review_version?: number;
    reviewed_at?: number | null;
  }[];
}

export interface NativeCaptureDiagnostics {
  available: boolean;
  version?: number;
  helper_build?: number | null;
  counters_complete?: boolean;
  default_input_changes?: number | null;
  default_output_changes?: number | null;
  audio_device_connections?: number | null;
  audio_device_disconnections?: number | null;
  listener_failures?: number | null;
  device_query_failures?: number | null;
  configured_microphone_is_default?: boolean | null;
  failure_stage?:
    | "none"
    | "startup"
    | "capture_stream"
    | "audio_conversion"
    | "storage"
    | "timeline_limit"
    | "unknown";
  stream_error_code?: number | null;
  tracks?: Record<
    "system" | "microphone",
    Record<
      | "input_buffers"
      | "empty_outputs"
      | "dropped_buffers"
      | "format_changes"
      | "gap_frames",
      number | null
    >
  >;
}
