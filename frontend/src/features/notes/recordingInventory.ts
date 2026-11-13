import { recordingActive } from "./recordingStatus";
import type { RecordingHealth, RecordingSession } from "../../types";

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

function hasAsrActivity(health: RecordingHealth) {
  return Object.values(health.asr || {}).some(
    (a) => a.covered_seconds || a.running_attempts,
  );
}

export function hasBriefContent(
  value: unknown,
  skipLanguageKeys = true,
): boolean {
  if (value == null) return false;
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return value.trim().length > 0;
  if (typeof value === "number") return Boolean(value);
  if (Array.isArray(value))
    return value.some((item) => hasBriefContent(item, false));
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).some(
      ([key, nested]) =>
        !(
          skipLanguageKeys &&
          (key === "source_language" || key === "output_language")
        ) && hasBriefContent(nested, false),
    );
  }
  return Boolean(value);
}

export function hasSavedAudio(session: RecordingSession): boolean {
  return !!(
    session.chunk_count ||
    session.native_audio_saved ||
    session.health?.audio.saved_bytes
  );
}

export function noAudioArchivable(session: RecordingSession): boolean {
  const h = session.health;
  if (!h || hasSavedAudio(session)) return false;
  if (h.audio.recoverable || h.audio.missing_chunk_count) return false;
  if (recordingActive(h) || h.runtime?.control?.status === "unconfirmed")
    return false;
  return ["failed", "interrupted", "stopped"].includes(h.state);
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
  if (hasSavedAudio(session) || session.notes.length) return false;
  if (h.capture_failure_code === "permission_required" && !hasAsrActivity(h))
    return true;
  if (hasBriefContent(session.brief)) return false;
  if (h.empty_session !== undefined) return h.empty_session;
  return !hasAsrActivity(h);
}
export function recordingRecoveryCount(sessions: RecordingSession[]) {
  return sessions.filter(
    (s) =>
      !recordingActive(s.health) &&
      !s.health?.recovery_archived &&
      !emptyRecording(s),
  ).length;
}
