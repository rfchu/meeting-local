import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { encodeLiveWav } from "../notes/liveAudio";
import type { CaptureSource } from "./readiness";
export interface CaptureTest {
  id: string;
  state: string;
  duration: number;
  source: string;
  error: string;
  levels?: Partial<Record<"system" | "microphone", number>>;
}
export default function AudioCheck({
  source,
  engine,
  engineReady,
  onTested,
  onActiveChange,
}: {
  source: CaptureSource;
  engine: "qwen" | "whisper";
  engineReady: boolean;
  onTested?: () => void;
  onActiveChange?: (active: boolean) => void;
}) {
  const [state, setState] = useState("idle"),
    [seconds, setSeconds] = useState(0),
    [error, setError] = useState("");
  const [audio, setAudio] = useState(""),
    [text, setText] = useState(""),
    [level, setLevel] = useState(0);
  const [nativeLevels, setNativeLevels] = useState<CaptureTest["levels"]>();
  const generation = useRef(0),
    stream = useRef<MediaStream | null>(null),
    context = useRef<AudioContext | null>(null);
  const processor = useRef<ScriptProcessorNode | null>(null),
    timer = useRef<number | undefined>(undefined),
    probe = useRef("");
  const blob = useRef<Blob | null>(null),
    objectURL = useRef("");
  const request = useRef<AbortController | null>(null);
  const cleanup = () => {
    window.clearInterval(timer.current);
    window.clearTimeout(timer.current);
    timer.current = undefined;
    processor.current?.disconnect();
    processor.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    if (context.current) void context.current.close().catch(() => {});
    context.current = null;
    request.current?.abort();
    request.current = null;
    if (probe.current) {
      void api.deleteCaptureTest(probe.current).catch(() => {});
      probe.current = "";
    }
  };
  const clearAudio = () => {
    if (objectURL.current) URL.revokeObjectURL(objectURL.current);
    objectURL.current = "";
    blob.current = null;
  };
  useEffect(
    () => () => {
      generation.current++;
      cleanup();
      clearAudio();
      onActiveChange?.(false);
    },
    [],
  );
  const complete = (value: Blob) => {
    blob.current = value;
    objectURL.current = URL.createObjectURL(value);
    setAudio(objectURL.current);
    setState("completed");
    onTested?.();
  };
  const pending = useRef(false);
  const start = async () => {
    if (pending.current) return;
    pending.current = true;
    const token = ++generation.current;
    cleanup();
    clearAudio();
    setAudio("");
    setText("");
    setError("");
    setSeconds(0);
    setLevel(0);
    setNativeLevels(undefined);
    setState("requesting");
    try {
      if (source !== "browser") {
        const item = await api.startCaptureTest(source);
        if (token !== generation.current) {
          void api.deleteCaptureTest(item.id);
          return;
        }
        probe.current = item.id;
        setState(item.state === "recording" ? "recording" : "requesting");
        let polling = false;
        timer.current = window.setInterval(() => {
          if (polling) return;
          polling = true;
          void (async () => {
            const current = await api.captureTest(item.id);
            if (token !== generation.current) return;
            setSeconds(Math.floor(current.duration));
            setNativeLevels(current.levels);
            if (current.state === "recording") setState("recording");
            if (current.state === "failed")
              throw new Error(current.error || "试录失败，请检查权限");
            if (current.state === "completed") {
              window.clearInterval(timer.current);
              const value = await api.captureTestAudio(item.id);
              if (token !== generation.current) return;
              cleanup();
              complete(value);
            }
          })()
            .catch((e) => {
              if (token === generation.current) {
                cleanup();
                setState("failed");
                setError(e.message);
              }
            })
            .finally(() => {
              polling = false;
            });
        }, 600);
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error("当前浏览器不支持录音，请更换浏览器或选择原生音源");
      const input = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (token !== generation.current) {
        input.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = input;
      const ctx = new AudioContext();
      context.current = ctx;
      await ctx.resume();
      if (token !== generation.current) return;
      const node = ctx.createMediaStreamSource(input),
        capture = ctx.createScriptProcessor(4096, 1, 1),
        mute = ctx.createGain();
      mute.gain.value = 0;
      processor.current = capture;
      const frames: Float32Array[] = [];
      let count = 0;
      capture.onaudioprocess = (event) => {
        if (token !== generation.current) return;
        const values = new Float32Array(event.inputBuffer.getChannelData(0));
        const clipped = values.slice(
          0,
          Math.max(0, ctx.sampleRate * 10 - count),
        );
        if (clipped.length) {
          frames.push(clipped);
          count += clipped.length;
        }
        setLevel(
          Math.min(
            1,
            Math.sqrt(
              values.reduce((total, v) => total + v * v, 0) / values.length,
            ) * 8,
          ),
        );
      };
      node.connect(capture);
      capture.connect(mute);
      mute.connect(ctx.destination);
      setState("recording");
      const started = performance.now();
      timer.current = window.setInterval(() => {
        const elapsed = Math.min(
          10,
          Math.floor((performance.now() - started) / 1000),
        );
        setSeconds(elapsed);
        if (elapsed >= 10) {
          if (!count) {
            cleanup();
            setState("failed");
            setError("未收到音频，请检查麦克风或保持页面可见后重试");
            return;
          }
          const encoded = encodeLiveWav(frames, ctx.sampleRate);
          cleanup();
          complete(encoded.blob);
        }
      }, 200);
      input.getTracks().forEach((track) => {
        track.onended = () => {
          if (token === generation.current && stream.current) {
            generation.current++;
            cleanup();
            setState("failed");
            setError("音频输入已中断，请重新试录");
          }
        };
      });
    } catch (e) {
      if (token === generation.current) {
        cleanup();
        setState("failed");
        setError(
          (e as Error).name === "NotAllowedError"
            ? "麦克风权限未允许，请在浏览器设置中开启后重试"
            : (e as Error).message,
        );
      }
    } finally {
      if (token === generation.current) pending.current = false;
    }
  };
  const transcribe = async () => {
    if (!blob.current) return;
    const token = generation.current;
    setState("recognizing");
    setError("");
    const controller = new AbortController();
    request.current = controller;
    try {
      const result = await api.liveTranscribe(
        new File([blob.current], "audio-check.wav", { type: "audio/wav" }),
        0,
        engine,
        undefined,
        controller.signal,
      );
      if (token === generation.current) {
        setText(
          result.segments.map((s) => s.text).join("") ||
            "未识别到文字，请回放确认是否录到了说话声音。",
        );
        setState("completed");
      }
    } catch (e) {
      if (token === generation.current) {
        setError((e as Error).message);
        setState("completed");
      }
    }
  };
  const active = ["requesting", "recording", "recognizing"].includes(state);

  useEffect(() => {
    onActiveChange?.(active);
  }, [active, onActiveChange]);

  const tracks =
    source === "macos-both"
      ? [
          { key: "system", name: "电脑声音", icon: "💻" },
          { key: "microphone", name: "麦克风", icon: "🎙️" },
        ]
      : source === "macos-system"
        ? [{ key: "system", name: "电脑声音", icon: "💻" }]
        : source === "macos-microphone"
          ? [{ key: "microphone", name: "麦克风", icon: "🎙️" }]
          : [{ key: "browser", name: "浏览器麦克风", icon: "🌐" }];

  return (
    <div className="audio-check-suite" aria-label="音源试录与电平监听">
      {/* 头部状态与操作 */}
      <div className="audio-check-header">
        <div className="audio-check-title-group">
          <div className="audio-check-indicator-row">
            <span
              className={`audio-check-dot ${
                state === "recording"
                  ? "recording"
                  : state === "recognizing"
                    ? "recognizing"
                    : state === "completed"
                      ? "completed"
                      : state === "failed"
                        ? "failed"
                        : "idle"
              }`}
            />
            <span className="audio-check-heading">
              {state === "recording"
                ? `正在试录 (${seconds}/10 秒)`
                : state === "requesting"
                  ? "正在等待权限…"
                  : state === "recognizing"
                    ? "正在识别测试录音…"
                    : state === "completed"
                      ? "试录完成，可回放试听"
                      : state === "failed"
                        ? "试录异常"
                        : "信号监听与试录"}
            </span>
          </div>
          <small className="audio-check-sub">
            {state === "recording"
              ? source === "macos-both"
                ? "请播放电脑声音，并对着麦克风说话观察跳动…"
                : source === "macos-system"
                  ? "请播放一段电脑声音观察电平跳动…"
                  : "请对着麦克风说话观察电平跳动…"
              : state === "completed"
                ? "试听确认声音清晰后，即可在右上角正式起录"
                : "会前试录 10 秒样本并回听，提前确认收音正常"}
          </small>
        </div>

        <div className="audio-check-actions">
          {!active && (
            <button
              type="button"
              className="btn-audio-check-primary"
              onClick={() => void start()}
            >
              {audio ? "🔄 重新试录" : "🎙️ 开始试录 (10s)"}
            </button>
          )}
          {active && (
            <button
              type="button"
              className="btn-audio-check-danger"
              onClick={() => {
                generation.current++;
                pending.current = false;
                cleanup();
                setState(audio ? "completed" : "idle");
              }}
            >
              ⏹️ 停止 / 取消
            </button>
          )}
        </div>
      </div>

      {/* 音轨与电平条 */}
      <div className="audio-check-tracks">
        {tracks.map((track) => {
          let rms: number | undefined;
          let levelRatio = 0;
          let statusText = "待试音";
          let statusClass = "idle";

          if (source !== "browser") {
            rms = nativeLevels?.[track.key as "system" | "microphone"];
            if (rms !== undefined) {
              levelRatio = Math.min(1, Math.sqrt(rms) * 2.4);
              const isVoice = rms >= 0.002;
              statusText = isVoice ? "有输入" : "等待输入";
              statusClass = isVoice ? "voice" : "quiet";
            } else if (state === "recording") {
              statusText = "检测中…";
              statusClass = "detecting";
            }
          } else {
            // browser
            if (state === "recording") {
              levelRatio = level;
              const isVoice = level > 0.05;
              statusText = isVoice ? "有输入" : "等待输入";
              statusClass = isVoice ? "voice" : "quiet";
            } else if (state === "completed") {
              statusText = "已采录";
              statusClass = "voice";
              levelRatio = 0.35;
            }
          }

          return (
            <div className="audio-check-track-item" key={track.key}>
              <div className="audio-track-info">
                <span className="audio-track-icon">{track.icon}</span>
                <span className="audio-track-name">{track.name}</span>
              </div>
              <div className="audio-track-bar-container">
                <div
                  className={`audio-track-bar-fill ${levelRatio > 0.02 ? "active" : ""}`}
                  style={{ width: `${Math.round(levelRatio * 100)}%` }}
                />
              </div>
              <span className={`audio-track-status-pill ${statusClass}`}>
                {statusText}
              </span>
            </div>
          );
        })}
      </div>

      {/* 试录完成后的回放与文字识别 */}
      {audio && (
        <div className="audio-check-player-section">
          <div className="audio-check-player-row">
            <audio controls src={audio} className="audio-check-native-player" />
            <button
              type="button"
              className="btn-audio-check-transcribe"
              disabled={active || !engineReady}
              onClick={() => void transcribe()}
              title={engineReady ? "使用所选引擎转写这段测试音频" : "识别引擎尚未就绪"}
            >
              {state === "recognizing" ? "正在识别…" : "📝 验证转写"}
            </button>
          </div>
          {text && (
            <div className="audio-check-result-box">
              <span className="result-tag">识别结果</span>
              <p className="result-content">{text}</p>
            </div>
          )}
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="audio-check-error-banner" role="alert">
          <span className="error-icon">⚠️</span>
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
