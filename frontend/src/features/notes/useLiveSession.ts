import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import {
  watchLiveSession,
  type SessionConfig,
  type SessionEvent,
  type SessionSnapshot,
} from "./liveSession";

export function useLiveSession(
  id: string | undefined,
  enabled: boolean,
  config: SessionConfig,
  onEvent: (event: SessionEvent) => void,
  onStatus: (message: string) => void,
) {
  const callbacks = useRef({ onEvent, onStatus });
  callbacks.current = { onEvent, onStatus };
  const ready = useRef<{
    id: string;
    promise: Promise<SessionSnapshot>;
  } | null>(null);
  const [connection, setConnection] = useState("");
  const cursor = useRef({ id: "", seq: 0 });
  const ensure = (rid: string) => {
    if (ready.current?.id === rid) return ready.current.promise;
    const promise = api.startLiveSession(rid, config).catch((error) => {
      if (ready.current?.promise === promise) ready.current = null;
      throw error;
    });
    ready.current = { id: rid, promise };
    return promise;
  };
  useEffect(() => {
    if (!id || !enabled) return;
    if (cursor.current.id !== id) cursor.current = { id, seq: 0 };
    let cancelled = false,
      timer: ReturnType<typeof setTimeout> | undefined,
      failures = 0;
    const controller = new AbortController();
    const run = async () => {
      while (!cancelled) {
        try {
          const snapshot = await ensure(id);
          if (cancelled) return;
          callbacks.current.onEvent(snapshot);
          cursor.current.seq = snapshot.seq;
          setConnection("后端持续识别 · 已连接");
          let ended = false;
          await watchLiveSession(
            id,
            cursor.current.seq,
            (event) => {
              if (cancelled) return;
              if (event.type === "ended") ended = true;
              cursor.current.seq = Math.max(cursor.current.seq, event.seq);
              callbacks.current.onEvent(event);
            },
            controller.signal,
          );
          if (ended) {
            setConnection("后端实时会话已结束");
            return;
          }
        } catch {
          if (cancelled) return;
          failures++;
          setConnection("连接中断 · 正在同步已保存结果");
          callbacks.current.onStatus(
            "实时连接重连中；后端继续处理已收到的音频",
          );
          // Fetch a fresh snapshot on reconnect rather than replaying a stale start response.
          ready.current = null;
          await new Promise<void>((resolve) => {
            timer = setTimeout(
              resolve,
              Math.min(5000, 500 * 2 ** Math.min(failures, 3)),
            );
          });
        }
      }
    };
    void run();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [id, enabled]);
  return { ensure, connection };
}
