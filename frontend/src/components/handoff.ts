import type { Meeting, TaskFocus } from "../types";

// A background response cannot replace local unsaved work or a newer adopted version.
export function currentHandoff(meeting: Meeting) {
  const value = meeting.handoff;
  if (
    !value ||
    value.version !== 1 ||
    value.revision !== meeting.revision ||
    value.title !== meeting.title ||
    !Array.isArray(value.stages) ||
    !value.primary ||
    ![
      "transcribe",
      "transcript",
      "speakers",
      "notes",
      "overview",
      "minutes",
      "actions",
      "preview",
      "tasks",
      "recording",
    ].includes(value.primary.target)
  )
    return null;
  const source = value.stages.find((stage) => stage.key === "minutes")?.source;
  if (source?.artifact_id !== (meeting.active_minutes_id || null)) return null;
  return value;
}
export function handoffAction(
  meeting: Meeting,
  transcriptDirty: boolean,
  minutesDirty: boolean,
) {
  if (transcriptDirty || minutesDirty) return null;
  return currentHandoff(meeting)?.primary || null;
}
export const handoffStates = {
  ready: "已就绪",
  review: "待核对",
  stale: "来源变化",
  processing: "处理中",
  optional: "按需整理",
  missing: "待准备",
};
export const handoffRelations = {
  reused_audio_ranges: "按已保存音频范围复用",
  context: "作为整理上下文",
  explicit_adoption: "已明确采用",
  confirmed_tracking: "已确认加入跟进",
};

// Apply only at the recording-to-meeting handoff, never during background polling.
export function handoffWorkspace(meeting: Meeting): "minutes" | "transcript" {
  const target = currentHandoff(meeting)?.primary.target;
  if (!target) {
    return meeting.active_minutes_id ||
      meeting.artifacts?.some((a) => a.kind === "meeting_minutes")
      ? "minutes"
      : "transcript";
  }
  if (["minutes", "actions", "preview", "notes", "overview"].includes(target))
    return "minutes";
  return "transcript";
}

export function handoffTaskFocus(
  meeting: Meeting,
  explicitJobId?: unknown,
): TaskFocus | undefined {
  const value = currentHandoff(meeting);
  const id =
    typeof explicitJobId === "string" ? explicitJobId : value?.primary.job_id;
  return value && id ? { meetingId: meeting.id, jobId: id } : undefined;
}
