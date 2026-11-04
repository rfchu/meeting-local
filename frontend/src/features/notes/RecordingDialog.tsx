import type { SessionSnapshot } from "./liveSession";
import { useLiveSession } from "./useLiveSession";
import { useLiveCorrections } from "./useLiveCorrections";
import LiveCorrectionReview from "./LiveCorrectionReview";
import RecordingSetupBar from "./RecordingSetupBar";
import MeetingBriefForm, { emptyBrief } from "../meeting/MeetingBriefForm";
import type { MeetingBrief, MeetingSummary } from "../../types";
import { useEffect, useRef, useState } from "react";
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
import Modal from "../../components/Modal";
import { encodeLiveWav } from "./liveAudio";
import RecordingWaveform from "./RecordingWaveform";
import RecordingHealthPanel from "./RecordingHealthPanel";
import { clientRecordingDiagnostics } from "./recordingInventory";
import { stopRecorder } from "./stopRecorder";
import LiveDraftList from "./LiveDraftList";
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

export default function RecordingDialog({
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
}: {
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
}) {
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
        if (live) setBrief(value);
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
  const [settingsOpen, setSettingsOpen] = useState(false),
    [settingsTab, setSettingsTab] = useState<
      "recording" | "model" | "outcome" | "preparation"
    >("recording");
  const [keepAwake, setKeepAwake] = useState(preferences.keepAwake);
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
    [notes, setNotes] = useState<MeetingNoteInput[]>([]),
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
    [correctionProfileId, setCorrectionProfileId] = useState(
      preferences.correctionProfileId,
    ),
    [correctionReviewOpen, setCorrectionReviewOpen] = useState(false),
    [responsiveMode, setResponsiveMode] = useState(preferences.responsiveMode),
    [modelStatus, setModelStatus] = useState<Awaited<
      ReturnType<typeof api.liveModelStatus>
    > | null>(null),
    [modelActionBusy, setModelActionBusy] = useState(false),
    [streamEnabled, setStreamEnabled] = useState(preferences.streamEnabled),
    [draftEngine, setDraftEngine] = useState<"whisper" | "qwen">(
      preferences.draftEngine,
    ),
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
    [nativeSessionId, setNativeSessionId] = useState(""),
    [microphoneLevels, setMicrophoneLevels] = useState<number[]>([]),
    [systemLevels, setSystemLevels] = useState<number[]>([]);
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
  noteDraftRef.current = { text: noteText, type: noteType, scope: noteScope };

  const persistPreferences = (mode = completionMode) => {
    let saved = false;
    try {
      saved = saveRecordingPreferences(window.localStorage, {
        version: 1,
        keepAwake,
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
  ]);
  const correctionSessionId = nativeSessionId || sessionRef.current?.id;
  const managedMode = draftEngine === "qwen" && streamEnabled;
  const backendLive = useLiveSession(
    correctionSessionId,
    managedMode && ["recording", "paused"].includes(state),
    {
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
        setDraftStatus("实时预览 · 浅色尾部仍可修订");
      } else if (event.type === "final") {
        setDecodeReport({
          decoder: event.result.decoder,
          decoder_totals: event.result.decoder_totals,
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
    Awaited<ReturnType<typeof api.liveSessions>>
  >([]);
  useEffect(() => {
    if (state !== "idle") return;
    let valid = true;
    api
      .liveSessions()
      .then((rows) => {
        if (valid) setRunningSessions(rows);
      })
      .catch(() => {});
    return () => {
      valid = false;
    };
  }, [state]);
  const attachRunning = (
    entry: Awaited<ReturnType<typeof api.liveSessions>>[number],
  ) => {
    const session = entry.recording;
    sessionRef.current = session;
    notesRef.current = session.notes || [];
    setNotes(session.notes || []);
    setBrief(session.brief || emptyBrief);
    setCaptureSource(
      session.include_microphone ? "macos-both" : "macos-system",
    );
    setDraftEngine("qwen");
    setStreamEnabled(true);
    setResponsiveMode(entry.config.responsive);
    setContextEnabled(entry.config.context_enabled);
    setPrefixEnabled(entry.config.prefix_enabled !== false);
    setRecognitionTerms(entry.config.terms);
    accruedRef.current = session.duration;
    activeSinceRef.current = performance.now();
    draftEnabledRef.current = true;
    stateRef.current = "requesting";
    setState("requesting");
    setNativeSessionId(session.id);
    setRunningSessions([]);
  };
  const handingOff = useRef(false);
  const handoffSaved = async (
    id: string,
    mode: CompletionMode,
    engine: "whisper" | "qwen",
  ) => {
    if (handingOff.current) return;
    handingOff.current = true;
    setCompleting(true);
    try {
      await onComplete(id, {
        mode,
        engine,
        goal: mode === "none" ? "transcript" : postGoal,
      });
    } catch (error) {
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
    accruedRef.current +
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
  useEffect(() => {
    if (
      !sessionRef.current ||
      sessionRef.current.source !== "macos" ||
      !["requesting", "recording", "paused"].includes(state)
    )
      return;
    let active = true,
      polling = false;
    const poll = async () => {
      if (!active || polling) return;
      polling = true;
      try {
        const status = await api.nativeStatus(sessionRef.current!.id);
        if (!active || ["saving", "finished"].includes(stateRef.current))
          return;
        accruedRef.current = status.duration;
        activeSinceRef.current = performance.now();
        setElapsed(status.duration);
        setCaptureHealth(status.health);
        setSystemLevels(status.system_levels || []);
        setMicrophoneLevels(status.microphone_levels || []);
        if (
          ["recording", "paused"].includes(status.state) &&
          stateRef.current === "requesting"
        ) {
          stateRef.current = status.state as "recording" | "paused";
          setState(status.state as "recording" | "paused");
        }
        setSaveStatus(
          status.state === "starting"
            ? "等待 macOS 采集权限…"
            : status.error ||
                `原生音轨保存中 · 电脑 ${((status.system_frames || 0) / 16000).toFixed(1)}s · 麦克风 ${((status.microphone_frames || 0) / 16000).toFixed(1)}s`,
        );
        if (
          ["failed", "interrupted", "stopped"].includes(status.state) &&
          stateRef.current !== "saving"
        ) {
          stateRef.current = "failed";
          setState("failed");
          toast(status.error || "采集已停止，可以恢复已有录音");
        }
      } catch (error) {
        if (active)
          setSaveStatus(`采集状态暂时不可用：${(error as Error).message}`);
      } finally {
        polling = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    const unwatch = watchRecordingRecovery(() => void poll());
    return () => {
      active = false;
      window.clearInterval(timer);
      unwatch();
    };
  }, [state, captureSource, nativeSessionId]);
  useEffect(() => {
    if (!nativeSessionId || captureSource === "browser" || managedMode) return;
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
  }, [nativeSessionId, captureSource, draftEngine, managedMode]);
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
    setState("saving");
    stateRef.current = "saving";
    sessionRef.current = session;
    try {
      await retrySync();
      const meeting =
        session.source === "macos"
          ? await api.nativeFinish(session.id)
          : await api.finishRecording(session.id, session.chunk_count);
      await forgetRecording(session.id);
      setSaveStatus("录音和简记已保存到本机");
      setSavedMeetingId(meeting.id);
      setSavedEngine(reuseEngine);
      stateRef.current = "finished";
      setState("finished");
      setElapsed(accruedRef.current);
      if (allowAutoProcess && autoProcess)
        await handoffSaved(meeting.id, completionMode, reuseEngine);
    } catch (error) {
      setState("failed");
      stateRef.current = "failed";
      toast(`录音已保留，请重试完成保存：${(error as Error).message}`);
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
      setMicrophoneLevels((old) => [...old.slice(-71), rms]);
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
    if (!engines.find((engine) => engine.id === draftEngine)?.ready)
      return toast("上次选择的识别引擎未就绪，请调整设置");
    if (captureSource !== "browser" && !nativeCapability?.available)
      return toast("上次选择的原生音源暂不可用，请调整设置");
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
        draftEnabledRef.current = true;
        nativeDraftOffsetRef.current = 0;
        setDraftStatus(
          `${draftEngine === "qwen" ? "Qwen-ASR" : "Whisper"} ${draftEngine === "qwen" && responsiveMode && streamEnabled ? "短片段预览与修订" : "约 15 秒识别"}，仅作会议跟随参考`,
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
      setDraftStatus("正在收集第一段语音…");
      accruedRef.current = 0;
      activeSinceRef.current = performance.now();
      stateRef.current = "recording";
      draftEnabledRef.current = true;
      try {
        await startDraftCapture(stream);
      } catch (error) {
        setDraftStatus(`实时草稿不可用 · ${(error as Error).message}`);
        await closeDraftCapture();
      }
      recorder.start(5000);
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
  const stop = async () => {
    if (!recordingState(stateRef.current)) return;
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
    await stopRecorder(recorder);
    if (managedMode) {
      // Wait only for bounded local audio transport, never an unbounded inference queue.
      await Promise.race([
        queueDraft(true),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    }
    // Saving the full recording must not wait for optional draft inference.
    draftEnabledRef.current = false;
    streamAbortRef.current?.abort();
    setPartialDraft(null);
    await closeDraftCapture();
    streamRef.current?.getTracks().forEach((track) => track.stop());
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
    const session = metadata();
    if (session) await finish(session, draftEngine, true);
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
  return (
    <Modal
      id="recordingWorkbench"
      className="recording-workbench"
      ariaLabel="录音与会议简记工作台"
      onClose={() => {
        if (
          !recording &&
          !completing &&
          !["saving", "requesting"].includes(state) &&
          !failedChunksRef.current.length &&
          !localSaveFailedRef.current
        )
          closeWithPreparation();
      }}
    >
      <div className="workbench-topbar">
        <div>
          <div className="eyebrow">本机会议采集</div>
          <h2>录音与会议简记</h2>
        </div>
        <div className={`recording-clock${recording ? " active" : ""}`}>
          <span /> {clock(elapsed)}
        </div>
        <div className="recording-controls">
          {state === "idle" && (
            <button
              className="primary"
              disabled={
                (postGoal === "minutes" &&
                  (!minutesModel || !environment?.auto_minutes_ready)) ||
                !engines.find((engine) => engine.id === draftEngine)?.ready ||
                (captureSource !== "browser" && !nativeCapability?.available)
              }
              onClick={() => void start()}
            >
              ● 开始录音
            </button>
          )}
          {state === "requesting" && (
            <button disabled>
              {captureSource === "browser"
                ? "正在请求麦克风…"
                : "等待 macOS 采集权限…"}
            </button>
          )}
          {state === "recording" && <button onClick={pause}>暂停</button>}
          {state === "paused" && <button onClick={resume}>继续录音</button>}
          {recording && (
            <button className="recording-stop" onClick={() => void stop()}>
              ■ {autoProcess ? "结束并整理" : "结束录音"}
            </button>
          )}
          {state === "saving" && <button disabled>正在保存并创建会议…</button>}
          {state === "failed" && (
            <button
              className="primary"
              onClick={() => {
                const session = metadata();
                if (session)
                  void finish(session, session.draft_engine || draftEngine);
              }}
            >
              重试完成保存
            </button>
          )}
          {!recording &&
            !completing &&
            state !== "saving" &&
            state !== "requesting" && (
              <button
                onClick={() => {
                  if (
                    failedChunksRef.current.length ||
                    localSaveFailedRef.current
                  )
                    return toast("还有未落盘的分段，请先重试保存");
                  closeWithPreparation();
                }}
              >
                {state === "failed"
                  ? "稍后恢复"
                  : state === "finished"
                    ? "返回会议资料库"
                    : "返回"}
              </button>
            )}
        </div>
      </div>
      <RecordingSetupBar
        source={sourceLabel}
        recognition={`${engineName} · ${draftEngine === "qwen" && streamEnabled ? (responsiveMode ? "短片段模式" : "逐字推送") : "分段识别"}`}
        outcome={
          completionMode === "none"
            ? "仅保存录音与简记"
            : postGoal === "minutes"
              ? "文字稿＋AI 纪要"
              : "生成会议文字稿"
        }
        processing={`${completionMode === "none" ? "暂不转写" : completionMode === "quick" ? "快速完成" : "精细处理"} · ${autoProcess ? "自动进入整理" : "手动选择"}`}
        title={brief.title}
        brief={
          brief.interview?.role
            ? `线上面试 · ${brief.interview.role}`
            : brief.purpose
        }
        editable={state === "idle"}
        preferencesSaved={preferencesSaved}
        onCapture={() => {
          setSettingsTab("recording");
          setSettingsOpen(true);
        }}
        onOutcome={() => {
          setSettingsTab("outcome");
          setSettingsOpen(true);
        }}
        onBrief={
          flowReady
            ? () => {
                setSettingsTab("preparation");
                setSettingsOpen(true);
              }
            : undefined
        }
      />
      {state === "idle" &&
        (!engines.find((engine) => engine.id === draftEngine)?.ready ||
          (captureSource !== "browser" && !nativeCapability?.available)) && (
          <p className="capture-warning">
            上次选择的音源或模型暂不可用，请调整设置后开始。
          </p>
        )}
      {state === "finished" && (
        <section
          className="recording-finish-panel"
          aria-label="录音结束后的处理选择"
        >
          <h3>录音与现场关注已保存</h3>
          <p>
            本次留下 {notes.length} 个关注点：
            {notes.filter((n) => n.note_type === "question").length} 个问题、
            {notes.filter((n) => n.note_type === "action").length}{" "}
            个待办线索。进入会后整理后核对用途，再生成纪要。
          </p>
          <p>{coverageStatus}。选择下一步处理方式：</p>
          <button
            className="primary"
            disabled={completing}
            onClick={() => void processSaved(completionMode)}
          >
            进入会后整理
          </button>
          <p>
            将沿用上次选择：
            {completionMode === "quick"
              ? "快速完成文字稿"
              : completionMode === "full"
                ? "精细处理文字稿"
                : "先保留录音，暂不转写"}
            。
            {postGoal === "minutes" && completionMode !== "none"
              ? "文字稿完成后将自动生成 AI 纪要草稿。"
              : ""}
            原音与关注点已经保存。
          </p>
          <details>
            <summary>调整文字稿处理方式</summary>
            <div className="finish-options">
              {(
                [
                  {
                    mode: "quick",
                    title: "快速完成",
                    description: `复用 ${savedEngine === "qwen" ? "千问" : "Whisper"} 实时结果，补齐缺段与尾段；不区分发言人。`,
                  },
                  {
                    mode: "full",
                    title: "精细处理",
                    description: `使用 ${engineLabel} 重识别、时间对齐，按会议设置区分发言人。`,
                  },
                  {
                    mode: "none",
                    title: "先整理关注点",
                    description: "保留录音和简记，稍后在会议中启动转写。",
                  },
                ] as const
              ).map((choice) => (
                <button
                  key={choice.mode}
                  className={completionMode === choice.mode ? "selected" : ""}
                  disabled={completing}
                  onClick={() => void processSaved(choice.mode)}
                >
                  <strong>
                    {choice.title}
                    {completionMode === choice.mode ? " · 上次选择" : ""}
                  </strong>
                  <span>{choice.description}</span>
                </button>
              ))}
            </div>
          </details>
          {completing && <p role="status">正在打开会议…</p>}
        </section>
      )}
      {state === "idle" && runningSessions.length > 0 && (
        <div className="live-session-resume" role="status">
          <span>有正在运行的原生录音，后端识别仍在继续。</span>
          {runningSessions.map((entry) => (
            <button
              key={entry.recording.id}
              onClick={() => attachRunning(entry)}
            >
              继续查看 {clock(entry.recording.duration)}
            </button>
          ))}
        </div>
      )}
      <div
        className={`workbench-history-wrapper${state === "finished" ? (historyOpen ? " ended open" : " ended") : ""}`}
      >
        {state === "finished" && (
          <button
            className="history-toggle"
            aria-expanded={historyOpen}
            onClick={() => setHistoryOpen((value) => !value)}
          >
            {historyOpen ? "收起" : "查看"}本次实时文字与简记（{notes.length}{" "}
            条简记）
          </button>
        )}
        {(state !== "finished" || historyOpen) && (
          <div className="recording-workspace">
            <section className="live-draft">
              <header>
                <h3>实时草稿</h3>
                <span>{draftStatus}</span>
              </header>
              {draftEngine === "qwen" && streamEnabled && (
                <div className="live-correction-bar">
                  <small>
                    {liveCorrection.status ||
                      (contextEnabled
                        ? "上下文辅助识别已开启 · 临时尾部可修订"
                        : "临时尾部可修订")}
                  </small>
                  <button
                    className="text-btn"
                    disabled={!correctionSessionId}
                    onClick={() => setCorrectionReviewOpen(true)}
                  >
                    纠错记录
                    {liveCorrection.corrections.filter(
                      (c) => c.state === "suggested",
                    ).length
                      ? ` · ${liveCorrection.corrections.filter((c) => c.state === "suggested").length} 待确认`
                      : ""}
                  </button>
                </div>
              )}
              <LiveDraftList
                drafts={displayDrafts}
                canAddNote={recording}
                partial={partialDraft}
                emptyText={
                  responsiveMode && draftEngine === "qwen" && streamEnabled
                    ? "约 3 秒开始预览，后端根据识别速度调整更新节奏。"
                    : undefined
                }
                onAddNote={(draft) =>
                  setNotes((old) => [
                    ...old,
                    {
                      captured_at: draft.start,
                      note_type: "note",
                      raw_text: draft.text,
                      note_scope: "meeting",
                    },
                  ])
                }
              />
            </section>
            <section className="live-notes">
              <header>
                <h3>我的关注</h3>
                <span>
                  {notes.length ? `${notes.length} 条` : "原文永久保留"}
                </span>
              </header>
              <div className="note-capture-actions">
                <button
                  className="mark-now"
                  aria-label="标记此刻 Alt+M"
                  title="标记此刻（Alt+M）"
                  disabled={!recording}
                  onClick={() => markNow()}
                >
                  ⚑ 标记
                </button>
                <div className="attention-quick-actions">
                  {(
                    [
                      ["highlight", "标重点"],
                      ["question", "记问题"],
                      ["action", "记待办"],
                      ["note", "随手记"],
                    ] as const
                  ).map(([type, label]) => (
                    <button
                      key={type}
                      disabled={!recording}
                      onClick={() => {
                        if (noteText.trim()) {
                          setNotes((old) => [
                            ...old,
                            {
                              captured_at: Math.max(0, currentSeconds()),
                              note_type: type,
                              raw_text: noteText.trim(),
                              note_scope: "personal",
                            },
                          ]);
                          setNoteText("");
                        } else markNow(type);
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <p className="note-purpose-hint">
                随手记下关键词，系统会关联此刻的录音。
              </p>
              <div className="live-note-options" ref={noteOptionsRef}>
                <button
                  className="note-options-trigger"
                  disabled={!recording}
                  aria-expanded={noteOptionsOpen}
                  onClick={() => setNoteOptionsOpen(!noteOptionsOpen)}
                >
                  {noteLabels[noteType]} ·{" "}
                  {noteScope === "personal" ? "个人备注" : "会议内容"}{" "}
                  <span>调整用途</span>
                </button>
                {noteOptionsOpen && (
                  <div
                    className="note-options-popover"
                    role="group"
                    aria-label="简记标签与用途"
                  >
                    <select
                      aria-label="记录用途"
                      value={noteScope}
                      onChange={(e) =>
                        setNoteScope(e.target.value as typeof noteScope)
                      }
                      disabled={!recording}
                    >
                      <option value="personal">个人备注</option>
                      <option value="meeting">会议内容（待核对）</option>
                    </select>
                    <select
                      aria-label="简记类型"
                      value={noteType}
                      disabled={!recording}
                      onChange={(event) =>
                        setNoteType(event.target.value as MeetingNoteType)
                      }
                    >
                      {Object.entries(noteLabels).map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <button onClick={() => setNoteOptionsOpen(false)}>
                      完成
                    </button>
                  </div>
                )}
              </div>
              <div className="live-note-form">
                <textarea
                  aria-label="会议简记"
                  placeholder={
                    recording
                      ? "只记关键词，系统会保留对应会议时间…"
                      : "开始录音后可记下重点"
                  }
                  value={noteText}
                  disabled={!recording}
                  onChange={(event) => setNoteText(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      (event.metaKey || event.ctrlKey) &&
                      event.key === "Enter"
                    )
                      addNote();
                  }}
                />
                <button
                  disabled={!recording || !noteText.trim()}
                  onClick={addNote}
                >
                  保存关注点
                </button>
              </div>
              <div className={`live-note-list${!notes.length ? " empty" : ""}`}>
                {!notes.length && <p>⌘ / Ctrl + Enter 保存简记</p>}
                {notes.map((note, index) => (
                  <article key={`${note.captured_at}-${index}`}>
                    <button
                      className="text-btn danger"
                      aria-label="删除简记"
                      disabled={!recording}
                      onClick={() =>
                        setNotes((old) => old.filter((_, i) => i !== index))
                      }
                    >
                      删除
                    </button>
                    <b>{clock(note.captured_at)}</b>
                    <span>{noteLabels[note.note_type]}</span>
                    <p>
                      {note.is_marker && !note.raw_text
                        ? "⚑ 时间标记 · 会后补充"
                        : note.raw_text}
                    </p>
                    <small>
                      {note.note_scope === "meeting"
                        ? "会议内容 · 待核对"
                        : "个人备注"}
                    </small>
                  </article>
                ))}
              </div>
            </section>
          </div>
        )}
      </div>
      <div className="workbench-bottom">
        <RecordingHealthPanel
          id={sessionRef.current?.id || ""}
          state={state}
          native={sessionRef.current?.source === "macos"}
          tracks={captureHealth}
          localFailure={localSaveFailedRef.current}
          memoryOnly={failedChunksRef.current.length}
          onDiagnostics={() =>
            void exportDiagnostics(sessionRef.current!.id).catch((error) =>
              toast(error.message),
            )
          }
        />
        <div className="recording-awake-status">
          <label>
            <input
              type="checkbox"
              aria-label="录音时保持亮屏"
              checked={keepAwake}
              disabled={state === "requesting" || state === "saving"}
              onChange={(event) => setKeepAwake(event.target.checked)}
            />{" "}
            录音时保持亮屏
          </label>
          <span role="status">{awakeStatus}</span>
        </div>
        <RecordingWaveform
          tracks={
            captureSource === "browser"
              ? [
                  {
                    label: "麦克风",
                    levels: microphoneLevels,
                    color: "#263752",
                  },
                ]
              : [
                  { label: "电脑声音", levels: systemLevels, color: "#3968e8" },
                  ...(captureSource === "macos-both"
                    ? [
                        {
                          label: "麦克风",
                          levels: microphoneLevels,
                          color: "#263752",
                        },
                      ]
                    : []),
                ]
          }
          active={recording}
          paused={state === "paused"}
          status={saveStatus}
          onRetry={
            recording &&
            (failedChunksRef.current.length > 0 ||
              localSaveFailedRef.current ||
              /失败|未完成|错误|待重试/.test(saveStatus))
              ? () => void retrySync().catch((error) => toast(error.message))
              : undefined
          }
        />
        <button
          className="recording-inspector-trigger"
          onClick={() => setInspectorOpen(true)}
        >
          运行详情 <span>识别覆盖与保存状态 ↗</span>
        </button>
      </div>
      {correctionReviewOpen && correctionSessionId && (
        <LiveCorrectionReview
          recordingId={correctionSessionId}
          canDecide={state === "recording" || state === "paused"}
          drafts={drafts}
          profileId={correctionEnabled ? correctionProfileId : ""}
          onChange={liveCorrection.setCorrections}
          onClose={() => setCorrectionReviewOpen(false)}
        />
      )}
      {inspectorOpen && (
        <Modal
          className="recording-inspector"
          ariaLabel="录音运行详情"
          onClose={() => setInspectorOpen(false)}
        >
          <div className="search-heading">
            <div>
              <h2>运行详情</h2>
              <p>{coverageStatus}</p>
            </div>
            <button onClick={() => setInspectorOpen(false)}>关闭</button>
          </div>{" "}
          <p>{draftStatus}</p>
          {managedMode && <p>{backendLive.connection}</p>}
          {managedMode && decodeReport.decoder?.mode && (
            <p className="native-capture-hint">
              本轮
              {decodeReport.decoder.mode === "prefix"
                ? `保留 ${decodeReport.decoder.prefix_tokens || 0} 个前缀 token，续写尾部`
                : "完整识别窗口"}
              {decodeReport.decoder.feature_cache_hit
                ? " · 复用相同音频编码"
                : ""}
              {decodeReport.decoder.final_verified ? " · 确认前已完整复核" : ""}
              <br />
              累计前缀续写 {decodeReport.decoder_totals?.prefix_rounds || 0} 次
              · 编码缓存命中 {decodeReport.decoder_totals?.feature_hits || 0} 次
              · 回退 {decodeReport.decoder_totals?.fallbacks || 0} 次
            </p>
          )}
          {draftEngine === "qwen" && streamEnabled && (
            <p className="native-capture-hint">
              {liveMetrics.firstMs !== null
                ? `本次请求首字 ${(liveMetrics.firstMs / 1000).toFixed(2)}s · `
                : ""}
              处理 {(liveMetrics.processingMs / 1000).toFixed(2)}s ·
              临时尾部更新 {liveMetrics.revisions} 次（不含逐字追加）
              {liveMetrics.rtf !== null
                ? ` · RTF ${liveMetrics.rtf.toFixed(2)}`
                : ""}
              {liveMetrics.lagMs !== null
                ? ` · 确认滞后 ${(liveMetrics.lagMs / 1000).toFixed(1)}s`
                : ""}
            </p>
          )}
          {captureSource !== "browser" && captureHealth && (
            <div className="capture-health" role="status" aria-live="polite">
              {captureHealth.tracks.map((track) => (
                <p
                  key={track.name}
                  className={
                    ["no_data", "quiet", "stalled"].includes(track.state)
                      ? "capture-warning"
                      : ""
                  }
                >
                  <strong>
                    {track.name === "system" ? "电脑声音" : "麦克风"}
                  </strong>{" "}
                  ·{" "}
                  {
                    {
                      receiving: "正在接收音频",
                      waiting: "等待音频",
                      paused: "已暂停",
                      inactive: "已停止",
                      stalled: "采集状态超过 10 秒未更新，已有音轨保留",
                      no_data: "超过 10 秒未收到音频数据，请检查声音来源和权限",
                      quiet:
                        "连续 10 秒声音很弱，请确认会议正在播放或麦克风正常",
                    }[track.state]
                  }{" "}
                  · 已保存 {track.saved_seconds.toFixed(1)}s
                </p>
              ))}
              <small>
                静音提醒不会停止录音。保存时长包含时间线中的静音间隔，不能代表有效发言时长。
              </small>
            </div>
          )}
          {coverageDetails &&
            (coverageDetails.gaps.length > 0 ||
              coverageDetails.recent_failures.length > 0) && (
              <details className="capture-health">
                <summary>查看未覆盖范围及失败记录</summary>
                {coverageDetails.gaps.slice(0, 20).map((gap, index) => (
                  <p key={`gap-${index}`}>
                    {clock(gap.start)}–{clock(gap.end)} · 快速完成时补识别
                  </p>
                ))}
                {coverageDetails.recent_failures.map((attempt, index) => (
                  <p key={`attempt-${index}`}>
                    {clock(attempt.start)}–{clock(attempt.end)} ·{" "}
                    {attempt.state === "running"
                      ? "识别中"
                      : attempt.state === "preview"
                        ? "临时结果，未确认"
                        : "失败"}{" "}
                    {attempt.error}
                  </p>
                ))}
                <small>
                  历史失败尝试会保留；已重试成功的范围不会再次补识别。最多显示
                  20 个缺段及最近 50 条失败/进行中记录。
                </small>
              </details>
            )}
        </Modal>
      )}
      {settingsOpen && (
        <Modal
          className="recording-settings-dialog recording-config-panel"
          ariaLabel="本次录音配置"
          onClose={() => setSettingsOpen(false)}
        >
          <div className="search-heading">
            <div>
              <span className="config-panel-eyebrow">本次会议</span>
              <h2>录音方案</h2>
              <p>调整当前方案，主工作区保持原位。</p>
            </div>
            <button onClick={() => setSettingsOpen(false)}>完成</button>
          </div>
          <div className="settings-tabs" aria-label="录音配置分类">
            {(
              [
                ["recording", "音源与权限"],
                ["model", "实时识别"],
                ["outcome", "会后产物"],
                ...(flowReady ? [["preparation", "会议准备"]] : []),
              ] as [typeof settingsTab, string][]
            ).map(([tab, label]) => (
              <button
                key={tab}
                aria-pressed={settingsTab === tab}
                onClick={() => setSettingsTab(tab)}
              >
                {label}
              </button>
            ))}
          </div>
          {settingsTab === "outcome" && (
            <section className="config-panel-section">
              <h3>结束录音后，你希望得到什么？</h3>
              <p className="config-panel-description">
                原音与简记先保存，再按此方案继续整理。
              </p>{" "}
              <div className="recording-plan-body">
                <label>
                  交付目标
                  <select
                    aria-label="录音交付目标"
                    value={postGoal}
                    onChange={(e) => {
                      setPostGoal(e.target.value as "transcript" | "minutes");
                      if (
                        e.target.value === "minutes" &&
                        completionMode === "none"
                      )
                        setCompletionMode("quick");
                    }}
                  >
                    <option value="transcript">文字稿／录音资料</option>
                    <option
                      value="minutes"
                      disabled={
                        !minutesModel || !environment?.auto_minutes_ready
                      }
                    >
                      文字稿＋AI 纪要（含待办候选）
                    </option>
                  </select>
                </label>
                {postGoal === "minutes" && (
                  <label>
                    纪要模型
                    <select
                      aria-label="自动纪要模型"
                      value={minutesModel}
                      onChange={(e) => onMinutesModel(e.target.value)}
                    >
                      {environment?.analysis.profiles.length
                        ? environment.analysis.profiles.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name} ·{" "}
                              {p.provider_type === "cloud" ? "云端" : "本地"}
                            </option>
                          ))
                        : environment?.analysis.models.map((m) => (
                            <option key={m}>{m}</option>
                          ))}
                    </select>
                  </label>
                )}
                {postGoal === "minutes" &&
                  (!minutesModel || !environment?.auto_minutes_ready) && (
                    <p role="alert">
                      自动纪要模型或后台服务尚未就绪，请调整模型或选择仅文字稿。
                    </p>
                  )}
                <label>
                  结束后处理
                  <select
                    aria-label="结束后处理"
                    value={completionMode}
                    onChange={(e) => {
                      setCompletionMode(e.target.value as CompletionMode);
                      if (e.target.value === "none") setPostGoal("transcript");
                    }}
                  >
                    <option value="quick">
                      快速完成文字稿（复用
                      {draftEngine === "qwen" ? "千问" : "Whisper"}实时结果）
                    </option>
                    <option value="full">
                      精细处理文字稿（{engineLabel}）
                    </option>
                    <option value="none">仅保存录音和关注点</option>
                  </select>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={autoProcess}
                    onChange={(e) => setAutoProcess(e.target.checked)}
                  />
                  结束后自动进入会后整理
                </label>
                <p>
                  先保存录音，再完成文字稿；选择 AI
                  纪要时随后排队生成草稿，使用上次纪要模板与详略，行动项仍需核对。云端模型会在排队前再次确认；异常恢复的录音默认只处理文字稿。
                </p>
              </div>
            </section>
          )}
          {settingsTab === "preparation" && flowReady && (
            <section className="config-panel-section">
              <h3>给这场会议一点背景</h3>
              <p className="config-panel-description">
                可选填写，帮助会后整理理解本次目标。
                {briefSaved ? "已保存到本机。" : "正在保存…"}
              </p>{" "}
              <MeetingBriefForm
                value={brief}
                onChange={setBrief}
                meetings={meetings}
                disabled={!briefReady}
              />
            </section>
          )}
          {settingsTab === "recording" && (
            <section>
              <label className="capture-source">
                录音来源
                <select
                  aria-label="录音来源"
                  value={captureSource}
                  disabled={state !== "idle"}
                  onChange={(e) =>
                    setCaptureSource(e.target.value as typeof captureSource)
                  }
                >
                  <option value="browser">浏览器麦克风（支持实时草稿）</option>
                  <option
                    value="macos-both"
                    disabled={!nativeCapability?.available}
                  >
                    macOS 电脑声音 + 麦克风
                  </option>
                  <option
                    value="macos-system"
                    disabled={!nativeCapability?.available}
                  >
                    macOS 仅电脑声音
                  </option>
                </select>
              </label>
              {captureSource !== "browser" && (
                <p className="native-capture-hint">
                  首次使用需允许
                  macOS“屏幕与系统音频录制”权限，联合采集还需麦克风权限。仅保存音频，不保存屏幕画面。建议戴耳机，避免麦克风重复收录扬声器声音。所选引擎生成实时草稿，停止后生成正式文字稿。
                </p>
              )}
              {state === "idle" && (
                <div className="capture-preflight">
                  <span>
                    原生采集器：
                    {nativeCapability?.available ? "已就绪" : "未就绪"} ·
                    系统音频权限：
                    {nativeCapability?.screen_permission
                      ? "已授权"
                      : "待授权"}{" "}
                    · 麦克风权限：
                    {nativeCapability?.microphone_permission
                      ? "已授权"
                      : "待授权"}
                  </span>
                  <button
                    disabled={checkingCapture}
                    onClick={() => void checkCapture()}
                  >
                    {checkingCapture ? "检查中…" : "重新检查"}
                  </button>
                  <small>
                    在系统设置中授权后重新检查；开始原生录音时会请求所需权限。
                  </small>
                </div>
              )}
              {!nativeCapability?.available && (
                <p className="native-capture-hint">
                  系统音频采集器尚未就绪，可运行 bash scripts/build_capture.sh
                  构建。
                </p>
              )}
            </section>
          )}
          {settingsTab === "model" && (
            <section>
              <button
                onClick={() => {
                  setSettingsOpen(false);
                  onModels();
                }}
              >
                分析 LLM 与本机服务配置
              </button>
              <label className="capture-source">
                实时草稿引擎
                <select
                  aria-label="实时草稿引擎"
                  value={draftEngine}
                  disabled={state !== "idle"}
                  onChange={(event) =>
                    setDraftEngine(event.target.value as "whisper" | "qwen")
                  }
                >
                  {(["whisper", "qwen"] as const).map((id) => (
                    <option
                      key={id}
                      value={id}
                      disabled={
                        !engines.find((engine) => engine.id === id)?.ready
                      }
                    >
                      {id === "qwen" ? "千问 Qwen-ASR" : "Whisper"}
                      {!engines.find((engine) => engine.id === id)?.ready
                        ? "（未就绪）"
                        : ""}
                    </option>
                  ))}
                </select>
              </label>
              <p className="native-capture-hint">
                实时草稿与停止后的正式转写引擎独立选择。
                {draftEngine === "qwen"
                  ? `千问草稿按识别片段定位，不是精确字词时间。${streamEnabled ? "流式识别复用常驻模型" : "整批输出每段加载模型"}；正式转写仍保留精细对齐。`
                  : "Whisper 草稿保留句级时间戳。"}
              </p>
              {draftEngine === "qwen" && (
                <label className="capture-source">
                  <input
                    type="checkbox"
                    checked={streamEnabled}
                    disabled={state !== "idle"}
                    onChange={(event) => setStreamEnabled(event.target.checked)}
                  />
                  逐字推送
                  <small>
                    {responsiveMode
                      ? "短片段先预览，再用后续上下文修订。"
                      : "收集约 15 秒音频后，文字逐步显示。"}
                    未确认文字不计入覆盖，也不能转为简记。关闭后整批显示。
                  </small>
                </label>
              )}
              {draftEngine === "qwen" && streamEnabled && (
                <>
                  <label className="capture-source">
                    <input
                      type="checkbox"
                      checked={responsiveMode}
                      disabled={state !== "idle" || !modelStatus}
                      onChange={(event) =>
                        setResponsiveMode(event.target.checked)
                      }
                    />
                    短片段与尾部修订（试用）
                  </label>
                  <p className="native-capture-hint">
                    {responsiveMode
                      ? "约 3 秒开始预览，后端自适应调度；静音、暂停或最长 15 秒后确认。能量静音检测可能提前分段，建议与标准模式对比。"
                      : "标准模式：约 15 秒一段，完成后确认。"}
                  </p>
                  {responsiveMode && (
                    <label className="capture-source">
                      <input
                        type="checkbox"
                        aria-label="前缀续写与尾部回退"
                        checked={prefixEnabled}
                        disabled={state !== "idle"}
                        onChange={(event) =>
                          setPrefixEnabled(event.target.checked)
                        }
                      />
                      前缀续写与尾部回退（试用）
                      <small>
                        连续预览稳定后保留前缀，重新生成尾部；确认时完整复核。音频相同可复用编码，音频增长后重新编码。
                      </small>
                    </label>
                  )}
                  <div className="capture-preflight">
                    <span>
                      Qwen 模型：
                      {modelStatus?.loading
                        ? "加载中"
                        : modelStatus?.loaded
                          ? "已常驻"
                          : "未加载"}{" "}
                      ·{" "}
                      {modelStatus?.busy
                        ? "识别中"
                        : modelStatus?.formal_job_running
                          ? "正式任务处理中"
                          : "空闲"}
                      {modelStatus?.peak_memory_gb
                        ? ` · 峰值 ${modelStatus.peak_memory_gb.toFixed(1)} GB`
                        : ""}
                    </span>
                    <button
                      disabled={
                        state !== "idle" || modelActionBusy || modelStatus?.busy
                      }
                      onClick={() => void controlModel("warm")}
                    >
                      预加载模型
                    </button>
                    <button
                      disabled={
                        !modelStatus?.loaded ||
                        modelActionBusy ||
                        modelStatus?.busy ||
                        !["idle", "paused"].includes(state)
                      }
                      onClick={() => void controlModel("release")}
                    >
                      释放模型
                    </button>
                    <small>
                      同一进程复用模型，空闲约 3
                      分钟自动释放；正式处理任务优先释放常驻模型。取消正在识别的窗口会重建进程。
                    </small>
                  </div>
                </>
              )}
              {draftEngine === "qwen" && streamEnabled && (
                <fieldset
                  className="live-context-settings"
                  disabled={state !== "idle"}
                >
                  <legend>上下文与纠错</legend>
                  <label>
                    <input
                      type="checkbox"
                      checked={contextEnabled}
                      onChange={(e) => setContextEnabled(e.target.checked)}
                    />{" "}
                    使用前文与会议准备辅助识别
                  </label>
                  <label>
                    会议术语／姓名
                    <textarea
                      aria-label="识别参考术语"
                      rows={2}
                      maxLength={1000}
                      value={recognitionTerms}
                      placeholder="例如：听记、Qwen、项目代号、参会人姓名；仅作为词汇参考"
                      onChange={(e) => setRecognitionTerms(e.target.value)}
                    />
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={correctionEnabled}
                      onChange={(e) => setCorrectionEnabled(e.target.checked)}
                    />{" "}
                    已确认片段智能纠错（本地 LLM）
                  </label>
                  {correctionEnabled && (
                    <label>
                      纠错模型
                      <select
                        aria-label="实时纠错模型"
                        value={correctionProfileId}
                        onChange={(e) => setCorrectionProfileId(e.target.value)}
                      >
                        <option value="">请选择本地模型配置</option>
                        {environment?.analysis.profiles
                          .filter((p) => p.provider_type === "local")
                          .map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name} · {p.model}
                            </option>
                          ))}
                      </select>
                    </label>
                  )}
                  <small>
                    原始识别始终保存；内容修改需在“纠错记录”确认。校对失败不影响录音与转写。与
                    ASR 同时运行本地 LLM 可能增加延迟。
                  </small>
                </fieldset>
              )}
            </section>
          )}
          <p className="muted">
            设置自动保存在当前浏览器。录音时不能更换音源或识别模式；结束后再选择快速完成、精细处理或暂不处理。
          </p>
        </Modal>
      )}
    </Modal>
  );
}
