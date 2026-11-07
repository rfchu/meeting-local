import { browserCaptureAllowed } from "../../preferences/nativeHost";
import { reconcileNativePhase } from "./recordingCoordinator";
import { useNativeCaptureStatus } from "./useNativeCaptureStatus";
import { useModelChoice } from "../../preferences/useModelChoice";
import { ensureNoteIds } from "../overview/notes";
import { recordingPlan, type Readiness } from "../onboarding/readiness";
import type { SessionSnapshot } from "./liveSession";
import { useLiveSession } from "./useLiveSession";
import { useLiveCorrections } from "./useLiveCorrections";
import { emptyBrief } from "../meeting/MeetingBriefForm";
import type { MeetingBrief, MeetingSummary } from "../../types";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type {
  LiveDraftSegment,
  MeetingNoteInput,
  MeetingNoteType,
  RecordingSession,
  NativeCapability,
  NativeCaptureStatus,
  EngineInfo,
  Environment,
} from "../../types";
import {
  readRecordingPreferences,
  saveRecordingPreferences,
  DEFAULT_RECORDING_PREFERENCES,
  type CompletionMode,
} from "./recordingPreferences";
import { clock } from "../../utils";
import { encodeLiveWav } from "./liveAudio";
import { useRecordingEnd } from "./RecordingEndGuide";
import { nativeCaptureSeconds } from "./nativeCaptureClock";
import { clientRecordingDiagnostics } from "./recordingInventory";
import { stopRecorder } from "./stopRecorder";
import { useRecordingAwake } from "./useRecordingAwake";
import { watchRecordingRecovery } from "./recordingRecovery";
import { queueAwake } from "./awakeQueue";
import { takeFrames } from "./rollingAudio";
import {
  bufferRecording,
  flushRecording,
  forgetRecording,
  pendingRecordingChunks,
  syncRecording,
} from "./recordingBuffer";

const noteLabels: Record<MeetingNoteType, string> = {
  note: "简记",
  highlight: "重点",
  question: "问题",
  decision: "决策",
  action: "待办",
};

const recordingState = (state: string) =>
  ["recording", "paused"].includes(state);

const supportedMime = () =>
  ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find((value) =>
    MediaRecorder.isTypeSupported(value),
  ) || "";

export type RecordingDialogProps = {
  targetRecordingId?: string;
  environment?: Environment;
  minutesModel: string;
  onMinutesModel: (model: string) => void;
  meetings: MeetingSummary[];
  flowReady: boolean;
  onModels: () => void;
  engineLabel: string;
  engines: EngineInfo[];
  onClose: () => void;
  onComplete: (
    meetingId: string,
    options: {
      mode: CompletionMode;
      engine: "whisper" | "qwen";
      goal?: "transcript" | "minutes";
    },
  ) => Promise<void>;
  toast: (text: string) => void;
};

