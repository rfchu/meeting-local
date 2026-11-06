import type { RecordingQuality } from "../../types";
import { clock } from "../../utils";
type Observation = RecordingQuality["items"][number];
export function qualityPosition(item: Observation) {
  if (item.start_seconds == null) return "音频位置未记录";
  const end = item.end_seconds;
  return end != null && end > item.start_seconds
    ? `观测位置 ${clock(item.start_seconds)}–${clock(end)}`
    : `观测位置 ${clock(item.start_seconds)}`;
}
export function qualitySeek(item: Observation) {
  // Review some audio before the first observation; this is not a missing-audio boundary.
  return item.start_seconds == null
    ? null
    : Math.max(0, item.start_seconds - 10);
}
export function qualityResolution(item: Observation) {
  if (!item.closed_at) return "尚未观测到恢复";
  return [
    "receiving",
    "quiet",
    "recording",
    "writing",
    "available",
    "saved",
  ].includes(item.end_state || "")
    ? "后续状态恢复，仍建议回听确认"
    : "该状态已结束，未确认中途音频是否完整";
}

export const qualityReviewChoices = {
  pending: "待回听",
  clear: "回听无明显问题",
  gap: "确认存在缺音",
  uncertain: "仍需核实",
} as const;
export function qualityReviewLabel(item: Observation) {
  return qualityReviewChoices[item.review_status || "pending"];
}
