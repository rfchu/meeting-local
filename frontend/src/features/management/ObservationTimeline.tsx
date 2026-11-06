import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { clock } from "../../utils";
import { lifecycleLabel } from "../notes/recordingStatus";
import {
  observationFlags,
  observationMarkers,
} from "./recordingValidationLabels";
interface Context {
  capture?: string;
  storage?: string;
  asr?: string;
  saved_seconds: number | null;
  covered_seconds: number | null;
}
export interface ObservationTimelineData {
  valid_sample_count: number;
  invalid_samples: number;
  invalid_markers: number;
  unrecognized_flag_count: number;
  source_samples_truncated: boolean;
  events_truncated: boolean;
  retained_from_elapsed_seconds: number | null;
  retained_to_elapsed_seconds: number | null;
  total_event_count: number;
  retained_event_count: number;
  events: {
    key: string;
    kind: string;
    code: string;
    start_elapsed_seconds: number;
    end_elapsed_seconds: number;
    sample_count: number;
    start_context: Context;
    end_context: Context;
    end_evidence: string;
  }[];
}
const evidence: Record<string, string> = {
  observation_ended: "保留采样截至此处",
  observation_gap: "后续观测不连续",
  not_seen_in_next_sample: "后续采样未再见该标记",
  next_sample_observed: "后续重新获得采样",
  state_change_observed: "两次保留采样间观察到状态变化",
  coverage_or_observation_changed: "后续覆盖或观测条件变化",
};
export function timelineTitle(item: ObservationTimelineData["events"][number]) {
  if (item.kind === "manual_marker")
    return `用户标记：${observationMarkers[item.code as keyof typeof observationMarkers] || "操作待核对"}`;
  if (item.code === "capture_pause_state_changed") return "采集暂停状态变化";
  if (item.code === "sampling_discontinuity")
    return "采样不连续，期间状态待核对";
  if (item.code === "asr_coverage_flat_with_file_growth")
    return "文件增长期间，ASR 确认覆盖暂未推进";
  return observationFlags[item.code] || "观测线索待核对";
}
function position(value: number | null) {
  return value == null ? "未知" : clock(value);
}
export default function ObservationTimeline({
  id,
  observing,
}: {
  id: string;
  observing: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["recording-check-timeline", id, offset, observing],
    queryFn: () => api.recordingCheckTimeline(id, offset),
    enabled: open,
    refetchInterval: open && observing ? 5000 : false,
  });
  const data = query.data;
  return (
    <details
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="observation-timeline"
    >
      <summary>查看观察时间线</summary>
      <p>
        最近观测优先。这里的时间是观察启动后的经过时间，不是音频时间戳；相邻事件不表示因果关系。观察进行中，条目和分页可能随新采样更新。
      </p>
      {query.isError ? (
        <p role="status">
          时间线暂不可用，原录音保留。
          <button onClick={() => void query.refetch()}>重新读取</button>
        </p>
      ) : !data ? (
        <p>正在整理保留的采样…</p>
      ) : (
        <>
          <p>
            {data.valid_sample_count} 条保留采样 · {data.total_event_count}{" "}
            条派生线索 · 首末采样 {position(data.retained_from_elapsed_seconds)}
            –{position(data.retained_to_elapsed_seconds)}
          </p>
          {(data.source_samples_truncated ||
            data.invalid_samples > 0 ||
            data.invalid_markers > 0 ||
            data.unrecognized_flag_count > 0) && (
            <p className="health-warning">
              部分记录未保留或不可解析，时间线不能代表完整观察过程。
            </p>
          )}
          {data.events_truncated && (
            <p>
              仅展示最近 {data.retained_event_count}{" "}
              条派生线索；可导出报告检查保留的原始采样。
            </p>
          )}
          {!data.events.length && (
            <p>本页没有可展示的异常线索；这不表示全程声音完整。</p>
          )}
          <ol>
            {data.events.map((item) => (
              <li key={item.key}>
                <strong>{timelineTitle(item)}</strong>
                <p>
                  观察 {clock(item.start_elapsed_seconds)}
                  {item.end_elapsed_seconds > item.start_elapsed_seconds
                    ? `–${clock(item.end_elapsed_seconds)}`
                    : ""}
                  {item.kind !== "manual_marker"
                    ? item.code === "sampling_discontinuity"
                      ? " · 期间状态未知"
                      : ` · ${item.sample_count} 次保留采样`
                    : ""}
                </p>
                {item.kind === "manual_marker" ? (
                  <p>仅表示用户报告该操作，不是系统确认的设备或电源事件。</p>
                ) : (
                  <>
                    <p>
                      文件末端 {position(item.start_context.saved_seconds)} →{" "}
                      {position(item.end_context.saved_seconds)} · ASR 确认覆盖{" "}
                      {position(item.start_context.covered_seconds)} →{" "}
                      {position(item.end_context.covered_seconds)}
                    </p>
                    <p>
                      末次采样状态：
                      {["capture", "storage", "asr"]
                        .map((part) =>
                          lifecycleLabel(
                            `${part}.${item.end_context[part as keyof Context] || "unknown"}`,
                          ),
                        )
                        .join(" · ")}
                    </p>
                    {item.code === "asr_coverage_flat_with_file_growth" && (
                      <p>
                        至少 30
                        秒的连续保留采样中，确认覆盖保持同值、文件字节增加。可能仍在等待分段或推理，不能据此认定
                        ASR 故障或声音正常。
                      </p>
                    )}
                    <small>
                      {evidence[item.end_evidence] || "结束状态待核对"}
                    </small>
                  </>
                )}
              </li>
            ))}
          </ol>
          <div className="quality-review-controls">
            <button
              disabled={query.isFetching || offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - 50))}
            >
              上一页
            </button>
            <button
              disabled={
                query.isFetching ||
                offset + data.events.length >= data.retained_event_count
              }
              onClick={() => setOffset((value) => value + 50)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </details>
  );
}
