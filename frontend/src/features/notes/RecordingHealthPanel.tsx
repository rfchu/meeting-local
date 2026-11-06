import RecordingControlNotice from "./RecordingControlNotice";
import type { NativeCaptureStatus } from "../../types";
import { useRecordingHealth } from "./useRecordingHealth";
import { recordingStatus, lifecycleLabel } from "./recordingStatus";

export default function RecordingHealthPanel({
  id,
  state,
  native,
  tracks,
  localFailure,
  memoryOnly,
  onDiagnostics,
  recognitionEnabled = true,
}: {
  id: string;
  state: string;
  native: boolean;
  tracks?: NativeCaptureStatus["health"];
  localFailure: boolean;
  memoryOnly: number;
  onDiagnostics: () => void;
  recognitionEnabled?: boolean;
}) {
  const {
    inventory,
    pending,
    unavailable,
    freshness,
    checkedAt,
    fetching,
    refresh,
  } = useRecordingHealth(id, native);
  if (!id) return null;
  const ended = ["failed", "finished", "saving"].includes(state);
  const unified = recordingStatus(inventory?.runtime, state, unavailable);
  const input =
    unified.input ||
    (state === "finished"
      ? "采集已结束"
      : state === "saving"
        ? "采集结束，正在保存"
        : state === "failed"
          ? "采集已中断，可恢复已存内容"
          : state === "paused"
            ? "录音已暂停"
            : native && unavailable
              ? "本机状态不可用，不能确认采集仍在继续"
              : native
                ? tracks?.tracks.some((t) =>
                    ["stalled", "no_data"].includes(t.state),
                  )
                  ? "音源异常，请检查声音和权限"
                  : tracks?.tracks.some((t) => t.state === "quiet")
                    ? "声音较弱，请确认音源"
                    : "原生采集中 · 详见音轨状态"
                : "麦克风采集中");
  const storage =
    inventory?.runtime?.storage.state === "failed"
      ? "写盘失败，已有音频保留，请重试同步"
      : inventory?.runtime?.storage.state === "unconfirmed"
        ? "音轨文件未确认持续更新，请检查音轨与磁盘；已有文件保留"
        : localFailure || memoryOnly > 0
          ? memoryOnly > 0
            ? `保存异常 · ${memoryOnly} 段仅在内存中，请勿关闭页面`
            : "浏览器暂存异常，请先重试保存，勿关闭页面"
          : unavailable
            ? "本机保存状态暂不可用，不能确认同步完成"
            : !inventory
              ? "正在核对已存音频…"
              : native
                ? inventory.audio.saved_bytes > 0
                  ? `本机音轨已落盘 ${(inventory.audio.saved_bytes / 1048576).toFixed(1)} MB`
                  : "等待首批音轨落盘"
                : pending === null
                  ? "浏览器暂存状态不可用，请检查运行详情"
                  : pending > 0
                    ? `${pending} 段待同步 · 浏览器暂存保留`
                    : inventory.audio.missing_chunk_count > 0
                      ? `本机缺 ${inventory.audio.missing_chunk_count} 段 · 请重试同步`
                      : inventory.audio.saved_bytes > 0
                        ? `本机已落盘 ${(inventory.audio.saved_bytes / 1048576).toFixed(1)} MB`
                        : "尚无音频落盘，等待首个分段";
  const asr = inventory?.asr;
  const failures = asr
    ? Object.values(asr).reduce((n, v) => n + v.failed_attempts, 0)
    : 0;
  return (
    <section className="recording-health-panel" aria-label="录音健康状态">
      <div className="recording-status-summary" role="status">
        <strong>
          {freshness === "checking" && state !== "finished"
            ? "正在核对录音状态"
            : unified.label}
        </strong>
        <span>
          {freshness === "stale"
            ? "状态超过 20 秒未更新，不能确认采集仍在继续"
            : freshness === "unavailable"
              ? "状态连接失败，已有音频与确认结果保留"
              : freshness === "checking"
                ? "等待本机返回最新状态，录音不会因此重新开始"
                : `最近核对 ${new Date(checkedAt).toLocaleTimeString()}`}
        </span>
        <span>
          {unavailable && unified.savedPosition ? "上次核对：" : ""}
          {unified.savedPosition} · 识别状态与采集状态分别核对
        </span>
      </div>
      <RecordingControlNotice
        id={id}
        runtime={inventory?.runtime}
        unavailable={unavailable}
      />
      <div>
        <strong>音频采集</strong>
        <span>{input}</span>
      </div>
      <div
        className={
          localFailure ||
          memoryOnly ||
          unavailable ||
          (!native && pending === null) ||
          pending ||
          inventory?.audio.missing_chunk_count
            ? "health-warning"
            : ""
        }
      >
        <strong>文件保存</strong>
        <span>{storage}</span>
      </div>
      <div>
        <strong>实时识别</strong>
        <span>
          {unified.asr ||
            (!recognitionEnabled
              ? "本次仅录音，会后可配置模型转写"
              : failures
                ? `${failures} 次失败尝试 · 完成时可补识别`
                : ended
                  ? "可选择快速完成或精细转写"
                  : "识别独立运行，变慢不代表原音丢失")}
        </span>
      </div>
      <div className="recording-health-actions">
        <button
          className="text-btn"
          disabled={fetching}
          onClick={() => void refresh()}
        >
          {fetching ? "核对中…" : "重新核对状态"}
        </button>
        <button className="text-btn" onClick={onDiagnostics}>
          导出脱敏诊断
        </button>
      </div>
      {!!inventory?.lifecycle && (
        <details className="recording-status-summary">
          <summary>
            运行事件与诊断
            {inventory.lifecycle.available ? "" : " · 记录暂不可用"}
          </summary>
          <p>仅记录状态、时间和计数，不含会议内容。</p>
          {inventory.lifecycle.events.slice(0, 8).map((event) => (
            <p key={event.seq}>
              {new Date(event.at * 1000).toLocaleTimeString()} ·{" "}
              {lifecycleLabel(event.code)}
            </p>
          ))}
        </details>
      )}
    </section>
  );
}
