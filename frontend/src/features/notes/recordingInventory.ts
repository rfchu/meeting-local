import { recordingActive } from "./recordingStatus";
import type { RecordingSession } from "../../types";

export function mergeRecordingInventory(
  local: RecordingSession[],
  remote: RecordingSession[],
) {
  const rows = new Map(remote.map((session) => [session.id, session]));
  for (const session of local) {
    const server = rows.get(session.id);
    rows.set(session.id, {
      ...(server && server.updated > session.updated ? server : session),
      health: server?.health || session.health,
      saved_chunks: server?.saved_chunks,
      chunk_count: Math.max(server?.chunk_count || 0, session.chunk_count),
      native_audio_saved:
        server?.native_audio_saved || session.native_audio_saved,
      duration: Math.max(server?.duration || 0, session.duration),
    });
  }
  return [...rows.values()]
    .filter(
      (s) =>
        s.state !== "completed" &&
        s.health?.state !== "completed" &&
        (s.chunk_count > 0 ||
          s.native_audio_saved ||
          s.notes.length > 0 ||
          s.source === "macos"),
    )
    .sort((a, b) => b.created - a.created);
}

// Deliberately select numeric/boolean fields; never include status text, content or IDs.
export function clientRecordingDiagnostics(
  state: string,
  pending: number | null,
  memoryOnly: number,
  metrics: {
    firstMs: number | null;
    processingMs: number;
    rtf: number | null;
    lagMs: number | null;
    revisions: number;
  },
) {
  const finite = (n: number | null) =>
    n !== null && Number.isFinite(n) ? Math.max(0, n) : null;
  return {
    state: [
      "idle",
      "requesting",
      "recording",
      "paused",
      "saving",
      "failed",
      "finished",
    ].includes(state)
      ? state
      : "unknown",
    browser_pending_chunks: finite(pending),
    memory_only_chunks: finite(memoryOnly),
    metrics: {
      first_text_ms: finite(metrics.firstMs),
      processing_ms: finite(metrics.processingMs),
      rtf: finite(metrics.rtf),
      confirmation_lag_ms: finite(metrics.lagMs),
      revisions: finite(metrics.revisions),
    },
  };
}

/** A known failed native start with no saved material is never counted as a recording. */
export function emptyRecording(session: RecordingSession): boolean {
  const h = session.health;
  if (
    !h ||
    session.source !== "macos" ||
    recordingActive(h) ||
    h.runtime?.control?.status === "unconfirmed" ||
    !["failed", "interrupted", "stopped"].includes(h.state)
  )
    return false;
  if (
    h.audio.saved_bytes ||
    session.native_audio_saved ||
    session.chunk_count ||
    session.notes.length ||
    Object.values(session.brief || {}).some(Boolean)
  )
    return false;
  if (h.empty_session !== undefined) return h.empty_session;
  return !Object.values(h.asr || {}).some(
    (a) => a.covered_seconds || a.running_attempts,
  );
}
export function recordingRecoveryCount(sessions: RecordingSession[]) {
  return sessions.filter(
    (s) =>
      !recordingActive(s.health) &&
      !s.health?.recovery_archived &&
      !emptyRecording(s),
  ).length;
}
