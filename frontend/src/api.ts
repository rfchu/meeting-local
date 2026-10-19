import type { NoteReview } from "./types";
import type { SearchHit, SearchStatus } from "./features/SearchDialog";
import {
  readLiveStream,
  type LivePartial,
  type LiveStreamResult,
} from "./features/notes/liveStream";
import type {
  Artifact,
  Environment,
  LLMConfig,
  LLMConfigInput,
  LiveDraftSegment,
  Meeting,
  MeetingMinutes,
  MeetingNoteInput,
  MinutesTemplate,
  MeetingSummary,
  Revision,
  SpeakerReviewResult,
  RecordingSession,
  RecordingHealth,
  MeetingReview,
  ReviewStep,
  NativeCapability,
  NativeCaptureStatus,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {
      /* retain status text */
    }
    throw new Error(detail);
  }
  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  searchStatus: () => request<SearchStatus>("/retrieval"),
  configureSearch: (profileId: string, model: string) =>
    request("/retrieval/config", json("PUT", { profile_id: profileId, model })),
  buildSearch: (force: boolean) =>
    request("/retrieval/build", json("POST", { force })),
  searchLibrary: (body: {
    query: string;
    mode: string;
    meeting_id: string | null;
    kind: string | null;
  }) =>
    request<{ results: SearchHit[]; pending: number }>(
      "/retrieval/search",
      json("POST", body),
    ),
  resolveSearch: (hit: SearchHit) =>
    request<SearchHit>(
      "/retrieval/resolve",
      json("POST", { id: hit.id, fingerprint: hit.fingerprint }),
    ),
  environment: () => request<Environment>("/status"),
  meetings: () => request<MeetingSummary[]>("/meetings"),
  recordingKeepAwake: (id: string, enabled: boolean) =>
    request<{ active: boolean; supported: boolean; error: string }>(
      `/recordings/${id}/keep-awake`,
      { ...json("POST", { enabled }), signal: AbortSignal.timeout(10000) },
    ),
  recordings: () =>
    request<RecordingSession[]>("/recordings", {
      signal: AbortSignal.timeout(10000),
    }),
  recordingHealth: (id: string) =>
    request<RecordingHealth>(`/recordings/${id}/health`, {
      signal: AbortSignal.timeout(10000),
    }),
  recordingDiagnostics: (id: string) =>
    request<RecordingHealth>(`/recordings/${id}/diagnostics`, {
      signal: AbortSignal.timeout(10000),
    }),
  liveModelStatus: () =>
    request<{
      loaded: boolean;
      loading: boolean;
      busy: boolean;
      model: string;
      load_count: number;
      requests: number;
      idle_seconds: number;
      idle_timeout_seconds: number;
      peak_memory_gb: number;
      load_ms: number;
      formal_job_running: boolean;
    }>("/live-model"),
  liveModelControl: (action: "warm" | "release") =>
    request("/live-model/" + action, { method: "POST" }),
  nativeCapability: () => request<NativeCapability>("/native-capture"),
  nativeStart: (microphone: boolean, keepAwake = true) =>
    request<RecordingSession>(
      "/native-capture",
      json("POST", { microphone, keep_awake: keepAwake }),
    ),
  nativeStatus: (id: string) =>
    request<NativeCaptureStatus>(`/native-capture/${id}`, {
      signal: AbortSignal.timeout(10000),
    }),
  nativeDraft: (
    id: string,
    offset: number,
    engine: "whisper" | "qwen" = "whisper",
  ) =>
    request<{
      ready: boolean;
      next_offset: number;
      skipped_seconds: number;
      segments: LiveDraftSegment[];
      processing_ms: number;
    }>(`/native-capture/${id}/draft`, json("POST", { offset, engine })),
  nativeDraftStream: (
    id: string,
    offset: number,
    onPartial: (partial: LivePartial) => void,
    signal: AbortSignal,
    responsive = false,
    windowSeconds: 5 | 10 | 15 = 15,
  ) =>
    readLiveStream<
      LiveStreamResult & {
        ready: boolean;
        next_offset: number;
        skipped_seconds: number;
      }
    >(
      `/native-capture/${id}/draft/stream`,
      {
        ...json("POST", {
          offset,
          engine: "qwen",
          responsive,
          window_seconds: windowSeconds,
        }),
        signal,
      },
      onPartial,
    ),
  nativeCommand: (id: string, command: "pause" | "resume" | "stop") =>
    request<NativeCaptureStatus>(
      `/native-capture/${id}/command`,
      json("POST", { command }),
    ),
  nativeFinish: (id: string) =>
    request<Meeting>(`/native-capture/${id}/finish`, { method: "POST" }),
  liveCoverage: (id: string, engine: "whisper" | "qwen") =>
    request<{
      covered_seconds: number;
      duration: number;
      completed_ranges: number;
      gaps: { start: number; end: number }[];
      attempts: Record<string, number>;
      recent_failures: {
        start: number;
        end: number;
        state: string;
        error: string;
      }[];
    }>(`/recordings/${id}/live-results?engine=${engine}`),
  createRecording: (mimeType: string) =>
    request<RecordingSession>(
      "/recordings",
      json("POST", { mime_type: mimeType }),
    ),
  recordingMetadata: (session: RecordingSession) =>
    request<RecordingSession>(`/recordings/${session.id}/metadata`, {
      ...json("PUT", {
        duration: session.duration,
        notes: session.notes,
        chunk_count: session.chunk_count,
      }),
      signal: AbortSignal.timeout(10000),
    }),
  recordingChunk: (id: string, index: number, blob: Blob) => {
    const data = new FormData();
    data.append("file", blob, "chunk.part");
    return request(`/recordings/${id}/chunks/${index}`, {
      method: "PUT",
      body: data,
      signal: AbortSignal.timeout(60000),
    });
  },
  finishRecording: (id: string, count: number) =>
    request<Meeting>(
      `/recordings/${id}/finish`,
      json("POST", { chunk_count: count }),
    ),
  meeting: (id: string) => request<Meeting>(`/meetings/${id}`),
  confirmReview: (
    id: string,
    step: ReviewStep,
    fingerprint: string,
    decision: "confirmed" | "skipped",
  ) =>
    request<MeetingReview>(
      `/meetings/${id}/review/${step}`,
      json("PUT", { fingerprint, decision }),
    ),
  resetReview: (id: string, step: ReviewStep) =>
    request<MeetingReview>(`/meetings/${id}/review/${step}`, {
      method: "DELETE",
    }),
  upload: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<Meeting>("/meetings", { method: "POST", body: data });
  },
  liveTranscribeStream: (
    file: File,
    offset: number,
    recordingId: string | undefined,
    onPartial: (partial: LivePartial) => void,
    signal: AbortSignal,
    confirmed = true,
  ) => {
    const data = new FormData();
    data.append("file", file);
    data.append("offset", String(offset));
    data.append("confirmed", String(confirmed));
    if (recordingId) data.append("recording_id", recordingId);
    return readLiveStream<LiveStreamResult>(
      "/live-transcribe/stream",
      { method: "POST", body: data, signal },
      onPartial,
    );
  },
  liveTranscribe: (
    file: File,
    offset: number,
    engine: "whisper" | "qwen" = "whisper",
    recordingId?: string,
  ) => {
    const data = new FormData();
    data.append("file", file);
    data.append("offset", String(offset));
    data.append("engine", engine);
    if (recordingId) data.append("recording_id", recordingId);
    return request<{
      segments: LiveDraftSegment[];
      duration: number;
      processing_ms: number;
    }>("/live-transcribe", { method: "POST", body: data });
  },
  rename: (id: string, title: string) =>
    request(`/meetings/${id}`, json("PATCH", { title })),
  speakerReview: (id: string, profileId?: string) =>
    request<Artifact<SpeakerReviewResult>>(
      `/meetings/${id}/speaker-reviews`,
      json("POST", { profile_id: profileId }),
    ),
  applySpeakerReview: (
    id: string,
    reviewId: string,
    revision: number,
    segmentIds: string[],
  ) =>
    request<{ revision: number }>(
      `/meetings/${id}/speaker-reviews/${reviewId}/apply`,
      json("POST", { revision, segment_ids: segmentIds }),
    ),
  remove: (id: string) => request(`/meetings/${id}`, { method: "DELETE" }),
  transcribe: (
    id: string,
    body: {
      engine: string;
      diarization: boolean;
      num_speakers: number;
      completion_mode?: "quick" | "full";
    },
  ) => request<{ id: string }>(`/meetings/${id}/jobs`, json("POST", body)),
  jobAction: (id: string, action: "cancel" | "retry") =>
    request(`/jobs/${id}/${action}`, { method: "POST" }),
  saveTranscript: (id: string, revision: number, segments: unknown[]) =>
    request<{ revision: number }>(
      `/meetings/${id}/transcript`,
      json("PUT", { revision, segments }),
    ),
  revision: (id: string, revision: number) =>
    request<Revision>(`/meetings/${id}/revisions/${revision}`),
  createNotes: (id: string, notes: MeetingNoteInput[]) =>
    request<{ ids: string[]; count: number }>(
      `/meetings/${id}/notes/batch`,
      json("POST", { notes }),
    ),
  createNote: (id: string, note: MeetingNoteInput) =>
    request(`/meetings/${id}/notes`, json("POST", note)),
  updateNote: (
    id: string,
    noteId: string,
    note: Pick<
      MeetingNoteInput,
      "note_type" | "raw_text" | "note_scope" | "is_marker" | "captured_at"
    >,
  ) => request(`/meetings/${id}/notes/${noteId}`, json("PUT", note)),
  reviewNote: (id: string, noteId: string) =>
    request<NoteReview>(`/meetings/${id}/notes/${noteId}/review`),
  useNote: (
    id: string,
    noteId: string,
    body: {
      kind: "minutes" | "todo";
      fingerprint: string;
      revision: number;
      content: string;
      source_segment_ids: string[];
      section: string;
      minutes_id: string | null;
      confirmed: boolean;
    },
  ) => request(`/meetings/${id}/notes/${noteId}/use`, json("POST", body)),
  updateNoteTodo: (
    id: string,
    todoId: string,
    body: { status: "pending" | "done"; owner: string; due_date: string },
  ) => request(`/meetings/${id}/note-todos/${todoId}`, json("PUT", body)),
  expandNote: (id: string, noteId: string, profileId?: string) =>
    request<{ id: string }>(
      `/meetings/${id}/notes/${noteId}/expand`,
      json("POST", { profile_id: profileId || null }),
    ),
  acceptNoteExpansion: (
    id: string,
    noteId: string,
    artifactId: string,
    acceptedText?: string,
  ) =>
    request(
      `/meetings/${id}/notes/${noteId}/accept-expansion`,
      json("POST", { artifact_id: artifactId, accepted_text: acceptedText }),
    ),
  deleteNote: (id: string, noteId: string) =>
    request(`/meetings/${id}/notes/${noteId}`, { method: "DELETE" }),
  analyze: (id: string, body: { profile_id?: string; model?: string }) =>
    request<{ id: string }>(
      `/meetings/${id}/analysis-jobs`,
      json("POST", body),
    ),
  minutesTemplates: () => request<MinutesTemplate[]>("/minutes-templates"),
  generateMinutes: (id: string, style: string, templateId: string) =>
    request<Artifact<MeetingMinutes>>(
      `/meetings/${id}/minutes`,
      json("POST", { style, template_id: templateId }),
    ),
  generateAiMinutes: (
    id: string,
    body: {
      profile_id?: string;
      model?: string;
      mode: "quick" | "full";
      template_id: string;
    },
  ) =>
    request<Artifact<MeetingMinutes>>(
      `/meetings/${id}/minutes/ai`,
      json("POST", body),
    ),
  saveMinutes: (
    id: string,
    sourceAnalysisId: string | null,
    document: MeetingMinutes,
  ) =>
    request<Artifact<MeetingMinutes>>(
      `/meetings/${id}/minutes`,
      json("PUT", { source_analysis_id: sourceAnalysisId, document }),
    ),
  configs: () => request<LLMConfig[]>("/llm-configs"),
  saveConfig: (body: LLMConfigInput) =>
    body.config_id
      ? request<LLMConfig>(`/llm-configs/${body.config_id}`, json("PUT", body))
      : request<LLMConfig>("/llm-configs", json("POST", body)),
  testConfig: (body: LLMConfigInput) =>
    request<{
      ok: boolean;
      models: string[];
      model_available: boolean;
      detected_context_window: number | null;
      effective_context_window: number;
      analysis_max_chars: number;
    }>("/llm-configs/test", json("POST", body)),
  deleteConfig: (id: string) =>
    request(`/llm-configs/${id}`, { method: "DELETE" }),
};

export const downloadUrl = (path: string) => `/api${path}`;
export const download = (path: string) => {
  const link = document.createElement("a");
  link.href = path.startsWith("/api/") ? path : downloadUrl(path);
  link.download = "";
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
};
