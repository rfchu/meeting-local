import type {
  Artifact,
  Job,
  Meeting,
  MeetingAnalysis,
  MeetingMinutes,
} from "./types";

export const clock = (seconds = 0) => {
  const value = Math.max(0, Math.floor(seconds));
  const hours =
    value >= 3600
      ? `${String(Math.floor(value / 3600)).padStart(2, "0")}:`
      : "";
  return `${hours}${String(Math.floor(value / 60) % 60).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
};

export const engineLabel = (id?: string) =>
  id === "qwen"
    ? "千问 Qwen3-ASR-1.7B"
    : id === "whisper" || id === "whisper.cpp"
      ? "Whisper large-v3-turbo"
      : id || "模型识别";
export const revisionLabel = (source?: string) => {
  if (!source) return "—";
  if (source === "manual") return "人工校对";
  try {
    const value = JSON.parse(source) as {
      engine_label?: string;
      engine?: string;
      kind?: string;
    };
    if (value.kind === "live_reuse")
      return `${value.engine_label || engineLabel(value.engine)} · 快速文字稿`;
    if (value.kind === "speaker_review") return "AI 发言人校对（已确认）";
    return value.engine_label || engineLabel(value.engine);
  } catch {
    return "模型识别";
  }
};
export const latestJob = (
  meeting: Meeting | null,
  kind: "transcription" | "analysis",
) => meeting?.jobs.find((job) => (job.kind || "transcription") === kind);
export const currentAnalysis = (meeting: Meeting | null) =>
  meeting?.artifacts.find(
    (item) =>
      item.kind === "meeting_analysis" && item.revision === meeting.revision,
  ) as Artifact<MeetingAnalysis> | undefined;
export const latestAnalysis = (meeting: Meeting | null) =>
  meeting?.artifacts.find((item) => item.kind === "meeting_analysis") as
    Artifact<MeetingAnalysis> | undefined;
export const currentMinutes = (meeting: Meeting | null) => {
  const value = meeting?.artifacts.find(
    (item) =>
      item.kind === "meeting_minutes" &&
      item.revision === meeting.revision &&
      (!meeting.active_minutes_id || item.id === meeting.active_minutes_id),
  ) as Artifact<MeetingMinutes> | undefined;
  return value?.stale ? undefined : value;
};
export const latestMinutes = (meeting: Meeting | null) =>
  (meeting?.active_minutes_id
    ? meeting.artifacts.find(
        (item) =>
          item.id === meeting.active_minutes_id &&
          item.kind === "meeting_minutes",
      )
    : currentMinutes(meeting) ||
      meeting?.artifacts.find((item) => item.kind === "meeting_minutes")) as
    Artifact<MeetingMinutes> | undefined;
export const isRunning = (job?: Job) =>
  !!job && (job.state === "queued" || job.state === "running");
export const clone = <T>(value: T): T => structuredClone(value);

export function trackLabel(track: "system" | "microphone") {
  return track === "system" ? "线上 · 电脑声音" : "现场 · 麦克风";
}
