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
} from "../../types";
import { clock } from "../../utils";
import Modal from "../../components/Modal";
import { encodeLiveWav } from "./liveAudio";
import RecordingWaveform from "./RecordingWaveform";
import LiveDraftList from "./LiveDraftList";
import { responsivePlan, takeFrames } from "./rollingAudio";
import {
  bufferRecording,
  flushRecording,
  forgetRecording,
  recoverableRecordings,
  syncRecording,
} from "./recordingBuffer";

const noteLabels: Record<MeetingNoteType, string> = {
  note: "简记",
  highlight: "重点",
  question: "问题",
  decision: "决策",
  action: "待办",
};

const supportedMime = () =>
  ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find((value) =>
    MediaRecorder.isTypeSupported(value),
  ) || "";

export default function RecordingDialog({
  engineLabel,
  engines,
  onClose,
  onComplete,
  toast,
}: {
  engineLabel: string;
  engines: EngineInfo[];
  onClose: () => void;
  onComplete: (
    meetingId: string,
    options: { mode: "quick" | "full"; engine: "whisper" | "qwen" },
  ) => Promise<void>;
  toast: (text: string) => void;
}) {
  const [state, setState] = useState<
      "idle" | "requesting" | "recording" | "paused" | "saving" | "failed"
    >("idle"),
    [elapsed, setElapsed] = useState(0),
    [noteText, setNoteText] = useState(""),
    [noteType, setNoteType] = useState<MeetingNoteType>("note"),
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
    [responsiveMode, setResponsiveMode] = useState(false),
    [modelStatus, setModelStatus] = useState<Awaited<
      ReturnType<typeof api.liveModelStatus>
    > | null>(null),
    [modelActionBusy, setModelActionBusy] = useState(false),
    [streamEnabled, setStreamEnabled] = useState(true),
    [draftEngine, setDraftEngine] = useState<"whisper" | "qwen">("whisper"),
    [completionMode, setCompletionMode] = useState<"quick" | "full">("quick"),
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
    [recoveries, setRecoveries] = useState<RecordingSession[]>([]),
    [captureSource, setCaptureSource] = useState<
      "browser" | "macos-system" | "macos-both"
    >("browser"),
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
    noteDraftRef = useRef({ text: noteText, type: noteType }),
    stateRef = useRef(state),
    audioContextRef = useRef<AudioContext | null>(null),
    audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null),
    audioProcessorRef = useRef<ScriptProcessorNode | null>(null),
    silentGainRef = useRef<GainNode | null>(null),
    pcmFramesRef = useRef<Float32Array[]>([]),
    pcmSamplesRef = useRef(0),
    draftOffsetRef = useRef(0),
    nativeDraftOffsetRef = useRef(0),
    nativePreviewGoalRef = useRef<5 | 10 | 15>(5),
    priorPreviewRef = useRef<LiveDraftSegment | null>(null),
    lastVoiceSampleRef = useRef(0),
    lastPreviewSampleRef = useRef(0),
    nativeDraftBusyRef = useRef(false),
    streamAbortRef = useRef<AbortController | null>(null),
    draftQueueRef = useRef<Promise<void>>(Promise.resolve()),
    draftPendingRef = useRef(0),
    draftEnabledRef = useRef(true);
  notesRef.current = notes;
  noteDraftRef.current = { text: noteText, type: noteType };

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
  useEffect(() => {
    void checkCapture();
    void recoverableRecordings()
      .then(setRecoveries)
      .catch((error) => toast(error.message));
  }, []);
  useEffect(() => {
    if (
      !sessionRef.current ||
      sessionRef.current.source !== "macos" ||
      state === "idle" ||
      state === "failed"
    )
      return;
    let active = true,
      polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const status = await api.nativeStatus(sessionRef.current!.id);
        if (!active) return;
        accruedRef.current = status.duration;
        activeSinceRef.current = performance.now();
        setElapsed(status.duration);
        setCaptureHealth(status.health);
        setSystemLevels(status.system_levels || []);
        setMicrophoneLevels(status.microphone_levels || []);
        if (status.state === "recording" && stateRef.current === "requesting") {
          stateRef.current = "recording";
          setState("recording");
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
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [state, captureSource, nativeSessionId]);
  useEffect(() => {
    if (!nativeSessionId || captureSource === "browser" || !sessionRef.current)
      return;
    let active = true;
    const pollDraft = async () => {
      if (
        nativeDraftBusyRef.current ||
        !["recording", "paused"].includes(stateRef.current)
      )
        return;
      nativeDraftBusyRef.current = true;
      try {
        const controller = new AbortController();
        streamAbortRef.current = controller;
        const result =
          draftEngine === "qwen" && streamEnabled
            ? await api.nativeDraftStream(
                nativeSessionId,
                nativeDraftOffsetRef.current,
                (partial) => {
                  if (active && draftEnabledRef.current) {
                    updatePartial(partial);
                    setDraftStatus("Qwen 正在逐步生成 · 部分文字尚未确认");
                  }
                },
                controller.signal,
                responsiveMode,
                responsiveMode ? nativePreviewGoalRef.current : 15,
              )
            : await api.nativeDraft(
                nativeSessionId,
                nativeDraftOffsetRef.current,
                draftEngine,
              );
        if (!active || !draftEnabledRef.current) return;
        if (result.ready) {
          trackResult(result, result.next_offset);
          if ("confirmed" in result && result.confirmed === false) {
            if (result.segments[0]) setPartialDraft(result.segments[0]);
            nativePreviewGoalRef.current = Math.min(
              15,
              nativePreviewGoalRef.current + 5,
            ) as 5 | 10 | 15;
            setDraftStatus("临时尾部已更新 · 等待更多上下文或静音确认");
          } else {
            setPartialDraft(null);
            nativePreviewGoalRef.current = 5;
            nativeDraftOffsetRef.current = result.next_offset;
            if (result.segments.length)
              setDrafts((old) => [...old, ...result.segments]);
            setDraftStatus(
              result.skipped_seconds > 0
                ? `积压跳过 ${Math.round(result.skipped_seconds)}s，原音保留`
                : "本段已确认",
            );
          }
        }
      } catch (error) {
        if (active && draftEnabledRef.current) setPartialDraft(null);
        if (active && draftEnabledRef.current)
          setDraftStatus(
            `暂时不可用 · ${(error as Error).message}；正式转写不受影响`,
          );
      } finally {
        nativeDraftBusyRef.current = false;
      }
    };
    void pollDraft();
    const timer = window.setInterval(() => void pollDraft(), 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [
    nativeSessionId,
    captureSource,
    draftEngine,
    streamEnabled,
    responsiveMode,
  ]);
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
        setSaveStatus("浏览器已暂存，正在同步到本机…");
        try {
          await syncRecording(session.id);
          setSaveStatus("最近一批分段已同步到本机");
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
      setSaveStatus("分段已同步到本机");
    } catch (error) {
      setSaveStatus(`保存未完成：${(error as Error).message}`);
      throw error;
    }
  };
  const finish = async (
    session: RecordingSession,
    reuseEngine = draftEngine,
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
      await onComplete(meeting.id, {
        mode: completionMode,
        engine: reuseEngine,
      });
    } catch (error) {
      setState("failed");
      stateRef.current = "failed";
      toast(`录音已保留，请重试完成保存：${(error as Error).message}`);
    }
  };
  const recover = async (session: RecordingSession) => {
    if (
      !confirm(
        "请确认这段录音已停止。恢复会把已保存的分段整理为会议，缺失的最后一段无法补回。",
      )
    )
      return;
    setNotes(session.notes);
    notesRef.current = session.notes;
    accruedRef.current = session.duration;
    setElapsed(session.duration);
    await finish(session, session.draft_engine || draftEngine);
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
    if (!context) return draftQueueRef.current;
    const responsive =
      responsiveMode && draftEngine === "qwen" && streamEnabled;
    const plan = responsive
      ? responsivePlan(
          sampleCount,
          context.sampleRate,
          lastVoiceSampleRef.current,
          lastPreviewSampleRef.current,
          final,
        )
      : sampleCount >= context.sampleRate * (final ? 0.5 : 12)
        ? { count: sampleCount, confirmed: true }
        : null;
    if (!plan) return draftQueueRef.current;
    const { selected, remaining } = takeFrames(
      pcmFramesRef.current,
      plan.count,
    );
    if (plan.confirmed) {
      pcmFramesRef.current = remaining;
      pcmSamplesRef.current -= plan.count;
      lastVoiceSampleRef.current = Math.max(
        0,
        lastVoiceSampleRef.current - plan.count,
      );
      lastPreviewSampleRef.current = 0;
    } else lastPreviewSampleRef.current = plan.count;
    const encoded = encodeLiveWav(selected, context.sampleRate),
      offset = draftOffsetRef.current;
    if (plan.confirmed) draftOffsetRef.current += encoded.duration;
    const file = new File([encoded.blob], `live-${offset.toFixed(1)}.wav`, {
      type: "audio/wav",
    });
    draftPendingRef.current++;
    setDraftStatus("正在识别最近一段…");
    const task = draftQueueRef.current.then(async () => {
      if (!draftEnabledRef.current) {
        draftPendingRef.current--;
        return;
      }
      try {
        const controller = new AbortController();
        streamAbortRef.current = controller;
        const result =
          draftEngine === "qwen" && streamEnabled
            ? await api.liveTranscribeStream(
                file,
                offset,
                sessionRef.current?.id,
                (partial) => {
                  if (
                    draftEnabledRef.current &&
                    (!responsive ||
                      plan.confirmed ||
                      offset >= draftOffsetRef.current - 0.01)
                  ) {
                    updatePartial(partial);
                    setDraftStatus("Qwen 正在逐步生成 · 部分文字尚未确认");
                  }
                },
                controller.signal,
                plan.confirmed,
              )
            : await api.liveTranscribe(
                file,
                offset,
                draftEngine,
                sessionRef.current?.id,
              );
        if (!draftEnabledRef.current) return;
        trackResult(result, offset + encoded.duration);
        if ("confirmed" in result && result.confirmed === false) {
          if (offset >= draftOffsetRef.current - 0.01 && result.segments[0])
            setPartialDraft(result.segments[0]);
          setDraftStatus("临时尾部已更新 · 等待更多上下文或静音确认");
        } else {
          setPartialDraft(null);
          if (result.segments.length)
            setDrafts((old) => [...old, ...result.segments]);
          setDraftStatus(
            result.segments.length ? "本段已确认" : "这一段没有识别到语音",
          );
        }
      } catch (error) {
        if (draftEnabledRef.current) setPartialDraft(null);
        if (draftEnabledRef.current)
          setDraftStatus(`暂时不可用 · ${(error as Error).message}`);
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
        responsiveMode && draftEngine === "qwen" && streamEnabled
          ? draftPendingRef.current < 1 &&
            responsivePlan(
              pcmSamplesRef.current,
              context.sampleRate,
              lastVoiceSampleRef.current,
              lastPreviewSampleRef.current,
            ) !== null
          : pcmSamplesRef.current >= context.sampleRate * 15 &&
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
    setPartialDraft(null);
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
        const session = await api.nativeStart(captureSource === "macos-both");
        sessionRef.current = session;
        setNativeSessionId(session.id);
        await bufferRecording(session);
        accruedRef.current = 0;
        activeSinceRef.current = performance.now();
        draftEnabledRef.current = true;
        nativeDraftOffsetRef.current = 0;
        nativePreviewGoalRef.current = 5;
        setDraftStatus(
          `${draftEngine === "qwen" ? "Qwen-ASR" : "Whisper"} ${responsiveMode && streamEnabled ? "短片段预览与修订" : "约 15 秒识别"}，仅作会议跟随参考`,
        );
        setState("requesting");
      } catch (error) {
        if (sessionRef.current?.source === "macos")
          await api
            .nativeCommand(sessionRef.current.id, "stop")
            .catch(() => {});
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
      sessionRef.current = session;
      recorder.ondataavailable = (event) => {
        if (!event.data.size || !sessionRef.current) return;
        const index = sessionRef.current.chunk_count++;
        const session = metadata();
        if (session) void persist(session, index, event.data).catch(() => {});
      };
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
    stateRef.current = "recording";
    setState("recording");
  };
  const addNote = () => {
    const text = noteText.trim();
    if (!text) return;
    const capturedAt = Math.max(0, currentSeconds());
    setNotes((old) => [
      ...old,
      { captured_at: capturedAt, note_type: noteType, raw_text: text },
    ]);
    setNoteText("");
  };
  const stop = async () => {
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
          },
        ];
        setNotes(notesRef.current);
        setNoteText("");
      }
      try {
        const status = await api.nativeCommand(sessionRef.current.id, "stop");
        accruedRef.current = status.duration;
        const session = metadata();
        if (session) await finish(session);
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
    const stopped = new Promise<void>((resolve) =>
      recorder.addEventListener("stop", () => resolve(), { once: true }),
    );
    recorder.stop();
    await stopped;
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
      });
    notesRef.current = savedNotes;
    setNotes(savedNotes);
    setNoteText("");
    const session = metadata();
    if (session) await finish(session);
  };

  const recording = state === "recording" || state === "paused";
  return (
    <Modal
      id="recordingDialog"
      className="recording-dialog"
      onClose={() => {
        if (failedChunksRef.current.length || localSaveFailedRef.current)
          return toast("还有尚未落盘的录音分段，请先重试保存");
        if (!recording && state !== "saving" && state !== "requesting")
          onClose();
      }}
    >
      <div className="recording-header">
        <div>
          <div className="eyebrow">本机会议采集</div>
          <h2>录音与会议简记</h2>
        </div>
        <div className={`recording-clock${recording ? " active" : ""}`}>
          <span /> {clock(elapsed)}
        </div>
      </div>
      <p className="recording-intro">
        {captureSource === "browser"
          ? "原音每约 5 秒分段保存，浏览器暂存保护待同步内容。所选引擎生成实时草稿；"
          : "电脑声音由原生采集器保存，联合模式还会保留独立麦克风音轨；"}
        {completionMode === "quick"
          ? `停止后复用 ${draftEngine === "qwen" ? "Qwen-ASR" : "Whisper"} 实时结果，仅补识别缺段与尾段。`
          : `停止后使用 ${engineLabel} 重新转写完整录音。`}
      </p>
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
          <option value="macos-both" disabled={!nativeCapability?.available}>
            macOS 电脑声音 + 麦克风
          </option>
          <option value="macos-system" disabled={!nativeCapability?.available}>
            macOS 仅电脑声音
          </option>
        </select>
      </label>
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
              disabled={!engines.find((engine) => engine.id === id)?.ready}
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
              onChange={(event) => setResponsiveMode(event.target.checked)}
            />
            短片段与尾部修订（试用）
          </label>
          <p className="native-capture-hint">
            {responsiveMode
              ? "约 5 秒开始预览，更多上下文会修订同一临时尾部；静音、暂停或最长 15 秒后确认。能量静音检测可能提前分段，建议与标准模式对比。"
              : "标准模式：约 15 秒一段，完成后确认。"}
          </p>
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
      {captureSource !== "browser" && (
        <p className="native-capture-hint">
          首次使用需允许
          macOS“屏幕与系统音频录制”权限，联合采集还需麦克风权限。仅保存音频，不保存屏幕画面。建议戴耳机，避免麦克风重复收录扬声器声音。所选引擎生成实时草稿，停止后生成正式文字稿。
        </p>
      )}
      {state === "idle" && (
        <div className="capture-preflight">
          <span>
            原生采集器：{nativeCapability?.available ? "已就绪" : "未就绪"} ·
            系统音频权限：
            {nativeCapability?.screen_permission ? "已授权" : "待授权"} ·
            麦克风权限：
            {nativeCapability?.microphone_permission ? "已授权" : "待授权"}
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
          系统音频采集器尚未就绪，可运行 bash scripts/build_capture.sh 构建。
        </p>
      )}
      <RecordingWaveform
        tracks={
          captureSource === "browser"
            ? [{ label: "麦克风", levels: microphoneLevels, color: "#263752" }]
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
          recording
            ? () => void retrySync().catch((error) => toast(error.message))
            : undefined
        }
      />
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
              <strong>{track.name === "system" ? "电脑声音" : "麦克风"}</strong>{" "}
              ·{" "}
              {
                {
                  receiving: "正在接收音频",
                  waiting: "等待音频",
                  paused: "已暂停",
                  inactive: "已停止",
                  stalled: "采集状态超过 10 秒未更新，已有音轨保留",
                  no_data: "超过 10 秒未收到音频数据，请检查声音来源和权限",
                  quiet: "连续 10 秒声音很弱，请确认会议正在播放或麦克风正常",
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
      {state === "idle" && recoveries.length > 0 && (
        <section className="recording-recovery">
          <h3>发现未完成的录音</h3>
          <p>刷新或中断后，可恢复已保存的音频和简记为会议。</p>
          {recoveries.map((session) => (
            <div key={session.id}>
              <span>
                {new Date(session.created * 1000).toLocaleString()} ·{" "}
                {clock(session.duration)} ·{" "}
                {session.source === "macos"
                  ? "原生音轨"
                  : `${session.chunk_count} 段`}
              </span>
              <button onClick={() => void recover(session)}>恢复为会议</button>
            </div>
          ))}
        </section>
      )}
      <label className="capture-source">
        停止后处理
        <select
          aria-label="停止后处理"
          value={completionMode}
          disabled={state === "saving"}
          onChange={(event) =>
            setCompletionMode(event.target.value as "quick" | "full")
          }
        >
          <option value="quick">快速完成：复用草稿，补缺段与尾段</option>
          <option value="full">
            精细处理：完整重识别、对齐与所选发言人区分
          </option>
        </select>
      </label>
      <p className="native-capture-hint">
        {coverageStatus}
        <br />
        {completionMode === "quick"
          ? "快速完成使用实时草稿引擎，保留草稿时间精度，不执行发言人区分；文字稿生成后可再启动精细转写。未完成或失败的范围会从原音补识别。"
          : `精细处理使用 ${engineLabel} 重新转写完整录音。`}
      </p>
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
              历史失败尝试会保留；已重试成功的范围不会再次补识别。最多显示 20
              个缺段及最近 50 条失败/进行中记录。
            </small>
          </details>
        )}
      <div className="recording-controls">
        {state === "idle" && (
          <button className="primary" onClick={() => void start()}>
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
            ■ 停止并生成文字稿
          </button>
        )}
        {state === "saving" && <button disabled>正在保存并创建会议…</button>}
        {state === "failed" && (
          <button
            className="primary"
            onClick={() => {
              const session = metadata();
              if (session) void finish(session);
            }}
          >
            重试完成保存
          </button>
        )}
        {!recording && state !== "saving" && state !== "requesting" && (
          <button
            onClick={() => {
              if (failedChunksRef.current.length || localSaveFailedRef.current)
                return toast("还有未落盘的分段，请先重试保存");
              onClose();
            }}
          >
            {state === "failed" ? "稍后恢复" : "取消"}
          </button>
        )}
      </div>
      <div className="recording-workspace">
        <section className="live-draft">
          <header>
            <h3>实时草稿</h3>
            <span>{draftStatus}</span>
          </header>
          {draftEngine === "qwen" && streamEnabled && (
            <p className="native-capture-hint">
              {liveMetrics.firstMs !== null
                ? `本次请求首字 ${(liveMetrics.firstMs / 1000).toFixed(2)}s · `
                : ""}
              处理 {(liveMetrics.processingMs / 1000).toFixed(2)}s ·
              临时尾部更新 {liveMetrics.revisions} 次（不含逐字追加）
            </p>
          )}
          <LiveDraftList
            drafts={drafts}
            partial={partialDraft}
            emptyText={
              responsiveMode && draftEngine === "qwen" && streamEnabled
                ? "约 5 秒开始预览，临时尾部会随上下文修订。"
                : undefined
            }
            onAddNote={(draft) =>
              setNotes((old) => [
                ...old,
                {
                  captured_at: draft.start,
                  note_type: "note",
                  raw_text: draft.text,
                },
              ])
            }
          />
        </section>
        <section className="live-notes">
          <header>
            <h3>会议简记</h3>
            <span>{notes.length ? `${notes.length} 条` : "原文永久保留"}</span>
          </header>
          <div className="live-note-form">
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
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter")
                  addNote();
              }}
            />
            <button disabled={!recording || !noteText.trim()} onClick={addNote}>
              添加简记
            </button>
          </div>
          <div className="live-note-list">
            {!notes.length && (
              <p>简记会自动绑定当前录音时间，支持 ⌘/Ctrl + Enter 添加。</p>
            )}
            {notes.map((note, index) => (
              <article key={`${note.captured_at}-${index}`}>
                <button
                  className="text-btn danger"
                  aria-label="删除简记"
                  onClick={() =>
                    setNotes((old) => old.filter((_, i) => i !== index))
                  }
                >
                  删除
                </button>
                <b>{clock(note.captured_at)}</b>
                <span>{noteLabels[note.note_type]}</span>
                <p>{note.raw_text}</p>
              </article>
            ))}
          </div>
        </section>
      </div>
    </Modal>
  );
}
