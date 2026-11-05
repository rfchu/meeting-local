import { useEffect, useRef, useState } from "react";
import { endCountdown, QuietMonitor } from "./recordingEnd";

export function useRecordingEnd(
  active: boolean,
  recording: boolean,
  enabled: boolean,
  stop: () => Promise<void>,
) {
  const monitor = useRef(new QuietMonitor());
  const sampling = useRef(false);
  sampling.current = recording && enabled;
  const stopRef = useRef(stop);
  stopRef.current = stop;
  const [quiet, setQuiet] = useState(false);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [seconds, setSeconds] = useState(5);
  useEffect(() => {
    monitor.current.reset();
    setQuiet(false);
    setDeadline(null);
  }, [active, recording, enabled]);
  useEffect(() => {
    if (!active) return;
    const cancel = () => {
      if (document.visibilityState !== "visible") {
        setDeadline(null);
        monitor.current.reset();
        setQuiet(false);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && deadline !== null) {
        event.preventDefault();
        event.stopPropagation();
        setDeadline(null);
      }
    };
    document.addEventListener("visibilitychange", cancel);
    document.addEventListener("keydown", escape, true);
    const timer = window.setInterval(() => {
      const now = performance.now();
      setQuiet(
        recording &&
          enabled &&
          document.visibilityState === "visible" &&
          monitor.current.shouldRemind(now),
      );
      if (deadline === null) return;
      const next = endCountdown(
        deadline,
        now,
        document.visibilityState === "visible",
      );
      setSeconds(next.seconds);
      if (next.state !== "waiting") {
        setDeadline(null);
        if (next.state === "finish") void stopRef.current();
      }
    }, 250);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", cancel);
      document.removeEventListener("keydown", escape, true);
    };
  }, [active, recording, enabled, deadline]);
  return {
    quiet,
    seconds,
    pending: deadline !== null,
    observe: (silent: boolean, available = true) => {
      if (sampling.current)
        monitor.current.observe(performance.now(), silent, available);
    },
    request: () => {
      if (active && deadline === null) {
        setSeconds(5);
        setDeadline(performance.now() + 5000);
      }
    },
    cancel: () => setDeadline(null),
    snooze: () => {
      monitor.current.snooze(performance.now());
      setQuiet(false);
    },
    finishNow: () => {
      if (deadline !== null) {
        setDeadline(null);
        void stopRef.current();
      }
    },
  };
}

export default function RecordingEndGuide({
  guide,
}: {
  guide: ReturnType<typeof useRecordingEnd>;
}) {
  if (guide.pending)
    return (
      <section className="recording-end-guide" aria-label="结束录音倒计时">
        <div role="status">
          <strong>{guide.seconds} 秒后结束录音</strong>
          <span>可取消并返回会议；结束时保存录音与关注点。</span>
        </div>
        <button className="primary" onClick={guide.cancel}>
          取消结束 · 返回会议
        </button>
        <button onClick={guide.finishNow}>立即结束</button>
      </section>
    );
  if (!guide.quiet) return null;
  return (
    <section className="recording-end-guide" aria-label="安静提醒">
      <div role="status">
        <strong>约 5 分钟未检测到明显声音</strong>
        <span>
          录音仍在继续。可确认会议是否结束，或检查音源；安静不一定代表会议结束。
        </span>
      </div>
      <button onClick={guide.snooze}>继续录音 · 5 分钟后再提醒</button>
      <button onClick={guide.request}>准备结束</button>
    </section>
  );
}
