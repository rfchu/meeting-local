import { recordingActive } from "./recordingStatus";
import { api } from "../../api";
import type { Meeting, RecordingHealth, RecordingSession } from "../../types";
import {
  bufferRecording,
  flushRecording,
  forgetRecording,
} from "./recordingBuffer";
interface RecoveryIO {
  health: (id: string) => Promise<RecordingHealth>;
  buffer: (session: RecordingSession) => Promise<void>;
  flush: (id: string) => Promise<void>;
  finishNative: (id: string) => Promise<Meeting>;
  finishBrowser: (id: string, count: number) => Promise<Meeting>;
  forget: (id: string) => Promise<void>;
}
const defaults: RecoveryIO = {
  health: api.recordingHealth,
  buffer: bufferRecording,
  flush: flushRecording,
  finishNative: api.nativeFinish,
  finishBrowser: api.finishRecording,
  forget: forgetRecording,
};
/** Recovery publishes saved material only. It cannot start capture or schedule inference. */
export async function recoverSavedRecording(
  session: RecordingSession,
  io: RecoveryIO = defaults,
) {
  const before = await io.health(session.id);
  if (before.state === "completed")
    throw new Error(
      "此录音已在其他页面保存，请先到会议资料库核对；浏览器暂存仍保留。",
    );
  if (recordingActive(before))
    throw new Error("此会话仍在录音，请先在原录音页面结束。");
  await io.buffer(session);
  await io.flush(session.id);
  const current = await io.health(session.id);
  if (current.state === "completed")
    throw new Error(
      "此录音已在其他页面保存，请到会议资料库核对；浏览器暂存仍保留。",
    );
  // Flushing browser metadata updates its age; this alone is not a new capture report.
  // Keep the user's stopped confirmation when raw state and command version did not change.
  const metadataRefreshOnly =
    before.source === "browser" &&
    current.source === "browser" &&
    !current.native_running &&
    !recordingActive(before) &&
    before.state === current.state &&
    before.runtime?.control?.version != null &&
    before.runtime.control.version === current.runtime?.control?.version;
  if (recordingActive(current) && !metadataRefreshOnly)
    throw new Error("检测到录音正在进行，已停止恢复操作。");
  if (session.source !== "macos" && current.audio.missing_chunk_count > 0)
    throw new Error(
      `仍有 ${current.audio.missing_chunk_count} 个分段未保存，已有内容保留，请检查原录音浏览器的暂存。`,
    );
  const meeting =
    session.source === "macos"
      ? await io.finishNative(session.id)
      : await io.finishBrowser(session.id, session.chunk_count);
  let cleanupPending = false;
  try {
    await io.forget(session.id);
  } catch {
    cleanupPending = true;
  }
  return { meeting, cleanupPending };
}
