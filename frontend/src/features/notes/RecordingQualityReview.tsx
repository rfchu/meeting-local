import type { RecordingQuality } from "../../types";
import { lifecycleLabel } from "./recordingStatus";
import {
  qualityPosition,
  qualityResolution,
  qualitySeek,
} from "./recordingQuality";
export default function RecordingQualityReview({
  quality,
  onSeek,
  compact = false,
}: {
  quality: RecordingQuality;
  onSeek?: (seconds: number) => void;
  compact?: boolean;
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
      className="recording-quality-review health-warning"
      aria-label="录音异常回听核对"
    >
      <strong>有 {quality.total_count} 条采集异常观测，建议回听</strong>
      <p>
        位置是状态观测记录，并非精确缺音范围；文件保存成功和识别覆盖完整都不能证明声音完整。
      </p>
      <details>
        <summary>
          查看异常位置与状态
          {quality.total_count > quality.items.length
            ? ` · 最近 ${quality.items.length} 条`
            : ""}
        </summary>
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
                {onSeek && seconds != null && (
                  <button className="text-btn" onClick={() => onSeek(seconds)}>
                    从附近回听
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      </details>
    </section>
  );
}