export function useRecordingController({
  targetRecordingId,
  engineLabel,
  meetings,
  flowReady,
  engines,
  onClose,
  onComplete,
  toast,
  onModels,
  environment,
  minutesModel,
  onMinutesModel,
}: RecordingDialogProps) {
  const closeWithPreparation = () => {
    if (!flowReady || stateRef.current !== "idle" || !briefReady) {
      onClose();
      return;
    }
    briefQueue.current = briefQueue.current
      .catch(() => {})
      .then(() => api.savePreparation(briefRef.current))
      .then(() => {
        onClose();
      })
      .catch((e) => toast("会议准备未保存，窗口已保留：" + e.message));
  };
  const [brief, setBrief] = useState<MeetingBrief>(emptyBrief);
  const briefRef = useRef(brief);
  briefRef.current = brief;
  const [briefReady, setBriefReady] = useState(false),
    [briefSaved, setBriefSaved] = useState(true);
  const briefQueue = useRef(Promise.resolve());
  useEffect(() => {
    if (!flowReady) {
      setBriefReady(true);
      return;
    }
    setBriefReady(false);
    let live = true;
    void api
      .preparation()
      .then((value) => {
        if (live)
          setBrief({
            ...value,
            source_language:
              value.source_language || preferences.sourceLanguage || "auto",
            output_language:
              value.output_language || preferences.outputLanguage || "zh",
          });
      })
      .catch((e) => toast("会议准备未载入：" + e.message))
      .finally(() => live && setBriefReady(true));
    return () => {
      live = false;
    };
  }, [flowReady]);
  const [preferences] = useState(() => {
    try {
      return readRecordingPreferences(window.localStorage);
    } catch {
      return { ...DEFAULT_RECORDING_PREFERENCES };
    }
  });
  const [liveEngine, chooseLiveEngine, liveChoice] =
    useModelChoice("live_transcription");
  const draftEngine: "qwen" | "whisper" =
    liveEngine === "qwen" ? "qwen" : "whisper";
  const setDraftEngine = (value: "qwen" | "whisper") => chooseLiveEngine(value);
  const [correctionProfileId, setCorrectionProfileId] =
    useModelChoice("correction");
  const [preflight, setPreflight] = useState<Readiness | null>(null);
  const [recordOnly, setRecordOnly] = useState(false);
  const recordOnlyRef = useRef(false);
  const processingDeferredRef = useRef(false);
  const [mobilePane, setMobilePane] = useState("transcript");
  const acceptReadiness = useCallback((value: Readiness) => {
    setPreflight(value);
    setNativeCapability(value.native);
  }, []);
  const [termsOpen, setTermsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false),
    [settingsTab, setSettingsTab] = useState<
      "recording" | "model" | "outcome" | "preparation"
    >("recording");
  const [keepAwake, setKeepAwake] = useState(preferences.keepAwake);
  const [silenceReminder, setSilenceReminder] = useState(
    preferences.silenceReminder,
  );
  const [nativeUnavailable, setNativeUnavailable] = useState(false);
  const nativeHealthyRef = useRef(false);
  const browserStateQueue = useRef<Promise<unknown>>(Promise.resolve());
  const reportBrowserState = (
    state: "recording" | "paused" | "stopped" | "interrupted",
  ) => {
    const session = sessionRef.current;
    if (!session || session.source === "macos") return Promise.resolve();
    browserStateQueue.current = browserStateQueue.current
      .catch(() => {})
      .then(() => api.captureState(session.id, state))
      .catch(() => {
        setSaveStatus("采集状态尚未同步，已有录音保留，正在等待连接恢复");
      });
    return browserStateQueue.current;
  };
  const [historyOpen, setHistoryOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false),
    [noteOptionsOpen, setNoteOptionsOpen] = useState(false);
  const noteOptionsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!noteOptionsOpen) return;
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !noteOptionsRef.current?.contains(event.target)
      )
        setNoteOptionsOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setNoteOptionsOpen(false);
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape, true);
    };
  }, [noteOptionsOpen]);
  const [preferencesSaved, setPreferencesSaved] = useState(true),
    [savedMeetingId, setSavedMeetingId] = useState(""),
    [savedEngine, setSavedEngine] = useState<"qwen" | "whisper">("whisper"),
    [completing, setCompleting] = useState(false);
  const [state, setState] = useState<
      | "idle"
      | "requesting"
      | "recording"
      | "paused"
      | "saving"
      | "failed"
      | "finished"
    >("idle"),
    [elapsed, setElapsed] = useState(0),
    [noteText, setNoteText] = useState(""),
    [noteType, setNoteType] = useState<MeetingNoteType>("note"),
    [noteScope, setNoteScope] = useState<"personal" | "meeting">("personal"),
    [notes, setNotesState] = useState<MeetingNoteInput[]>([]),
    [drafts, setDrafts] = useState<LiveDraftSegment[]>([]),
    [partialDraft, setPartialDraft] = useState<LiveDraftSegment | null>(null),
    [liveMetrics, setLiveMetrics] = useState({
      firstMs: null as number | null,
      processingMs: 0,
      rtf: null as number | null,
      lagMs: null as number | null,
      revisions: 0,
    }),
    [decodeReport, setDecodeReport] = useState<SessionSnapshot["metrics"]>({}),
    [prefixEnabled, setPrefixEnabled] = useState(preferences.prefixEnabled),
    [contextEnabled, setContextEnabled] = useState(preferences.contextEnabled),
    [recognitionTerms, setRecognitionTerms] = useState(
      preferences.recognitionTerms,
    ),
    [correctionEnabled, setCorrectionEnabled] = useState(
      preferences.correctionEnabled,
    ),
    [correctionReviewOpen, setCorrectionReviewOpen] = useState(false),
    [responsiveMode, setResponsiveMode] = useState(preferences.responsiveMode),
    [modelStatus, setModelStatus] = useState<Awaited<
      ReturnType<typeof api.liveModelStatus>
    > | null>(null),
    [modelActionBusy, setModelActionBusy] = useState(false),
    [streamEnabled, setStreamEnabled] = useState(preferences.streamEnabled),
    [postGoal, setPostGoal] = useState(preferences.postGoal),
    [autoProcess, setAutoProcess] = useState(preferences.autoProcess),
    [completionMode, setCompletionMode] = useState<CompletionMode>(
      preferences.completionMode,
    ),
    [coverageDetails, setCoverageDetails] = useState<{
      gaps: { start: number; end: number }[];
      recent_failures: {
        start: number;
        end: number;
        state: string;
        error: string;
      }[];
    } | null>(null),
    [coverageStatus, setCoverageStatus] = useState("开始识别后保存覆盖情况"),
    [draftStatus, setDraftStatus] = useState("开始录音后自动生成"),
    [saveStatus, setSaveStatus] = useState("录音分段会自动保存到本机"),
    [captureSource, setCaptureSource] = useState<
      "browser" | "macos-system" | "macos-both"
    >(preferences.captureSource),
    [nativeCapability, setNativeCapability] = useState<NativeCapability | null>(
      null,
    ),
    [checkingCapture, setCheckingCapture] = useState(false),
    [captureHealth, setCaptureHealth] =
      useState<NativeCaptureStatus["health"]>(),
    [nativeOwnership, setNativeOwnership] =
      useState<NativeCaptureStatus["ownership"]>(),
    [recoveringControl, setRecoveringControl] = useState(false),
    [nativeSessionId, setNativeSessionId] = useState(""),
    [microphoneLevels, setMicrophoneLevels] = useState<number[]>([]),
    [systemLevels, setSystemLevels] = useState<number[]>([]);
  const setNotes = useCallback(
    (
      value:
        MeetingNoteInput[] | ((old: MeetingNoteInput[]) => MeetingNoteInput[]),
    ) => {
      setNotesState((old) =>
        ensureNoteIds(typeof value === "function" ? value(old) : value, () =>
          crypto.randomUUID(),
        ),
      );
    },
    [],
  );
  const [jumpDraft, setJumpDraft] = useState<{
    id: string;
    nonce: number;
  } | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null),
    streamRef = useRef<MediaStream | null>(null),
    sessionRef = useRef<RecordingSession | null>(null),
    bufferQueueRef = useRef<Promise<void>>(Promise.resolve()),
    failedChunksRef = useRef<{ index: number; blob: Blob }[]>([]),
    localSaveFailedRef = useRef(false),
    activeSinceRef = useRef(0),
    accruedRef = useRef(0),
    notesRef = useRef(notes),
    noteDraftRef = useRef({ text: noteText, type: noteType, scope: noteScope }),
    stateRef = useRef(state),
    audioContextRef = useRef<AudioContext | null>(null),
    audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null),
    audioProcessorRef = useRef<ScriptProcessorNode | null>(null),
    silentGainRef = useRef<GainNode | null>(null),
    pcmFramesRef = useRef<Float32Array[]>([]),
    pcmSamplesRef = useRef(0),
    draftOffsetRef = useRef(0),
    nativeDraftOffsetRef = useRef(0),
    priorPreviewRef = useRef<LiveDraftSegment | null>(null),
    lastVoiceSampleRef = useRef(0),
    lastPreviewSampleRef = useRef(0),
    nativeDraftBusyRef = useRef(false),
    streamAbortRef = useRef<AbortController | null>(null),
    draftQueueRef = useRef<Promise<void>>(Promise.resolve()),
    draftPendingRef = useRef(0),
    draftEnabledRef = useRef(true);
  notesRef.current = notes;
  const endGuide = useRecordingEnd(
    recordingState(state),
    state === "recording",
    silenceReminder,
    () => stop(),
  );
  noteDraftRef.current = { text: noteText, type: noteType, scope: noteScope };

  const persistPreferences = (mode = completionMode) => {
    let saved = false;
    try {
      saved = saveRecordingPreferences(window.localStorage, {
        version: 1,
        sourceLanguage: brief.source_language || "auto",
        outputLanguage: brief.output_language || "zh",
        keepAwake,
        silenceReminder,
        autoProcess,
        postGoal,
        captureSource,
        draftEngine,
        streamEnabled,
        contextEnabled,
        prefixEnabled,
        recognitionTerms,
        correctionEnabled,
        correctionProfileId,
        responsiveMode,
        completionMode: mode,
      });
    } catch {
      /* storage can be disabled */
    }
    setPreferencesSaved(saved);
  };
  useEffect(() => {
    persistPreferences();
  }, [
    brief.source_language,
    brief.output_language,
    captureSource,
    draftEngine,
    streamEnabled,
    responsiveMode,
    contextEnabled,
    prefixEnabled,
    recognitionTerms,
    correctionEnabled,
    correctionProfileId,
    completionMode,
    postGoal,
    autoProcess,
    keepAwake,
    silenceReminder,
  ]);
  const correctionSessionId = nativeSessionId || sessionRef.current?.id;
  const managedMode =
    !recordOnly && (draftEngine === "whisper" || streamEnabled);
  const backendLive = useLiveSession(
    correctionSessionId,
    managedMode && ["recording", "paused"].includes(state),
    {
      engine: draftEngine,
      source_language: brief.source_language || "zh",
      responsive: responsiveMode,
      context_enabled: contextEnabled,
      terms: recognitionTerms,
      prefix_enabled: prefixEnabled,
    },
    (event) => {
      if (event.type === "snapshot") {
        setDecodeReport(event.metrics);
        setDrafts(event.segments);
        setPartialDraft(
          "text" in event.partial ? (event.partial as LiveDraftSegment) : null,
        );
        nativeDraftOffsetRef.current = event.cursor;
        setDraftStatus(
          event.status === "stopped"
            ? "实时会话已结束"
            : "后端持续识别 · 已同步保存结果",
        );
      } else if (event.type === "partial") {
        if (event.metrics) setDecodeReport(event.metrics);
        updatePartial(event.partial);
        setDraftStatus(
          event.metrics?.extending
            ? "连续讲话中 · 正在延长窗口，实时文字继续更新"
            : "实时预览 · 浅色尾部仍可修订",
        );
      } else if (event.type === "final") {
        setDecodeReport({
          load: event.result.load,
          decoder: event.result.decoder,
          decoder_totals: event.result.decoder_totals,
          boundary_reason: event.result.boundary_reason,
          extending: event.result.extending,
        });
        setPartialDraft(null);
        nativeDraftOffsetRef.current = event.result.next_offset;
        trackResult(event.result, event.result.next_offset);
        setDrafts((old) => [
          ...old,
          ...event.result.segments.filter(
            (s) =>
              !old.some(
                (x) =>
                  x.live_range_id === s.live_range_id &&
                  x.live_segment_index === s.live_segment_index,
              ),
          ),
        ]);
        setDraftStatus(
          event.result.skipped_seconds > 0
            ? "已跟随最近语音，积压范围保留待补识别"
            : event.result.boundary_reason === "hard_limit"
              ? "达到 25 秒上限，后续片段将关联阅读"
              : "本段已确认并保存",
        );
      } else if (event.type === "status") setDraftStatus(event.message);
    },
    setDraftStatus,
  );

  const liveCorrection = useLiveCorrections(
    correctionSessionId,
    drafts,
    correctionEnabled && draftEngine === "qwen" && streamEnabled,
    correctionProfileId,
    state === "recording" || state === "paused",
  );
  const displayDrafts = drafts.map((draft) => {
    const correction = liveCorrection.corrections.find(
      (c) =>
        c.range_id === draft.live_range_id &&
        c.segment_index === (draft.live_segment_index || 0) &&
        c.state === "accepted",
    );
    if (!correction || correction.original_text !== draft.text) return draft;
    let text = draft.text;
    const edits = [...correction.edits].sort(
      (a, b) => draft.text.indexOf(b.original) - draft.text.indexOf(a.original),
    );
    for (const edit of edits) {
      const start = text.indexOf(edit.original);
      text =
        text.slice(0, start) +
        edit.replacement +
        text.slice(start + edit.original.length);
    }
    return {
      ...draft,
      text,
      asr_original_text: draft.text,
      live_correction_id: correction.id,
    };
  });
  const [runningSessions, setRunningSessions] = useState<
    Awaited<ReturnType<typeof api.nativeRecordings>>
  >([]);
  useEffect(() => {
    if (state !== "idle") return;
    let valid = true;
    let polling = false;
    const poll = async () => {
      if (!valid || polling) return;
      polling = true;
      try {
        const rows = await api.nativeRecordings();
        if (valid) setRunningSessions(rows);
      } catch {
        /* Preserve the last known entry until connectivity returns. */
      } finally {
        polling = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 5000);
    const unwatch = watchRecordingRecovery(() => void poll());
    return () => {
      valid = false;
      window.clearInterval(timer);
      unwatch();
    };
  }, [state]);
  const attachRunning = async (
    entry: Awaited<ReturnType<typeof api.nativeRecordings>>[number],
  ) => {
    const session = entry.recording;
    try {
      const status = await api.nativeStatus(session.id);
      if (status.ownership?.control_required) {
        if (!status.ownership.can_recover_control)
          return toast(
            "原控制租约尚未释放，请稍后重新检查；录音仍由独立服务保存。",
          );
        if (status.ownership.controller_generation == null)
          return toast("采集控制身份尚未核对，请重新检查。");
        if (
          !window.confirm(
            "恢复这场录音的控制权？这只重新连接控制服务，不会重新开始录音。原控制租约尚未到期时会拒绝接管。",
          )
        )
          return;
        await api.recoverNativeControl(
          session.id,
          status.ownership.controller_generation,
        );
      }
    } catch (error) {
      return toast((error as Error).message);
    }
    sessionRef.current = session;
    notesRef.current = session.notes || [];
    setNotes(session.notes || []);
    setBrief({
      ...(session.brief || emptyBrief),
      source_language:
        session.brief?.source_language || session.source_language || "zh",
      output_language: session.brief?.output_language || "zh",
    });
    setCaptureSource(
      session.include_microphone ? "macos-both" : "macos-system",
    );
    setRecordOnly(!entry.config);
    recordOnlyRef.current = !entry.config;
    setDraftEngine(entry.config?.engine || "qwen");
    setStreamEnabled(true);
    setResponsiveMode(entry.config?.responsive ?? false);
    setContextEnabled(entry.config?.context_enabled ?? false);
    setPrefixEnabled(entry.config?.prefix_enabled !== false);
    setRecognitionTerms(entry.config?.terms || "");
    accruedRef.current = session.duration;
    activeSinceRef.current = performance.now();
    draftEnabledRef.current = !!entry.config;
    stateRef.current = "requesting";
    setState("requesting");
    setNativeSessionId(session.id);
    setRunningSessions([]);
  };
  const recoverActiveControl = async () => {
    if (!nativeSessionId || recoveringControl) return;
    setRecoveringControl(true);
    try {
      const status = await api.nativeStatus(nativeSessionId);
      if (!status.ownership?.control_required) {
        setNativeOwnership(status.ownership);
        return;
      }
      if (
        !status.ownership.can_recover_control ||
        status.ownership.controller_generation == null
      )
        return toast(
          "原控制租约尚未释放，请等待重新检查；不会中断或重复采集。",
        );
      if (
        !window.confirm(
          "恢复这场录音的控制权？只连接已有采集器，不会重新开始录音。",
        )
      )
        return;
      const recovered = await api.recoverNativeControl(
        nativeSessionId,
        status.ownership.controller_generation,
      );
      setNativeOwnership(recovered.ownership);
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setRecoveringControl(false);
    }
  };
  const handingOff = useRef(false);
  const finishing = useRef(false);
  const [finishStage, setFinishStage] = useState("正在收尾音频…");
  const [handoffError, setHandoffError] = useState("");
  const handoffSaved = async (
    id: string,
    mode: CompletionMode,
    engine: "whisper" | "qwen",
  ) => {
    if (handingOff.current) return;
    handingOff.current = true;
    setHandoffError("");
    setCompleting(true);
    try {
      await onComplete(id, {
        mode,
        engine,
        goal: mode === "none" ? "transcript" : postGoal,
      });
    } catch (error) {
      setHandoffError((error as Error).message);
      toast(`录音已保存，后续处理未完成，可重试：${(error as Error).message}`);
    } finally {
      handingOff.current = false;
      setCompleting(false);
    }
  };
  const processSaved = async (mode: CompletionMode) => {
    if (!savedMeetingId || handingOff.current) return;
    setCompletionMode(mode);
    persistPreferences(mode);
    await handoffSaved(savedMeetingId, mode, savedEngine);
  };

  const currentSeconds = () =>
    sessionRef.current?.source === "macos"
      ? nativeCaptureSeconds(
          accruedRef.current,
          activeSinceRef.current,
          performance.now(),
          stateRef.current === "recording" && nativeHealthyRef.current,
        )
      : accruedRef.current +
        (stateRef.current === "recording"
          ? (performance.now() - activeSinceRef.current) / 1000
          : 0);

  const checkCapture = async () => {
    setCheckingCapture(true);
    try {
      setNativeCapability(await api.nativeCapability());
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setCheckingCapture(false);
    }
  };

  useEffect(() => {
    if (draftEngine !== "qwen") return;
    let active = true;
    const poll = () =>
      void api
        .liveModelStatus()
        .then((status) => {
          if (active) setModelStatus(status);
        })
        .catch(() => {});
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [draftEngine]);
  const controlModel = async (action: "warm" | "release") => {
    setModelActionBusy(true);
    try {
      await api.liveModelControl(action);
      setModelStatus(await api.liveModelStatus());
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setModelActionBusy(false);
    }
  };
  const updatePartial = (partial: LiveDraftSegment) =>
    setPartialDraft((old) => {
      // Keep the prior preview while a larger cumulative decode catches up.
      if (
        responsiveMode &&
        old &&
        Math.abs(old.start - partial.start) < 0.01 &&
        partial.text.length < old.text.length
      )
        return old;
      return partial;
    });
  const trackResult = (
    result: {
      segments: LiveDraftSegment[];
      processing_ms: number;
      confirmed?: boolean;
      first_text_ms?: number | null;
      duration?: number;
    },
    windowEnd?: number,
  ) => {
    const current = result.segments[0],
      previous = priorPreviewRef.current;
    const changed =
      responsiveMode &&
      current &&
      previous &&
      Math.abs(current.start - previous.start) < 0.01 &&
      current.text !== previous.text;
    setLiveMetrics((old) => ({
      firstMs: result.first_text_ms ?? null,
      processingMs: result.processing_ms,
      rtf: result.duration
        ? result.processing_ms / 1000 / result.duration
        : null,
      lagMs:
        result.confirmed === false || windowEnd === undefined
          ? null
          : Math.max(0, (currentSeconds() - windowEnd) * 1000),
      revisions: old.revisions + (changed ? 1 : 0),
    }));
    priorPreviewRef.current =
      result.confirmed === false ? current || null : null;
  };
  const exportDiagnostics = async (id: string) => {
    const [server, local] = await Promise.allSettled([
      api.recordingDiagnostics(id),
      pendingRecordingChunks(id),
    ]);
    const current = sessionRef.current?.id === id;
    const report = {
      schema_version: 1,
      server:
        server.status === "fulfilled" ? server.value : { available: false },
      client: clientRecordingDiagnostics(
        current ? state : "idle",
        local.status === "fulfilled" ? local.value : null,
        current ? failedChunksRef.current.length : 0,
        current
          ? liveMetrics
          : {
              firstMs: null,
              processingMs: 0,
              rtf: null,
              lagMs: null,
              revisions: 0,
            },
      ),
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "tingji-recording-diagnostics.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("脱敏诊断已导出，不包含录音和会议正文");
  };
  useEffect(() => {
    void checkCapture();
  }, []);
  useNativeCaptureStatus(
    nativeSessionId,
    sessionRef.current?.source === "macos" &&
      ["requesting", "recording", "paused"].includes(state),
    {
      status: (status) => {
        if (["saving", "finished"].includes(stateRef.current)) return;
        accruedRef.current = status.duration;
        nativeHealthyRef.current =
          status.state === "recording" && !status.health?.stalled;
        setNativeUnavailable(false);
        activeSinceRef.current = performance.now();
        setElapsed(status.duration);
        setCaptureHealth(status.health);
        setNativeOwnership(status.ownership);
        const tracks = status.health?.tracks || [];
        endGuide.observe(
          tracks.length > 0 && tracks.every((track) => track.state === "quiet"),
          !status.health?.stalled &&
            tracks.length > 0 &&
            tracks.every((track) =>
              ["quiet", "receiving"].includes(track.state),
            ),
        );
        setSystemLevels(status.system_levels || []);
        setMicrophoneLevels(status.microphone_levels || []);
        const nextPhase = reconcileNativePhase(stateRef.current, status.state);
        if (nextPhase !== stateRef.current) {
          stateRef.current = nextPhase as "recording" | "paused";
          setState(nextPhase as "recording" | "paused");
        }
        setSaveStatus(
          status.health?.stalled
            ? `采集心跳未更新，最后确认位置 ${clock(status.duration)}；无法确认仍在采集`
            : status.health?.tracks.some((t) => t.file_state === "unconfirmed")
              ? "音轨文件更新待核对，请检查音轨与磁盘，已有文件保留"
              : status.state === "starting"
                ? "等待 macOS 采集权限…"
                : status.error ||
                  `音轨文件末端 · ${status.health?.tracks.map((t) => `${t.name === "system" ? "电脑" : "麦克风"} ${t.saved_seconds.toFixed(1)}s`).join(" · ") || "待核对"}`,
        );
        if (
          ["failed", "interrupted", "stopped"].includes(status.state) &&
          stateRef.current !== "saving"
        ) {
          stateRef.current = "failed";
          setState("failed");
          toast(status.error || "采集已停止，可以恢复已有录音");
        }
      },
      failure: (error) => {
        nativeHealthyRef.current = false;
        setNativeUnavailable(true);
        setSaveStatus(`采集状态暂时不可用：${(error as Error).message}`);
      },
      checking: () => {
        nativeHealthyRef.current = false;
        setNativeUnavailable(true);
        setSaveStatus("正在重新核对采集状态，已有音轨保留");
      },
    },
    state,
  );
  useEffect(() => {
    if (
      !nativeSessionId ||
      captureSource === "browser" ||
      managedMode ||
      recordOnly
    )
      return;
    let active = true;
    const poll = async () => {
      if (
        nativeDraftBusyRef.current ||
        !["recording", "paused"].includes(stateRef.current)
      )
        return;
      nativeDraftBusyRef.current = true;
      try {
        const result = await api.nativeDraft(
          nativeSessionId,
          nativeDraftOffsetRef.current,
          draftEngine,
          briefRef.current.source_language || "zh",
        );
        if (!active || !draftEnabledRef.current || !result.ready) return;
        trackResult(result, result.next_offset);
        nativeDraftOffsetRef.current = result.next_offset;
        setDrafts((old) => [...old, ...result.segments]);
        setDraftStatus(
          result.skipped_seconds > 0
            ? "已跟随最近语音，积压音频保留"
            : "本段已确认",
        );
      } catch (error) {
        if (active)
          setDraftStatus(`实时识别暂时不可用 · ${(error as Error).message}`);
      } finally {
        nativeDraftBusyRef.current = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [nativeSessionId, captureSource, draftEngine, managedMode, recordOnly]);
  useEffect(() => {
    if (!sessionRef.current || state === "idle") return;
    let active = true;
    const poll = async () => {
      try {
        const coverage = await api.liveCoverage(
          sessionRef.current!.id,
          draftEngine,
        );
        if (active) setCoverageDetails(coverage);
        if (active)
          setCoverageStatus(
            `已保存 ${coverage.completed_ranges} 段 · 覆盖 ${Math.round(coverage.covered_seconds)}s / ${Math.round(coverage.duration)}s · 未识别范围 ${coverage.gaps.length} 处 · 失败尝试 ${coverage.attempts.failed || 0} 次`,
          );
      } catch (error) {
        if (active)
          setCoverageStatus(`覆盖检查暂时不可用 · ${(error as Error).message}`);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [state, nativeSessionId, draftEngine]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (
        ["recording", "paused", "saving", "failed"].includes(stateRef.current)
      )
        event.preventDefault();
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);
  const metadata = () => {
    const session = sessionRef.current;
    if (!session) return null;
    session.duration = currentSeconds();
    session.updated = Date.now() / 1000;
    session.notes = [...notesRef.current];
    session.brief = briefRef.current;
    return structuredClone(session);
  };
  const persist = (session: RecordingSession, index?: number, blob?: Blob) => {
    const task = bufferQueueRef.current
      .catch(() => {})
      .then(() => bufferRecording(session, index, blob))
      .catch((error) => {
        localSaveFailedRef.current = true;
        if (blob && index !== undefined)
          failedChunksRef.current.push({ index, blob });
        if (recorderRef.current?.state === "recording") {
          accruedRef.current = currentSeconds();
          recorderRef.current.pause();
          void reportBrowserState("paused");
          stateRef.current = "paused";
          setState("paused");
        }
        setSaveStatus(`暂存失败，录音已暂停：${error.message}。请重试保存`);
        throw error;
      });
    bufferQueueRef.current = task;
    void task
      .then(async () => {
        setSaveStatus(
          session.source === "macos"
            ? "简记已暂存，正在同步录音信息…"
            : "浏览器已暂存，正在同步到本机…",
        );
        try {
          await syncRecording(session.id);
          setSaveStatus(
            session.source === "macos"
              ? "录音信息和简记已同步到本机"
              : "最近一批分段已同步到本机",
          );
        } catch (error) {
          setSaveStatus(
            `待重试 · ${(error as Error).message}；浏览器暂存已保留`,
          );
        }
      })
      .catch(() => {});
    return task;
  };
  useEffect(() => {
    if (!["recording", "paused"].includes(state)) return;
    const save = () => {
      const session = metadata();
      if (session) void persist(session).catch(() => {});
    };
    const timer = window.setInterval(save, 5000);
    save();
    return () => window.clearInterval(timer);
  }, [state, notes]);
  const retrySync = async () => {
    const session = metadata();
    if (!session) return;
    try {
      await bufferQueueRef.current.catch(() => {});
      for (const chunk of failedChunksRef.current)
        await bufferRecording(session, chunk.index, chunk.blob);
      failedChunksRef.current = [];
      await bufferRecording(session);
      localSaveFailedRef.current = false;
      await flushRecording(session.id);
      setSaveStatus(
        session.source === "macos"
          ? "录音信息和简记已同步到本机"
          : "分段已同步到本机",
      );
    } catch (error) {
      setSaveStatus(`保存未完成：${(error as Error).message}`);
      throw error;
    }
  };
  const finish = async (
    session: RecordingSession,
    reuseEngine = draftEngine,
    allowAutoProcess = false,
  ) => {
    if (finishing.current) return;
    finishing.current = true;
    setState("saving");
    stateRef.current = "saving";
    sessionRef.current = session;
    try {
      setFinishStage("同步最后的音频与关注点…");
      await retrySync();
      setFinishStage("创建本机会议文件，收尾实时识别…");
      const meeting =
        session.source === "macos"
          ? await api.nativeFinish(session.id)
          : await api.finishRecording(session.id, session.chunk_count);
      setSaveStatus("录音和简记已保存到本机");
      setSavedMeetingId(meeting.id);
      setSavedEngine(reuseEngine);
      stateRef.current = "finished";
      setState("finished");
      setElapsed(accruedRef.current);
      // Publication is already durable. A local cleanup or report failure must
      // never send the user back to the recording-save recovery state.
      try {
        await forgetRecording(session.id);
      } catch {
        setSaveStatus("本机会议已保存，浏览器暂存清理未完成");
      }
      if (allowAutoProcess && autoProcess && !processingDeferredRef.current) {
        try {
          const receipt = await api.recordingCompletion(
            session.id,
            reuseEngine,
          );
          if (!receipt.audio_saved)
            throw new Error("本机文件核对未通过，请检查运行详情后继续");
          await handoffSaved(meeting.id, completionMode, reuseEngine);
        } catch {
          setHandoffError(
            "录音已保存，自动交接前的文件核对未完成，可重新核对或手动进入会后整理。",
          );
        }
      }
    } catch (error) {
      setState("failed");
      stateRef.current = "failed";
      toast(`录音已保留，请重试完成保存：${(error as Error).message}`);
    } finally {
      finishing.current = false;
    }
  };
  useEffect(() => {
    if (state !== "recording") return;
    const timer = window.setInterval(() => setElapsed(currentSeconds()), 250);
    return () => window.clearInterval(timer);
  }, [state]);
  useEffect(
    () => () => {
      draftEnabledRef.current = false;
      streamAbortRef.current?.abort();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      audioSourceRef.current?.disconnect();
      audioProcessorRef.current?.disconnect();
      silentGainRef.current?.disconnect();
      void audioContextRef.current?.close();
    },
    [],
  );

  const queueDraft = (final = false) => {
    if (recordOnlyRef.current) return draftQueueRef.current;
    const context = audioContextRef.current,
      sampleCount = pcmSamplesRef.current;
    if (
      !context ||
      sampleCount < context.sampleRate * (final ? 0.5 : managedMode ? 2 : 15)
    )
      return draftQueueRef.current;
    const count = Math.min(
      sampleCount,
      context.sampleRate * (managedMode ? 10 : 15),
    );
    const { selected, remaining } = takeFrames(pcmFramesRef.current, count);
    pcmFramesRef.current = remaining;
    pcmSamplesRef.current -= count;
    const encoded = encodeLiveWav(selected, context.sampleRate),
      offset = draftOffsetRef.current;
    draftOffsetRef.current += encoded.duration;
    const file = new File([encoded.blob], `live-${offset.toFixed(1)}.wav`, {
      type: "audio/wav",
    });
    draftPendingRef.current++;
    const task = draftQueueRef.current.then(async () => {
      try {
        if (!draftEnabledRef.current || !sessionRef.current) return;
        if (managedMode) {
          await backendLive.ensure(sessionRef.current.id);
          await api.uploadLiveAudio(sessionRef.current.id, file, offset);
        } else {
          const result = await api.liveTranscribe(
            file,
            offset,
            draftEngine,
            sessionRef.current.id,
            undefined,
            briefRef.current.source_language || "zh",
          );
          if (!draftEnabledRef.current) return;
          trackResult(result, offset + encoded.duration);
          setDrafts((old) => [...old, ...result.segments]);
          setDraftStatus("本段已确认");
        }
      } catch (error) {
        if (draftEnabledRef.current)
          setDraftStatus(
            `实时音频同步暂时不可用 · ${(error as Error).message}；完整录音已保留`,
          );
      } finally {
        draftPendingRef.current--;
        if (
          draftEnabledRef.current &&
          stateRef.current === "paused" &&
          pcmSamplesRef.current > 0
        )
          void queueDraft(true);
      }
    });
    draftQueueRef.current = task;
    return task;
  };

  const startDraftCapture = async (stream: MediaStream) => {
    const context = new AudioContext();
    audioContextRef.current = context;
    await context.resume();
    const source = context.createMediaStreamSource(stream),
      processor = context.createScriptProcessor(4096, 1, 1),
      silentGain = context.createGain();
    silentGain.gain.value = 0;
    processor.onaudioprocess = (event) => {
      if (stateRef.current !== "recording") return;
      const frame = new Float32Array(event.inputBuffer.getChannelData(0));
      let power = 0;
      for (const sample of frame) power += sample * sample;
      const rms = Math.sqrt(power / frame.length);
      endGuide.observe(rms < 0.003);
      setMicrophoneLevels((old) => [...old.slice(-71), rms]);
      if (recordOnlyRef.current) return;
      pcmFramesRef.current.push(frame);
      pcmSamplesRef.current += frame.length;
      if (rms >= 0.003) lastVoiceSampleRef.current = pcmSamplesRef.current;
      // Bound backlog while inference is slower than capture. Final audio is retained.
      while (pcmSamplesRef.current > context.sampleRate * 30) {
        const dropped = pcmFramesRef.current.shift();
        if (!dropped) break;
        pcmSamplesRef.current -= dropped.length;
        lastVoiceSampleRef.current = Math.max(
          0,
          lastVoiceSampleRef.current - dropped.length,
        );
        lastPreviewSampleRef.current = 0;
        draftOffsetRef.current += dropped.length / context.sampleRate;
        setDraftStatus("识别积压，优先跟随最近语音；完整录音已保留");
      }
      if (
        pcmSamplesRef.current >= context.sampleRate * (managedMode ? 2 : 15) &&
        draftPendingRef.current < 2
      )
        void queueDraft();
    };
    source.connect(processor);
    processor.connect(silentGain);
    silentGain.connect(context.destination);
    audioContextRef.current = context;
    audioSourceRef.current = source;
    audioProcessorRef.current = processor;
    silentGainRef.current = silentGain;
  };

  const closeDraftCapture = async () => {
    audioSourceRef.current?.disconnect();
    audioProcessorRef.current?.disconnect();
    silentGainRef.current?.disconnect();
    audioSourceRef.current = null;
    audioProcessorRef.current = null;
    silentGainRef.current = null;
    const context = audioContextRef.current;
    audioContextRef.current = null;
    if (context && context.state !== "closed") await context.close();
  };

  const start = async () => {
    if (targetRecordingId) return;
    if (captureSource === "browser" && !browserCaptureAllowed())
      return toast(
        "内嵌工作区未启用浏览器采集。请改用原生音轨，或在浏览器工作台录音。",
      );
    if (
      captureSource !== "browser" &&
      nativeCapability?.ownership &&
      nativeCapability.ownership.lease !== "free"
    )
      return toast(
        "采集所有权待核对，请先检查正在进行的录音；不会自动接管或重复启动。",
      );
    if (preflight && !preflight.storage.writable)
      return toast("数据目录无法写入，请修复后再录音");
    const plan = recordingPlan(
      liveChoice.ready &&
        !!(preflight?.engines || engines).find(
          (engine) => engine.id === draftEngine,
        )?.ready,
      completionMode,
      autoProcess,
    );
    recordOnlyRef.current = plan.recordOnly;
    setRecordOnly(plan.recordOnly);
    processingDeferredRef.current =
      plan.recordOnly ||
      (postGoal === "minutes" &&
        (!minutesModel || !environment?.auto_minutes_ready));
    if (!plan.recordOnly && processingDeferredRef.current)
      toast("AI 纪要配置尚未就绪，本次先保存，结束后可继续整理");
    if (captureSource !== "browser" && !nativeCapability?.available)
      return toast("上次选择的原生音源暂不可用，请调整设置");
    if (liveChoice.ready) setDraftEngine(draftEngine);
    if (correctionEnabled && correctionProfileId)
      setCorrectionProfileId(correctionProfileId);
    setSettingsOpen(false);
    persistPreferences();
    setPartialDraft(null);
    setDecodeReport({});
    priorPreviewRef.current = null;
    setLiveMetrics({
      firstMs: null,
      processingMs: 0,
      rtf: null,
      lagMs: null,
      revisions: 0,
    });
    setMicrophoneLevels([]);
    setSystemLevels([]);
    if (captureSource !== "browser") {
      stateRef.current = "requesting";
      setState("requesting");
      try {
        const session = await api.nativeStart(
          captureSource === "macos-both",
          keepAwake,
        );
        session.brief = briefRef.current;
        sessionRef.current = session;
        setNativeSessionId(session.id);
        await bufferRecording(session);
        accruedRef.current = 0;
        activeSinceRef.current = performance.now();
        draftEnabledRef.current = !recordOnlyRef.current;
        nativeDraftOffsetRef.current = 0;
        setDraftStatus(
          recordOnlyRef.current
            ? "仅录音与简记，结束后可配置模型转写"
            : `${draftEngine === "qwen" ? "Qwen-ASR" : "Whisper"} ${draftEngine === "qwen" && responsiveMode && streamEnabled ? "短片段预览与修订" : "约 15 秒识别"}，仅作会议跟随参考`,
        );
      } catch (error) {
        if (sessionRef.current?.source === "macos") {
          const rid = sessionRef.current.id;
          void queueAwake(rid, () => api.recordingKeepAwake(rid, false)).catch(
            () => {},
          );
          await api.nativeCommand(rid, "stop").catch(() => {});
        }
        stateRef.current = "idle";
        setState("idle");
        toast((error as Error).message);
      }
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      toast("当前浏览器不支持麦克风录音");
      return;
    }
    stateRef.current = "requesting";
    setState("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;
      const mimeType = supportedMime();
      const recorder = new MediaRecorder(stream, {
        ...(mimeType ? { mimeType } : {}),
        audioBitsPerSecond: 128000,
      });
      const session = await api.createRecording(
        recorder.mimeType || mimeType || "audio/webm",
      );
      await bufferRecording(session);
      session.brief = briefRef.current;
      sessionRef.current = session;
      recorder.ondataavailable = (event) => {
        if (!event.data.size || !sessionRef.current) return;
        const index = sessionRef.current.chunk_count++;
        const session = metadata();
        if (session) void persist(session, index, event.data).catch(() => {});
      };
      recorder.addEventListener("stop", () => {
        if (["recording", "paused"].includes(stateRef.current)) {
          toast("录音输入已停止，正在保存已有内容");
          void stop().catch((error) => toast(error.message));
        }
      });
      recorder.onerror = () => {
        void reportBrowserState("interrupted");
        accruedRef.current = currentSeconds();
        stateRef.current = "failed";
        setState("failed");
        draftEnabledRef.current = false;
        stream.getTracks().forEach((track) => track.stop());
        void closeDraftCapture();
        toast("录音输入中断，已有分段已保留，请重试完成保存");
      };
      stream.getAudioTracks().forEach((track) => {
        track.onended = () => {
          if (["recording", "paused"].includes(stateRef.current)) void stop();
        };
      });
      recorderRef.current = recorder;
      streamRef.current = stream;
      pcmFramesRef.current = [];
      pcmSamplesRef.current = 0;
      draftOffsetRef.current = 0;
      lastVoiceSampleRef.current = 0;
      lastPreviewSampleRef.current = 0;
      setDrafts([]);
      setDraftStatus(
        recordOnlyRef.current
          ? "仅录音与简记，结束后可配置模型转写"
          : "正在收集第一段语音…",
      );
      accruedRef.current = 0;
      activeSinceRef.current = performance.now();
      stateRef.current = "recording";
      draftEnabledRef.current = !recordOnlyRef.current;
      try {
        await startDraftCapture(stream);
      } catch (error) {
        setDraftStatus(`实时草稿不可用 · ${(error as Error).message}`);
        await closeDraftCapture();
      }
      recorder.start(5000);
      void reportBrowserState("recording");
      setState("recording");
    } catch (error) {
      stateRef.current = "idle";
      streamRef.current?.getTracks().forEach((track) => track.stop());
      await closeDraftCapture();
      setState("idle");
      toast(
        (error as DOMException).name === "NotAllowedError"
          ? "需要允许麦克风权限才能开始录音"
          : `无法开始录音：${(error as Error).message}`,
      );
    }
  };

  const pause = () => {
    if (sessionRef.current?.source === "macos") {
      void api
        .nativeCommand(sessionRef.current.id, "pause")
        .then((status) => {
          accruedRef.current = status.duration;
          stateRef.current = "paused";
          setState("paused");
        })
        .catch((error) => toast(error.message));
      return;
    }
    const recorder = recorderRef.current;
    if (!recorder || state !== "recording") return;
    setMicrophoneLevels([]);
    accruedRef.current = currentSeconds();
    recorder.pause();
    void reportBrowserState("paused");
    setElapsed(accruedRef.current);
    stateRef.current = "paused";
    setState("paused");
    if (managedMode && sessionRef.current) {
      const rid = sessionRef.current.id;
      void queueDraft(true)
        .then(() =>
          stateRef.current === "paused"
            ? api.liveSessionState(rid, true)
            : undefined,
        )
        .catch(() => {});
    }
  };
  const resume = () => {
    if (sessionRef.current?.source === "macos") {
      void api
        .nativeCommand(sessionRef.current.id, "resume")
        .then((status) => {
          accruedRef.current = status.duration;
          activeSinceRef.current = performance.now();
          stateRef.current = "recording";
          setState("recording");
        })
        .catch((error) => toast(error.message));
      return;
    }
    const recorder = recorderRef.current;
    if (!recorder || state !== "paused") return;
    if (failedChunksRef.current.length)
      return toast("请先重试保存，再继续录音");
    activeSinceRef.current = performance.now();
    recorder.resume();
    void reportBrowserState("recording");
    if (managedMode && sessionRef.current)
      void api.liveSessionState(sessionRef.current.id, false).catch(() => {});
    stateRef.current = "recording";
    setState("recording");
  };
  useEffect(() => {
    if (!flowReady || !briefReady || state !== "idle") return;
    setBriefSaved(false);
    const timer = window.setTimeout(() => {
      const snapshot = brief;
      briefQueue.current = briefQueue.current
        .catch(() => {})
        .then(() => api.savePreparation(snapshot))
        .then(() => {
          if (briefRef.current === snapshot) setBriefSaved(true);
        })
        .catch((e) => {
          toast("会议准备保存失败：" + e.message);
        });
    }, 600);
    return () => window.clearTimeout(timer);
  }, [brief, briefReady, state, flowReady]);
  const addNote = () => {
    const text = noteText.trim();
    if (!text) return;
    const capturedAt = Math.max(0, currentSeconds());
    setNotes((old) => [
      ...old,
      {
        captured_at: capturedAt,
        note_type: noteType,
        raw_text: text,
        note_scope: noteScope,
      },
    ]);
    setNoteText("");
  };
  const markNow = (type: MeetingNoteType = "note") => {
    if (!["recording", "paused"].includes(stateRef.current)) return;
    setNotes((old) => [
      ...old,
      {
        captured_at: Math.max(0, currentSeconds()),
        note_type: type,
        raw_text: "",
        note_scope: "personal",
        is_marker: true,
      },
    ]);
  };
  useEffect(() => {
    if (!recordingState(state)) return;
    const handler = (event: KeyboardEvent) => {
      if (
        event.altKey &&
        event.code === "KeyM" &&
        !event.repeat &&
        !event.isComposing
      ) {
        event.preventDefault();
        markNow();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [state]);
  const stopCapture = async () => {
    if (!recordingState(stateRef.current)) return;
    setFinishStage("停止采集并收取音频尾段…");
    if (sessionRef.current)
      void api.pauseOverview(sessionRef.current.id).catch(() => {});
    if (sessionRef.current?.source === "macos") {
      accruedRef.current = currentSeconds();
      draftEnabledRef.current = false;
      streamAbortRef.current?.abort();
      setPartialDraft(null);
      stateRef.current = "saving";
      setState("saving");
      if (noteDraftRef.current.text.trim()) {
        notesRef.current = [
          ...notesRef.current,
          {
            captured_at: accruedRef.current,
            note_type: noteDraftRef.current.type,
            raw_text: noteDraftRef.current.text.trim(),
            note_scope: noteDraftRef.current.scope,
          },
        ];
        setNotes(notesRef.current);
        setNoteText("");
      }
      try {
        const status = await api.nativeCommand(sessionRef.current.id, "stop");
        accruedRef.current = status.duration;
        const session = metadata();
        if (session) await finish(session, draftEngine, true);
      } catch (error) {
        stateRef.current = "failed";
        setState("failed");
        toast((error as Error).message);
      }
      return;
    }
    const recorder = recorderRef.current;
    if (!recorder || !["recording", "paused"].includes(stateRef.current))
      return;
    if (stateRef.current === "recording") accruedRef.current = currentSeconds();
    setElapsed(accruedRef.current);
    stateRef.current = "saving";
    setState("saving");
    const savedNotes = [...notesRef.current];
    if (noteDraftRef.current.text.trim())
      savedNotes.push({
        captured_at: accruedRef.current,
        note_type: noteDraftRef.current.type,
        raw_text: noteDraftRef.current.text.trim(),
        note_scope: noteDraftRef.current.scope,
      });
    notesRef.current = savedNotes;
    setNotes(savedNotes);
    setNoteText("");
    await stopRecorder(recorder);
    await reportBrowserState("stopped");
    if (managedMode) {
      // Wait only for bounded local audio transport, never an unbounded inference queue.
      await Promise.race([
        queueDraft(true).catch(() => {}),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    }
    // Saving the full recording must not wait for optional draft inference.
    draftEnabledRef.current = false;
    streamAbortRef.current?.abort();
    setPartialDraft(null);
    await closeDraftCapture();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    const session = metadata();
    if (session) await finish(session, draftEngine, true);
  };
  const stop = async () => {
    try {
      await stopCapture();
    } catch (error) {
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        stateRef.current = recorder.state === "paused" ? "paused" : "recording";
      } else {
        draftEnabledRef.current = false;
        streamAbortRef.current?.abort();
        streamRef.current?.getTracks().forEach((track) => track.stop());
        stateRef.current = "failed";
      }
      setState(stateRef.current);
      toast(`结束未完成，已保存内容保留，可重试：${(error as Error).message}`);
    }
  };

  const recording = state === "recording" || state === "paused";
  const awakeStatus = useRecordingAwake(
    ["requesting", "recording", "paused", "saving"].includes(state),
    sessionRef.current?.id || "",
    keepAwake,
  );
  const sourceLabel = {
    browser: "浏览器麦克风",
    "macos-system": "电脑声音",
    "macos-both": "电脑声音 + 麦克风",
  }[captureSource];
  const engineName = draftEngine === "qwen" ? "千问 Qwen-ASR" : "Whisper";
  return {
    acceptReadiness,
    accruedRef,
    addNote,
    attachRunning,
    autoProcess,
    awakeStatus,
    backendLive,
    brief,
    briefReady,
    briefSaved,
    captureHealth,
    nativeOwnership,
    recoveringControl,
    recoverActiveControl,
    captureSource,
    checkCapture,
    checkingCapture,
    closeWithPreparation,
    completing,
    completionMode,
    contextEnabled,
    controlModel,
    correctionEnabled,
    correctionProfileId,
    correctionReviewOpen,
    correctionSessionId,
    coverageDetails,
    coverageStatus,
    currentSeconds,
    decodeReport,
    displayDrafts,
    draftEngine,
    draftStatus,
    drafts,
    elapsed,
    endGuide,
    engineLabel,
    engineName,
    engines,
    environment,
    exportDiagnostics,
    failedChunksRef,
    finish,
    finishStage,
    flowReady,
    handoffError,
    handoffSaved,
    historyOpen,
    inspectorOpen,
    jumpDraft,
    keepAwake,
    liveCorrection,
    liveMetrics,
    localSaveFailedRef,
    managedMode,
    markNow,
    meetings,
    metadata,
    microphoneLevels,
    minutesModel,
    mobilePane,
    modelActionBusy,
    modelStatus,
    nativeCapability,
    nativeUnavailable,
    noteLabels,
    noteOptionsOpen,
    noteOptionsRef,
    noteScope,
    noteText,
    noteType,
    notes,
    onClose,
    onMinutesModel,
    onModels,
    partialDraft,
    pause,
    postGoal,
    preferencesSaved,
    prefixEnabled,
    preflight,
    processSaved,
    processingDeferredRef,
    recognitionTerms,
    recordOnly,
    recording,
    responsiveMode,
    resume,
    retrySync,
    runningSessions: targetRecordingId
      ? runningSessions.filter(
          (entry) => entry.recording.id === targetRecordingId,
        )
      : runningSessions,
    targetRecordingId,
    saveStatus,
    savedEngine,
    savedMeetingId,
    sessionRef,
    setAutoProcess,
    setBrief,
    setCaptureSource,
    setCompletionMode,
    setContextEnabled,
    setCorrectionEnabled,
    setCorrectionProfileId,
    setCorrectionReviewOpen,
    setDraftEngine,
    setHistoryOpen,
    setInspectorOpen,
    setJumpDraft,
    setKeepAwake,
    setMobilePane,
    setNoteOptionsOpen,
    setNoteScope,
    setNoteText,
    setNoteType,
    setNotes,
    setPostGoal,
    setPrefixEnabled,
    setRecognitionTerms,
    setResponsiveMode,
    setSettingsOpen,
    setSettingsTab,
    setSilenceReminder,
    setStreamEnabled,
    setTermsOpen,
    settingsOpen,
    settingsTab,
    silenceReminder,
    sourceLabel,
    start,
    state,
    stop,
    streamEnabled,
    systemLevels,
    termsOpen,
    toast,
  };
}
export type RecordingViewModel = ReturnType<typeof useRecordingController>;
