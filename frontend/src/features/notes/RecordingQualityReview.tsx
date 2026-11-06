import type { ReactNode } from "react";
import type { RecordingQuality, RecordingReviewStatus } from "../../types";
import { lifecycleLabel } from "./recordingStatus";
import {
  qualityPosition,
  qualityResolution,
  qualitySeek,
  qualityReviewLabel,
  qualityReviewChoices,
} from "./recordingQuality";
export default function RecordingQualityReview({
  quality,
  onSeek,
  compact = false,
  onReview,
  busy = false,
  controls,
}: {
  quality: RecordingQuality;
  onSeek?: (seconds: number) => void;
  compact?: boolean;
  onReview?: (
    item: RecordingQuality["items"][number],
    status: RecordingReviewStatus,
  ) => void;
  busy?: boolean;
  controls?: ReactNode;
}) {
  if (!quality.recorded) return null;
  if (!quality.available)
    return (
      <p className="health-warning">
        录音异常记录暂不可用，当前无法核对采集完整性。
      </p>
    );
  if (!quality.total_count)
    return compact ? null : (
      <p className="muted">
        {quality.monitored
          ? "未观测到采集异常；采样检查不能保证全程没有缺音。"
          : "本次缺少采集异常观测记录，无法据此确认录音完整性。"}
      </p>
    );
  return (
    <section
      className={`recording-quality-review ${quality.pending_count === 0 && !quality.gap_count ? "" : "health-warning"}`}
      aria-label="录音异常回听核对"
    >
      <strong>
        {quality.pending_count != null
          ? `待核对 ${quality.pending_count} 条 · 已明确结论 ${quality.reviewed_count || 0} 条${quality.gap_count ? ` · 确认缺音 ${quality.gap_count} 条` : ""}`
          : `有 ${quality.total_count} 条采集异常观测，建议回听`}
      </strong>
      {quality.retained_count != null &&
        quality.total_count > quality.retained_count && (
          <p>
            另有 {quality.total_count - quality.retained_count}{" "}
            条较旧观测详情不再保留，不计入当前待核对数。
          </p>
        )}
      <p>
        位置是状态观测记录，并非精确缺音范围；文件保存成功和识别覆盖完整都不能证明声音完整。
      </p>
      <details>
        <summary>
          查看异常位置与状态
          {quality.total_count > quality.items.length
            ? ` · 本页 ${quality.items.length} 条`
            : ""}
        </summary>
        {controls}
        {quality.items.length === 0 && (
          <p>当前没有待核对的保留详情，可切换“全部记录”查看已记录结论。</p>
        )}
        <ul>
          {quality.items.map((item) => {
            const seconds = qualitySeek(item);
            return (
              <li key={item.id}>
                <strong>
                  {lifecycleLabel(`${item.component}.${item.state}`)}
                </strong>
                <p>
                  {qualityPosition(item)} · {qualityResolution(item)}
                </p>
                <p>人工核对：{qualityReviewLabel(item)}</p>
                {onSeek && seconds != null && (
                  <button className="text-btn" onClick={() => onSeek(seconds)}>
                    从附近回听
                  </button>
                )}
                {onReview && (
                  <label>
                    核对结论{" "}
                    <select
                      aria-label={`核对结论 ${item.id}`}
                      value={item.review_status || "pending"}
                      disabled={busy || !item.closed_at}
                      onChange={(event) =>
                        onReview(
                          item,
                          event.target.value as RecordingReviewStatus,
                        )
                      }
                    >
                      {Object.entries(qualityReviewChoices).map(
                        ([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ),
                      )}
                    </select>
                  </label>
                )}
                {onReview && !item.closed_at && (
                  <small>观测仍在变化，状态结束后可记录结论。</small>
                )}
              </li>
            );
          })}
        </ul>
      </details>
    </section>
  );
}
