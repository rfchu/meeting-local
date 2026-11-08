import type { ChineseResult } from "./features/notes/ChineseRecordLine";
import type { DeliveryReadiness } from "./features/minutes/deliveryReadiness";
import type {
  HandoffSources,
  SourceDecision,
} from "./features/meeting/handoffSources";
import { taskQuery } from "./features/management/taskScope";
import type { ResourceStatus } from "./features/management/ResourceStatusPanel";
import { reportRecordingCommand } from "./features/notes/recordingCommandEvents";
import { RecordingCommandClient } from "./features/notes/recordingCommands";
import type { ObservationTimelineData } from "./features/management/ObservationTimeline";
import type { RecordingObservation } from "./features/management/RecordingValidationPanel";
import type {
  RecordingQuality,
  RecordingReviewStatus,
  SourceLanguage,
  TranscriptionLanguage,
} from "./types";
import type { RuntimeTasks } from "./features/management/TasksPanel";
import type { RuntimeOverview } from "./features/management/RuntimeManagement";
import type { OverviewStatus } from "./features/overview/types";
import type { Readiness } from "./features/onboarding/readiness";
import type { CaptureTest } from "./features/onboarding/AudioCheck";
import type { SampleMeeting } from "./features/onboarding/SampleMeetingDialog";
import type { Term, TermList, TermInput } from "./features/terminology/terms";
import type {
  SessionConfig,
  SessionSnapshot,
} from "./features/notes/liveSession";
import type { MeetingBrief, MeetingFlow, NoteTodo, NoteReview } from "./types";
import type { SearchHit, SearchStatus } from "./features/SearchDialog";
import type { QARun, QAPrepare } from "./features/qaTypes";
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
  LiveCorrection,
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

export class APIError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message);
  }
}
async function checkedResponse(path: string, init?: RequestInit) {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    let code: string | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (
        body.detail &&
        typeof body.detail === "object" &&
        "message" in body.detail
      ) {
        const structured = body.detail as { message: string; code?: string };
        detail = structured.message;
        code = structured.code;
      } else
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
    } catch {
      /* retain status text */
    }
    throw new APIError(detail, response.status, code);
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await checkedResponse(path, init);
  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

function commandFailure(id: string, error: unknown) {
  const code = error instanceof APIError ? error.code : undefined;
  reportRecordingCommand(
    id,
    code === "command_unconfirmed"
      ? "unconfirmed"
      : [
            "version_conflict",
            "request_reused",
            "command_superseded",
            "terminal_state",
            "invalid_state",
          ].includes(code || "")
        ? "conflict"
        : "unavailable",
  );
}
const recordingCommands = new RecordingCommandClient(
  async (id) => {
    try {
      const health = await request<RecordingHealth>(
        `/recordings/${id}/health`,
        {
          signal: AbortSignal.timeout(10000),
        },
      );
      const version = health.runtime?.control?.version;
      if (version == null)
        throw new Error("本机服务尚未提供录音控制版本，请刷新或更新服务后重试");
      return version;
    } catch (error) {
      commandFailure(id, error);
      throw error;
    }
  },
  async (id, body) => {
    try {
      const result = await request<
        import("./features/notes/recordingCommands").CommandReply
      >(`/recordings/${id}/commands`, {
        ...json("POST", body),
        signal: AbortSignal.timeout(30000),
      });
      reportRecordingCommand(id);
      return result;
    } catch (error) {
      commandFailure(id, error);
      throw error;
    }
  },
  (error) =>
    error instanceof APIError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 408 &&
    error.code !== "command_unconfirmed",
);

type AudiencePreviewInput = {
  artifact_id: string;
  audience: "internal" | "external";
  selection: string[];
  include_citations: boolean;
  template_version?: number;
};

