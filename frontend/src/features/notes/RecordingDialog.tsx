import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type {
  LiveDraftSegment,
  MeetingNoteInput,
  MeetingNoteType,
} from "../../types";
import { clock } from "../../utils";
import Modal from "../../components/Modal";
import { encodeLiveWav } from "./liveAudio";

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
  onClose,
  onComplete,
  toast,
}: {
  engineLabel: string;
  onClose: () => void;
  onComplete: (file: File, notes: MeetingNoteInput[]) => Promise<void>;
  toast: (text: string) => void;
}) {
  const [state, setState] = useState<
      "idle" | "requesting" | "recording" | "paused" | "saving"
    >("idle"),
    [elapsed, setElapsed] = useState(0),
    [noteText, setNoteText] = useState(""),
    [noteType, setNoteType] = useState<MeetingNoteType>("note"),
    [notes, setNotes] = useState<MeetingNoteInput[]>([]),
    [drafts, setDrafts] = useState<LiveDraftSegment[]>([]),
    [draftStatus, setDraftStatus] = useState("开始录音后自动生成");
  const recorderRef = useRef<MediaRecorder | null>(null),
    streamRef = useRef<MediaStream | null>(null),
    chunksRef = useRef<Blob[]>([]),
    activeSinceRef = useRef(0),
    accruedRef = useRef(0),
    notesRef = useRef(notes),
    stateRef = useRef(state),
    audioContextRef = useRef<AudioContext | null>(null),
    audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null),
    audioProcessorRef = useRef<ScriptProcessorNode | null>(null),
    silentGainRef = useRef<GainNode | null>(null),
    pcmFramesRef = useRef<Float32Array[]>([]),
    pcmSamplesRef = useRef(0),
    draftOffsetRef = useRef(0),
    draftQueueRef = useRef<Promise<void>>(Promise.resolve()),
    draftPendingRef = useRef(0),
    draftEnabledRef = useRef(true);
  notesRef.current = notes;

  const currentSeconds = () =>
    accruedRef.current +
    (state === "recording"
      ? (performance.now() - activeSinceRef.current) / 1000
      : 0);

  useEffect(() => {
    if (state !== "recording") return;
    const timer = window.setInterval(() => setElapsed(currentSeconds()), 250);
    return () => window.clearInterval(timer);
  }, [state]);
  useEffect(
    () => () => {
      draftEnabledRef.current = false;
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
    if (!context || sampleCount < context.sampleRate * (final ? 0.5 : 12))
      return draftQueueRef.current;
    const frames = pcmFramesRef.current;
    pcmFramesRef.current = [];
    pcmSamplesRef.current = 0;
    const encoded = encodeLiveWav(frames, context.sampleRate),
      offset = draftOffsetRef.current;
    draftOffsetRef.current += encoded.duration;
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
        const result = await api.liveTranscribe(file, offset);
        if (!draftEnabledRef.current) return;
        if (result.segments.length)
          setDrafts((old) => [...old, ...result.segments]);
        setDraftStatus(
          result.segments.length
            ? `已更新 · ${result.processing_ms / 1000}s`
            : "这一段没有识别到语音",
        );
      } catch (error) {
        if (draftEnabledRef.current)
          setDraftStatus(`暂时不可用 · ${(error as Error).message}`);
      } finally {
        draftPendingRef.current--;
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
      pcmFramesRef.current.push(frame);
      pcmSamplesRef.current += frame.length;
      // Bound backlog while inference is slower than capture. Final audio is retained.
      while (pcmSamplesRef.current > context.sampleRate * 30) {
        const dropped = pcmFramesRef.current.shift();
        if (!dropped) break;
        pcmSamplesRef.current -= dropped.length;
        draftOffsetRef.current += dropped.length / context.sampleRate;
        setDraftStatus("识别积压，优先跟随最近语音；完整录音已保留");
      }
      if (
        pcmSamplesRef.current >= context.sampleRate * 15 &&
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
      const mimeType = supportedMime();
      const recorder = new MediaRecorder(stream, {
        ...(mimeType ? { mimeType } : {}),
        audioBitsPerSecond: 128000,
      });
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorderRef.current = recorder;
      streamRef.current = stream;
      pcmFramesRef.current = [];
      pcmSamplesRef.current = 0;
      draftOffsetRef.current = 0;
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
      recorder.start(1000);
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
    const recorder = recorderRef.current;
    if (!recorder || state !== "recording") return;
    accruedRef.current = currentSeconds();
    recorder.pause();
    setElapsed(accruedRef.current);
    stateRef.current = "paused";
    setState("paused");
  };
  const resume = () => {
    const recorder = recorderRef.current;
    if (!recorder || state !== "paused") return;
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
    const recorder = recorderRef.current;
    if (!recorder || !["recording", "paused"].includes(state)) return;
    if (state === "recording") accruedRef.current = currentSeconds();
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
    await closeDraftCapture();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    const mimeType = recorder.mimeType || "audio/webm";
    const extension = mimeType.includes("mp4") ? "m4a" : "webm";
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const file = new File(chunksRef.current, `会议录音-${stamp}.${extension}`, {
      type: mimeType,
    });
    const savedNotes = [...notesRef.current];
    if (noteText.trim())
      savedNotes.push({
        captured_at: accruedRef.current,
        note_type: noteType,
        raw_text: noteText.trim(),
      });
    try {
      await onComplete(file, savedNotes);
    } catch {
      setState("idle");
    }
  };

  const recording = state === "recording" || state === "paused";
  return (
    <Modal
      id="recordingDialog"
      className="recording-dialog"
      onClose={() => {
        if (!recording && state !== "saving") onClose();
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
        Whisper 在录音中生成增量临时草稿；停止后将保存原音，并使用 {engineLabel}
        重新生成高质量文字稿。
      </p>
      <div className="recording-controls">
        {state === "idle" && (
          <button className="primary" onClick={() => void start()}>
            ● 开始录音
          </button>
        )}
        {state === "requesting" && <button disabled>正在请求麦克风…</button>}
        {state === "recording" && <button onClick={pause}>暂停</button>}
        {state === "paused" && <button onClick={resume}>继续录音</button>}
        {recording && (
          <button className="recording-stop" onClick={() => void stop()}>
            ■ 停止并转写
          </button>
        )}
        {state === "saving" && <button disabled>正在保存并创建会议…</button>}
        {!recording && state !== "saving" && (
          <button onClick={onClose}>取消</button>
        )}
      </div>
      <div className="recording-workspace">
        <section className="live-draft">
          <header>
            <h3>实时草稿</h3>
            <span>{draftStatus}</span>
          </header>
          <div className="live-draft-list">
            {!drafts.length && <p>每约 15 秒更新一次，仅作会议跟随参考。</p>}
            {drafts.map((draft, index) => (
              <article key={`${draft.start}-${index}`}>
                <b>{clock(draft.start)}</b>
                <p>{draft.text}</p>
                <button
                  className="text-btn"
                  onClick={() =>
                    setNotes((old) => [
                      ...old,
                      {
                        captured_at: draft.start,
                        note_type: "note",
                        raw_text: draft.text,
                      },
                    ])
                  }
                >
                  转为简记
                </button>
              </article>
            ))}
          </div>
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
