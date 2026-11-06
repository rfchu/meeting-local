import { api } from "../../api";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { RecordingRuntime } from "../../types";
import { controlPresentation } from "./recordingStatus";
import {
  watchRecordingCommands,
  recordingCommandFeedback,
  dismissRecordingCommandFeedback,
} from "./recordingCommandEvents";
export default function RecordingControlNotice({
  id,
  runtime,
  unavailable = false,
}: {
  id: string;
  runtime?: RecordingRuntime;
  unavailable?: boolean;
}) {
  const feedback = useSyncExternalStore(
    watchRecordingCommands,
    () => recordingCommandFeedback(id),
    () => null,
  );
  const [busy, setBusy] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const epoch = useRef(0);
  const [retryError, setRetryError] = useState("");
  useEffect(() => {
    ++epoch.current;
    setBusy(false);
    setRetryError("");
    setConfirmStop(false);
    return () => {
      ++epoch.current;
    };
  }, [id]);
  const act = async (fn: () => Promise<unknown>) => {
    const version = ++epoch.current;
    setBusy(true);
    setRetryError("");
    try {
      await fn();
      if (epoch.current === version) setConfirmStop(false);
    } catch (error) {
      if (epoch.current === version) setRetryError((error as Error).message);
    } finally {
      if (epoch.current === version) setBusy(false);
    }
  };
  const notice = controlPresentation(runtime, unavailable, feedback?.kind);
  if (!notice) return null;
  return (
    <div className="recording-control-notice" role="status">
      <strong>{notice.label}</strong>
      <p>{notice.message}</p>
      {api.pendingNativeCommand(id) && (
        <button
          className="text-btn"
          disabled={busy}
          onClick={() => void act(() => api.retryNativeCommand(id))}
        >
          {busy ? "正在核对上次操作…" : "重试上次操作"}
        </button>
      )}
      {notice.persistent &&
        !unavailable &&
        runtime?.source === "native" &&
        runtime.schema_version === 1 &&
        (confirmStop ? (
          <div>
            <p>
              确认结束这场录音的采集？已有音轨会保留，之后可在录音恢复中整理；此操作不会生成新录音。
            </p>
            <button
              disabled={busy}
              onClick={() => void act(() => api.nativeCommand(id, "stop"))}
            >
              {busy ? "正在结束采集…" : "确认结束采集"}
            </button>
            <button disabled={busy} onClick={() => setConfirmStop(false)}>
              取消
            </button>
          </div>
        ) : (
          <button
            className="text-btn"
            disabled={busy}
            onClick={() => setConfirmStop(true)}
          >
            结束这场采集…
          </button>
        ))}
      {retryError && <p>{retryError}</p>}
      {feedback && !notice.persistent && (
        <button
          className="text-btn"
          onClick={() => dismissRecordingCommandFeedback(id)}
        >
          收起本次提示
        </button>
      )}
    </div>
  );
}
