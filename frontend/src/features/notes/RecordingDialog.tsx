import { useEffect, useRef, useState } from "react";
import type { MeetingNoteInput, MeetingNoteType } from "../../types";
import { clock } from "../../utils";
import Modal from "../../components/Modal";

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
    [notes, setNotes] = useState<MeetingNoteInput[]>([]);
  const recorderRef = useRef<MediaRecorder | null>(null),
    streamRef = useRef<MediaStream | null>(null),
    chunksRef = useRef<Blob[]>([]),
    activeSinceRef = useRef(0),
    accruedRef = useRef(0),
    notesRef = useRef(notes);
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
    () => () => streamRef.current?.getTracks().forEach((track) => track.stop()),
    [],
  );

  const start = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      toast("当前浏览器不支持麦克风录音");
      return;
    }
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
      accruedRef.current = 0;
      activeSinceRef.current = performance.now();
      recorder.start(1000);
      setState("recording");
    } catch (error) {
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
    setState("paused");
  };
  const resume = () => {
    const recorder = recorderRef.current;
    if (!recorder || state !== "paused") return;
    activeSinceRef.current = performance.now();
    recorder.resume();
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
    setState("saving");
    const stopped = new Promise<void>((resolve) =>
      recorder.addEventListener("stop", () => resolve(), { once: true }),
    );
    recorder.stop();
    await stopped;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    const mimeType = recorder.mimeType || "audio/webm";
    const extension = mimeType.includes("mp4") ? "m4a" : "webm";
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const file = new File(chunksRef.current, `会议录音-${stamp}.${extension}`, {
      type: mimeType,
    });
    try {
      await onComplete(file, notesRef.current);
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
        当前版本录制麦克风音频。停止后将保存到本机，并自动使用 {engineLabel}
        生成高质量文字稿。
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
      <section className="live-notes">
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
    </Modal>
  );
}