export const api = {
  handoffSources: (mid: string, artifactId?: string, offset = 0) =>
    request<HandoffSources>(
      `/meetings/${mid}/handoff/sources?${new URLSearchParams({ offset: String(offset), ...(artifactId ? { artifact_id: artifactId } : {}) })}`,
    ),
  reviewHandoffSource: (
    mid: string,
    body: {
      artifact_id: string;
      source_id: string;
      fingerprint: string;
      decision: SourceDecision;
      request_id: string;
      confirmed: boolean;
    },
  ) =>
    request<{ id: string; reused: boolean; decision: SourceDecision }>(
      `/meetings/${mid}/handoff/source-reviews`,
      json("POST", body),
    ),
  runtimeRecordings: (signal?: AbortSignal) =>
    request<{
      items: {
        recording_id: string;
        source: "native" | "browser";
        created: number;
        available: boolean;
        runtime: import("./types").RecordingRuntime | null;
      }[];
      total_count: number;
      limit: number;
      truncated: boolean;
    }>("/runtime/recordings", {
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(10000)])
        : AbortSignal.timeout(10000),
    }),
  runtimeTasks: (state: string, focus?: import("./types").TaskFocus) =>
    request<RuntimeTasks>(taskQuery(state, focus)),
  runtimeResources: () => request<ResourceStatus>("/runtime/resources"),
  captureControl: () =>
    request<
      import("./features/management/CaptureControlPanel").CaptureControlStatus
    >("/runtime/capture-control", { signal: AbortSignal.timeout(10000) }),
  recoverLiveSession: (id: string, seq: number) =>
    request(
      `/recordings/${id}/live-session/recover`,
      json("POST", { expected_seq: seq }),
    ),
  runtimeOverview: () => request<RuntimeOverview>("/runtime/overview"),
  serviceLifecycle: () =>
    request<{
      available: boolean;
      mode: string;
      events: import("./types").LifecycleEvent[];
    }>("/runtime/lifecycle", { signal: AbortSignal.timeout(10000) }),
  liveOverview: (id: string, version?: number) =>
    request<OverviewStatus>(
      `/recordings/${id}/overview${version ? `?version=${version}` : ""}`,
    ),
  enableOverview: (
    id: string,
    body: {
      profile_id: string;
      engine: string;
      confirm_cloud: boolean;
      expected_provider: unknown;
    },
  ) => request<OverviewStatus>(`/recordings/${id}/overview`, json("PUT", body)),
  pauseOverview: (id: string) =>
    request<OverviewStatus>(`/recordings/${id}/overview/pause`, json("POST")),
  refreshOverview: (id: string) =>
    request<OverviewStatus>(`/recordings/${id}/overview/refresh`, json("POST")),
  reviewOverview: (
    id: string,
    itemId: string,
    body: {
      state: string;
      fingerprint: string;
      version: number;
      content?: string;
    },
  ) =>
    request<OverviewStatus>(
      `/recordings/${id}/overview/items/${itemId}`,
      json("PUT", body),
    ),
  recordingReadiness: () => request<Readiness>("/recording-readiness"),
  sampleMeeting: () => request<SampleMeeting>("/onboarding/sample"),
  startCaptureTest: (source: "macos-system" | "macos-both") =>
    request<CaptureTest>("/capture-tests", json("POST", { source })),
  captureTest: (id: string) => request<CaptureTest>(`/capture-tests/${id}`),
  deleteCaptureTest: (id: string) =>
    request(`/capture-tests/${id}`, json("DELETE")),
  captureTestAudio: async (id: string) => {
    const result = await fetch(`/api/capture-tests/${id}/audio`);
    if (!result.ok) throw new Error("未获取到测试音频，请检查音源后重试");
    return result.blob();
  },
  terms: (scope: string) =>
    request<TermList>(`/terminology/${encodeURIComponent(scope)}`),
  createTerm: (scope: string, body: TermInput) =>
    request<Term>(
      `/terminology/${encodeURIComponent(scope)}`,
      json("POST", body),
    ),
  updateTerm: (scope: string, id: string, body: TermInput) =>
    request<Term>(
      `/terminology/${encodeURIComponent(scope)}/${id}`,
      json("PUT", body),
    ),
  deleteTerm: (scope: string, id: string, version: number) =>
    request(
      `/terminology/${encodeURIComponent(scope)}/${id}?version=${version}`,
      json("DELETE"),
    ),
  scanTerms: (
    scope: string,
    profile_id?: string,
    confirm_cloud = false,
    expected_provider?: Pick<
      LLMConfig,
      "id" | "base_url" | "model" | "provider_type"
    >,
  ) =>
    request<{ id: string }>(
      `/terminology/${encodeURIComponent(scope)}/scan`,
      json("POST", { profile_id, confirm_cloud, expected_provider }),
    ),
  qaPrepare: (body: QAPrepare) =>
    request<QARun>("/retrieval/qa/prepare", {
      ...json("POST", body),
      signal: AbortSignal.timeout(75000),
    }),
  qaGenerate: (id: string, confirmCloud: boolean) =>
    request<QARun>(
      `/retrieval/qa/${id}/generate`,
      json("POST", { confirm_cloud: confirmCloud }),
    ),
  qaRun: (id: string) =>
    request<QARun>(`/retrieval/qa/${id}`, {
      signal: AbortSignal.timeout(10000),
    }),
  qaHistory: () =>
    request<{ id: string; question: string; created: string }[]>(
      "/retrieval/qa",
    ),
  qaDelete: (id: string) =>
    request(`/retrieval/qa/${id}`, { method: "DELETE" }),
  searchStatus: () => request<SearchStatus>("/retrieval"),
  configureSearch: (profileId: string, model: string) =>
    request("/retrieval/config", json("PUT", { profile_id: profileId, model })),
  configureBuiltinSearch: () =>
    request("/retrieval/config", json("PUT", { provider: "builtin" })),
  installEmbedding: () =>
    request("/retrieval/builtin/install", json("POST", {})),
  cancelEmbeddingInstall: () =>
    request("/retrieval/builtin/cancel", json("POST", {})),
  importEmbedding: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request("/retrieval/builtin/import", { method: "POST", body });
  },
  buildSearch: (force: boolean) =>
    request("/retrieval/build", json("POST", { force })),
  searchLibrary: (body: {
    query: string;
    mode: string;
    meeting_id: string | null;
    kind: string | null;
    include_personal?: boolean;
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
  workspaceReadiness: () =>
    request<{
      paths: { id: string; path: string; state: string }[];
      free_bytes: number | null;
      python_version: string;
    }>("/runtime/workspace"),
  organizeMeetings: (body: {
    meeting_ids: string[];
    group?: string;
    group_kind?: "project" | "customer" | "series";
    archived?: boolean;
  }) => request<{ updated: number }>("/meeting-library", json("PUT", body)),
  meetings: () => request<MeetingSummary[]>("/meetings"),
  privateQualitySamples: () =>
    request<
      import("./features/management/QualitySamplesPanel").PrivateQualityInventory
    >("/runtime/quality-samples"),
  privateQualitySample: (id: string) =>
    request<import("./features/management/QualitySamplesPanel").PrivateSample>(
      `/runtime/quality-samples/${id}`,
    ),
  addPrivateQualitySample: (
    file: File,
    tags: string,
    rights: boolean,
    real: boolean,
  ) => {
    const form = new FormData();
    form.append("file", file);
    form.append("tags", tags);
    form.append("rights_confirmed", String(rights));
    form.append("real_speech", String(real));
    return request<
      import("./features/management/QualitySamplesPanel").PrivateSample
    >("/runtime/quality-samples", {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(45000),
    });
  },
  annotatePrivateQualitySample: (
    id: string,
    version: number,
    reference: string,
    heard: boolean,
  ) =>
    request(
      `/runtime/quality-samples/${id}/annotation`,
      json("PUT", {
        expected_version: version,
        reference,
        audio_reviewed: heard,
      }),
    ),
  freezePrivateQualityDataset: (members: { id: string; version: number }[]) =>
    request<{ id: string; samples: number }>(
      "/runtime/quality-datasets",
      json("POST", { members }),
    ),
  qualityBaseline: () =>
    request<
      import("./features/management/QualityBaselinePanel").QualityBaseline
    >("/runtime/quality-baseline", { signal: AbortSignal.timeout(10000) }),
  releaseReadiness: () =>
    request<
      import("./features/management/ReleaseReadinessPanel").ReleaseReadiness
    >("/runtime/release-readiness", { signal: AbortSignal.timeout(30000) }),
  recordingAcceptance: (id: string) =>
    request<
      import("./features/management/AcceptanceReviewPanel").AcceptanceAssessment
    >(`/runtime/recording-checks/${id}/acceptance`, {
      signal: AbortSignal.timeout(10000),
    }),
  reviewRecordingAcceptance: (
    id: string,
    body: {
      case: string;
      evidence_fingerprint: string;
      verdict: "passed" | "failed";
      real_device: boolean;
      audio_reviewed: boolean;
    },
  ) =>
    request(
      `/runtime/recording-checks/${id}/acceptance-review`,
      json("POST", body),
    ),
  recordingChecks: () =>
    request<RecordingObservation[]>("/runtime/recording-checks", {
      signal: AbortSignal.timeout(10000),
    }),
  recordingCheckTimeline: (id: string, offset = 0) =>
    request<ObservationTimelineData>(
      `/runtime/recording-checks/${id}/timeline?offset=${offset}&limit=50`,
      { signal: AbortSignal.timeout(10000) },
    ),
  startRecordingCheck: (recordingId: string, targetSeconds: number) =>
    request<RecordingObservation>("/runtime/recording-checks", {
      ...json("POST", {
        recording_id: recordingId,
        target_seconds: targetSeconds,
      }),
      signal: AbortSignal.timeout(10000),
    }),
  stopRecordingCheck: (id: string) =>
    request<RecordingObservation>(`/runtime/recording-checks/${id}/stop`, {
      ...json("POST", {}),
      signal: AbortSignal.timeout(10000),
    }),
  markRecordingCheck: (
    id: string,
    kind:
      | "background_return"
      | "device_changed"
      | "other_app_changed"
      | "pause_resume"
      | "wake_return",
  ) =>
    request(`/runtime/recording-checks/${id}/markers`, {
      ...json("POST", { kind }),
      signal: AbortSignal.timeout(10000),
    }),
  recordingKeepAwake: (id: string, enabled: boolean) =>
    request<{ active: boolean; supported: boolean; error: string }>(
      `/recordings/${id}/keep-awake`,
      { ...json("POST", { enabled }), signal: AbortSignal.timeout(10000) },
    ),
  recordings: (signal?: AbortSignal) =>
    request<RecordingSession[]>("/recordings", {
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(10000)])
        : AbortSignal.timeout(10000),
    }),
  archiveEmptyRecording: (id: string, archived: boolean) =>
    request(`/recordings/${id}/recovery-archive?archived=${archived}`, {
      method: "POST",
    }),
  recordingHealth: (id: string, signal?: AbortSignal) =>
    request<RecordingHealth>(`/recordings/${id}/health`, {
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(10000)])
        : AbortSignal.timeout(10000),
    }),
  captureState: (
    id: string,
    state: "recording" | "paused" | "stopped" | "interrupted",
  ) =>
    recordingCommands.execute(
      id,
      (
        {
          recording: "resume",
          paused: "pause",
          stopped: "stop",
          interrupted: "report_interrupted",
        } as const
      )[state],
      state,
    ),
  recordingQuality: (
    id: string,
    view: "all" | "pending" | "gaps" = "all",
    offset = 0,
  ) =>
    request<RecordingQuality>(
      `/meetings/${id}/recording-quality?view=${view}&offset=${offset}`,
      {
        signal: AbortSignal.timeout(10000),
      },
    ),
  reviewRecordingQuality: (
    id: string,
    aid: number,
    status: RecordingReviewStatus,
    expectedVersion: number,
  ) =>
    request(`/meetings/${id}/recording-quality/${aid}/review`, {
      ...json("POST", { status, expected_version: expectedVersion }),
      signal: AbortSignal.timeout(10000),
    }),
  recordingCompletion: (id: string, engine: "whisper" | "qwen") =>
    request<{
      quality: RecordingQuality;
      published: boolean;
      audio_saved: boolean;
      saved_bytes: number;
      duration_seconds: number;
      notes_count: number;
      coverage: {
        engine: string;
        covered_seconds: number;
        uncovered_seconds: number;
        gaps: { start: number; end: number }[];
        completed_ranges: number;
        failed_attempts: number;
      };
    }>(`/recordings/${id}/completion?engine=${engine}`, {
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
  nativeStatus: (id: string, signal?: AbortSignal) =>
    request<NativeCaptureStatus>(`/native-capture/${id}`, {
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(10000)])
        : AbortSignal.timeout(10000),
    }),
  recoverNativeControl: (id: string, expectedGeneration: number) =>
    request<NativeCaptureStatus>(
      `/native-capture/${id}/recover-control`,
      json("POST", { expected_generation: expectedGeneration }),
    ),
  nativeRecordings: () =>
    request<{ recording: RecordingSession; config: SessionConfig | null }[]>(
      "/native-recordings",
      { signal: AbortSignal.timeout(10000) },
    ),
  liveSessions: () =>
    request<{ recording: RecordingSession; config: SessionConfig }[]>(
      "/live-sessions",
    ),
  startLiveSession: (id: string, config: SessionConfig) =>
    request<SessionSnapshot>(
      `/recordings/${id}/live-session`,
      json("PUT", config),
    ),
  finishLiveSession: (id: string) =>
    request<SessionSnapshot>(
      `/recordings/${id}/live-session/finish`,
      json("POST", {}),
    ),
  liveSessionState: (id: string, paused: boolean) =>
    request(`/recordings/${id}/live-session/state`, json("POST", { paused })),
  uploadLiveAudio: (id: string, file: File, offset: number) => {
    const data = new FormData();
    data.append("file", file);
    data.append("offset", String(offset));
    return request(`/recordings/${id}/live-session/audio`, {
      method: "POST",
      body: data,
      signal: AbortSignal.timeout(10000),
    });
  },
  nativeDraft: (
    id: string,
    offset: number,
    engine: "whisper" | "qwen" = "whisper",
    sourceLanguage?: SourceLanguage,
  ) =>
    request<{
      ready: boolean;
      next_offset: number;
      skipped_seconds: number;
      segments: LiveDraftSegment[];
      processing_ms: number;
    }>(
      `/native-capture/${id}/draft`,
      json("POST", { offset, engine, source_language: sourceLanguage }),
    ),
  nativeDraftStream: (
    id: string,
    offset: number,
    onPartial: (partial: LivePartial) => void,
    signal: AbortSignal,
    responsive = false,
    windowSeconds: 5 | 10 | 15 = 15,
    contextEnabled = true,
    terms = "",
    sourceLanguage?: SourceLanguage,
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
          context_enabled: contextEnabled,
          terms,
          source_language: sourceLanguage,
        }),
        signal,
      },
      onPartial,
    ),
  pendingNativeCommand: (id: string) =>
    recordingCommands.pendingNativeCommand(id),
  retryNativeCommand: (id: string): Promise<NativeCaptureStatus> => {
    const command = recordingCommands.pendingNativeCommand(id);
    if (!command)
      return Promise.reject(
        new Error("本页未保留上次请求，请核对当前采集状态，必要时主动结束录音"),
      );
    return api.nativeCommand(id, command);
  },
  nativeCommand: async (id: string, command: "pause" | "resume" | "stop") => {
    const result = await recordingCommands.execute(id, command);
    const status = result.native;
    const expected =
      command === "pause"
        ? ["paused"]
        : command === "resume"
          ? ["recording"]
          : ["stopped", "interrupted", "failed"];
    if (!status || !expected.includes(status.state)) {
      reportRecordingCommand(id, "conflict");
      throw new Error("当前采集状态已变化，请重新核对；未重放旧操作");
    }
    return status;
  },
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
  preparation: () =>
    request<MeetingBrief>("/meeting-preparation", {
      signal: AbortSignal.timeout(10000),
    }),
  savePreparation: (brief: MeetingBrief) =>
    request<MeetingBrief>("/meeting-preparation", {
      ...json("PUT", brief),
      signal: AbortSignal.timeout(10000),
    }),
  saveBrief: (mid: string, brief: MeetingBrief) =>
    request<MeetingFlow>(`/meetings/${mid}/brief`, json("PUT", brief)),
  attentionDecision: (
    mid: string,
    nid: string,
    body: {
      decision: "meeting" | "personal" | "deferred";
      content: string;
      source_segment_ids: string[];
      fingerprint: string;
      revision: number;
      confirmed: boolean;
    },
  ) =>
    request<MeetingFlow>(
      `/meetings/${mid}/notes/${nid}/disposition`,
      json("PUT", body),
    ),
  trackMinutesAction: (
    mid: string,
    aid: string,
    index: number,
    body: {
      content: string;
      owner: string;
      due_date: string;
      confirmed: boolean;
    },
  ) =>
    request<{ reused: boolean }>(
      `/meetings/${mid}/minutes/${aid}/actions/${index}/track`,
      json("POST", body),
    ),
  carryTask: (mid: string, tid: string) =>
    request(`/meetings/${mid}/tasks/${tid}/carry`, json("POST")),
  unlinkTask: (mid: string, tid: string) =>
    request(`/meetings/${mid}/tasks/${tid}/link`, { method: "DELETE" }),
  audienceTemplate: (audience: "internal" | "external") =>
    request<{
      version: number;
      columns: string[];
      allowed: Record<string, string>;
    }>(`/audience-templates/${audience}`),
  saveAudienceTemplate: (
    audience: "internal" | "external",
    columns: string[],
    expectedVersion: number,
  ) =>
    request<{
      version: number;
      columns: string[];
      allowed: Record<string, string>;
    }>(
      `/audience-templates/${audience}`,
      json("PUT", { columns, expected_version: expectedVersion }),
    ),
  audiencePreview: (mid: string, body: AudiencePreviewInput) =>
    request<{
      candidates: { id: string; label: string; content: string }[];
      markdown: string;
      fingerprint: string;
      can_finalize: boolean;
    }>(`/meetings/${mid}/minutes/audience-preview`, json("POST", body)),
  finalizeAudience: (
    mid: string,
    body: AudiencePreviewInput,
    fingerprint: string,
  ) =>
    request<import("./types").MinutesDelivery>(
      `/meetings/${mid}/minutes/audience-finalize`,
      json("POST", { ...body, fingerprint, confirmed: true }),
    ),
  setDeliverySent: (mid: string, id: string, sent: boolean) =>
    request<import("./types").MinutesDelivery>(
      `/meetings/${mid}/minutes/deliveries/${id}/sent`,
      json("PUT", { sent }),
    ),
  deliveryReadiness: (mid: string, includeCitations: boolean) =>
    request<DeliveryReadiness>(
      `/meetings/${mid}/minutes/delivery-readiness?include_citations=${includeCitations}`,
    ),
  finalizeDelivery: (
    mid: string,
    artifactId: string,
    fingerprint: string,
    includeCitations: boolean,
  ) =>
    request<import("./types").MinutesDelivery & { reused: boolean }>(
      `/meetings/${mid}/minutes/delivery-finalize`,
      json("POST", {
        artifact_id: artifactId,
        fingerprint,
        include_citations: includeCitations,
        confirmed: true,
      }),
    ),
  createDelivery: (
    mid: string,
    artifactId: string,
    includeCitations: boolean,
  ) =>
    request<import("./types").MinutesDelivery>(
      `/meetings/${mid}/minutes/deliveries`,
      json("POST", {
        artifact_id: artifactId,
        include_citations: includeCitations,
        confirmed: true,
      }),
    ),
  followups: () =>
    request<(NoteTodo & { meeting_id: string; meeting_title: string })[]>(
      "/followups",
    ),
  recordingMetadata: (session: RecordingSession) =>
    request<RecordingSession>(`/recordings/${session.id}/metadata`, {
      ...json("PUT", {
        duration: session.duration,
        notes: session.notes,
        brief: session.brief,
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
  chineseMeetingRecord: (
    id: string,
    profile: string,
    offset: number,
    draftId?: string,
  ) =>
    request<{
      revision: number;
      counts: Record<ChineseResult["status"] | "pending", number>;
      total: number;
      items: (Omit<ChineseResult, "status"> & {
        id: string;
        original: string;
        status: ChineseResult["status"] | "pending";
      })[];
    }>(
      `/meetings/${id}${draftId ? `/experimental-drafts/${encodeURIComponent(draftId)}` : ""}/chinese-record?${new URLSearchParams({ profile_id: profile, offset: String(offset) })}`,
    ),
  translateMeetingSegment: (
    id: string,
    segment: string,
    revision: number,
    profile: string,
    text: string,
    signal: AbortSignal,
    draftId?: string,
  ) =>
    request<ChineseResult>(
      `/meetings/${id}${draftId ? `/experimental-drafts/${encodeURIComponent(draftId)}` : ""}/chinese-record/${encodeURIComponent(segment)}`,
      {
        ...json("POST", {
          ...(draftId ? {} : { revision }),
          profile_id: profile,
          expected_text: text,
        }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
      },
    ),
  translateLiveSegment: (
    id: string,
    range: string,
    index: number,
    profile: string,
    text: string,
    signal: AbortSignal,
  ) =>
    request<ChineseResult>(
      `/recordings/${id}/chinese-record/${range}/${index}`,
      {
        ...json("POST", { profile_id: profile, expected_text: text }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
      },
    ),
  upload: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<Meeting>("/meetings", { method: "POST", body: data });
  },
  liveCorrections: (id: string) =>
    request<LiveCorrection[]>(`/recordings/${id}/corrections`),
  proposeLiveCorrection: (
    id: string,
    rangeId: string,
    index: number,
    profileId: string,
  ) =>
    request<LiveCorrection>(
      `/recordings/${id}/corrections`,
      json("POST", {
        range_id: rangeId,
        segment_index: index,
        profile_id: profileId,
      }),
    ),
  decideLiveCorrection: (
    id: string,
    cid: string,
    state: "accepted" | "rejected" | "suggested",
  ) =>
    request<LiveCorrection>(
      `/recordings/${id}/corrections/${cid}`,
      json("PUT", { state }),
    ),
  liveTranscribeStream: (
    file: File,
    offset: number,
    recordingId: string | undefined,
    onPartial: (partial: LivePartial) => void,
    signal: AbortSignal,
    confirmed = true,
    contextEnabled = true,
    terms = "",
    sourceLanguage?: SourceLanguage,
  ) => {
    const data = new FormData();
    data.append("file", file);
    data.append("offset", String(offset));
    data.append("confirmed", String(confirmed));
    data.append("context_enabled", String(contextEnabled));
    data.append("terms", terms);
    if (sourceLanguage) data.append("source_language", sourceLanguage);
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
    signal?: AbortSignal,
    sourceLanguage?: SourceLanguage,
  ) => {
    const data = new FormData();
    data.append("file", file);
    data.append("offset", String(offset));
    data.append("engine", engine);
    if (sourceLanguage) data.append("source_language", sourceLanguage);
    if (recordingId) data.append("recording_id", recordingId);
    return request<{
      segments: LiveDraftSegment[];
      duration: number;
      processing_ms: number;
    }>("/live-transcribe", { method: "POST", body: data, signal });
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
      source_language?: TranscriptionLanguage;
      diarization: boolean;
      num_speakers: number;
      completion_mode?: "quick" | "full";
      recording_handoff?: boolean;
      minutes_followup?: {
        profile_id?: string;
        model?: string;
        confirm_cloud: boolean;
        expected_provider?: {
          id: string;
          base_url: string;
          provider_type: string;
          model: string;
        };
        settings: import("./features/minutes/composition").MinutesOptions;
      };
    },
  ) =>
    request<{ id: string; reused?: boolean }>(
      `/meetings/${id}/jobs`,
      json("POST", body),
    ),
  jobAction: (id: string, action: "cancel" | "retry", attempt?: number) =>
    request(`/jobs/${id}/${action}`, {
      method: "POST",
      ...(attempt !== undefined
        ? { headers: { "X-Task-Attempt": String(attempt) } }
        : {}),
    }),
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
  composeMinutes: (
    id: string,
    body: import("./features/minutes/composition").MinutesOptions & {
      expected_analysis_id?: string;
      chinese_record_profile_id?: string;
      confirm_cloud?: boolean;
      expected_provider?: {
        id: string;
        base_url: string;
        provider_type: "local" | "cloud";
        model: string;
      };
      profile_id?: string;
      model?: string;
      source_minutes_id?: string;
    },
  ) =>
    request<{
      artifact?: Artifact<MeetingMinutes>;
      job_id?: string;
      analysis_job_id?: string;
      warning?: string;
    }>(
      `/meetings/${id}/minutes/${body.method === "analysis" ? "compose" : "jobs"}`,
      json("POST", body),
    ),
  adoptMinutes: (id: string, artifactId: string) =>
    request(`/meetings/${id}/minutes/${artifactId}/adopt`, json("POST")),
  saveMinutes: (
    id: string,
    sourceAnalysisId: string | null,
    document: MeetingMinutes,
    sourceMinutesId?: string,
    mergeFromMinutesId?: string,
  ) =>
    request<Artifact<MeetingMinutes>>(
      `/meetings/${id}/minutes`,
      json("PUT", {
        source_analysis_id: sourceAnalysisId,
        source_minutes_id: sourceMinutesId,
        allow_branch: true,
        merge_from_minutes_id: mergeFromMinutesId,
        document,
      }),
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
  shorthandNotes: (rid: string) =>
    request<import("./types").CopilotNote[]>(`/recordings/${rid}/notes/shorthand`),
  postShorthand: (
    rid: string,
    body: {
      raw_text: string;
      captured_at?: number;
      synthesize?: boolean;
      speaker_hint?: string;
      client_id?: string;
    },
  ) =>
    request<{
      id: string;
      raw_text: string;
      status: string;
      captured_at: number;
      speaker_hint?: string;
      synthesized_text?: string;
    }>(`/recordings/${rid}/notes/shorthand`, json("POST", body)),
  candidates: (rid: string) =>
    request<{
      candidates: Array<{
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
      }>;
      accepted_count: number;
      dismissed_count: number;
    }>(`/recordings/${rid}/candidates`),
  acceptCandidate: (rid: string, cid: string) =>
    request<{
      ok: boolean;
      candidate_id: string;
      status: string;
      note?: any;
    }>(`/recordings/${rid}/candidates/${cid}/accept`, { method: "POST" }),
  rejectCandidate: (rid: string, cid: string) =>
    request<{
      ok: boolean;
      candidate_id: string;
      status: string;
    }>(`/recordings/${rid}/candidates/${cid}/reject`, { method: "POST" }),
};

export const downloadUrl = (path: string) => `/api${path}`;
export const download = (path: string, filename = "") => {
  const link = document.createElement("a");
  link.href =
    path.startsWith("/api/") || path.startsWith("blob:")
      ? path
      : downloadUrl(path);
  link.download = filename;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
};

export async function downloadDelivery(path: string, filename: string) {
  const response = await checkedResponse(path);
  const url = URL.createObjectURL(await response.blob());
  download(url, filename);
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
