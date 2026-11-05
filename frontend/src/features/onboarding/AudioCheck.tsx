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
}
export default function AudioCheck({
  source,
  engine,
  engineReady,
  onTested,
}: {
  source: CaptureSource;
  engine: "qwen" | "whisper";
  engineReady: boolean;
  onTested: () => void;
}) {
  const [state, setState] = useState("idle"),
    [seconds, setSeconds] = useState(0),
    [error, setError] = useState("");
  const [audio, setAudio] = useState(""),
    [text, setText] = useState(""),
    [level, setLevel] = useState(0);
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
    },
    [],
  );
  const complete = (value: Blob) => {
    blob.current = value;
    objectURL.current = URL.createObjectURL(value);
    setAudio(objectURL.current);
    setState("completed");
    onTested();
  };
  const start = async () => {
    const token = ++generation.current;
    cleanup();
    clearAudio();
    setAudio("");
    setText("");
    setError("");
    setSeconds(0);
    setLevel(0);
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
  return (
    <section className="audio-check" aria-label="十秒录音测试">
      <h3>十秒试录</h3>
      <p>
        请
        {source === "browser"
          ? "对着麦克风说一句话"
          : source === "macos-both"
            ? "播放一段电脑声音，并对着麦克风说一句话"
            : "播放一段电脑声音"}
        ，完成后回放检查。
      </p>
      <p className="muted">
        测试不创建会议，不进入恢复中心；本次试录只在本机处理，关闭后清理。
      </p>
      {state === "recording" && (
        <div role="status">
          <strong>{seconds}/10 秒</strong>
          <meter
            min={0}
            max={1}
            value={source === "browser" ? level : 0}
            aria-label="麦克风测试音量"
            hidden={source !== "browser"}
          />
          <span>
            {source !== "browser" ? "等待原生采集完成" : "请说话并观察音量"}
          </span>
        </div>
      )}
      {state === "requesting" && <p role="status">等待采集权限…</p>}
      <div className="guide-actions">
        <button disabled={active} onClick={() => void start()}>
          {audio ? "重新试录" : "开始十秒试录"}
        </button>
        {active && (
          <button
            onClick={() => {
              generation.current++;
              cleanup();
              setState(audio ? "completed" : "idle");
            }}
          >
            取消测试
          </button>
        )}
      </div>
      {audio && (
        <>
          <audio controls src={audio} aria-label="回放测试录音" />
          <p className="muted">
            听到预期音源后，才能确认采集正常；获取到音频不代表声音内容正确。
          </p>
          <button
            disabled={active || !engineReady}
            onClick={() => void transcribe()}
          >
            {state === "recognizing" ? "正在识别测试音频…" : "识别这段测试录音"}
          </button>
          {!engineReady && (
            <small>识别模型尚未就绪，仍可先回放确认录音。</small>
          )}
          {text && <p className="audio-test-result">测试识别结果：{text}</p>}
          {state === "completed" && !text && (
            <p className="muted">识别为可选步骤，不会创建或保存正式文字稿。</p>
          )}
        </>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
