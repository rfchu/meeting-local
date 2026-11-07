import AcceptanceReviewPanel from "./AcceptanceReviewPanel";
import ObservationTimeline from "./ObservationTimeline";
import {
  observationMarkers as markers,
  observationFlags as flags,
  observationEndReason,
  observationStateLabel,
} from "./recordingValidationLabels";
import type { NativeCaptureDiagnostics } from "../../types";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, download } from "../../api";
import { clock } from "../../utils";
export interface RecordingObservation {
  id: string;
  recording_id: string;
  state: string;
  status_fresh: boolean;
  target_seconds: number;
  started_at: number;
  updated_at: number;
  elapsed_seconds: number;
  sample_count: number;
  marker_count: number;
  flags: Record<string, number>;
  native_counter_increases?: Record<string, number>;
  end_reason: string | null;
  last_sample?: {
    file_growth_bytes?: number | null;
    native_diagnostics?: NativeCaptureDiagnostics;
    capture: string;
    storage: string;
    asr: string;
    saved_bytes: number | null;
    saved_seconds: number | null;
    covered_seconds: number | null;
  };
}
const counterNames: Record<string, string> = {
  default_input_changes: "默认输入设备变化",
  default_output_changes: "默认输出设备变化",
  audio_device_connections: "音频设备连接",
  audio_device_disconnections: "音频设备断开",
  listener_failures: "监听注册失败",
  device_query_failures: "设备信息读取失败",
};
function counterLabel(key: string) {
  if (counterNames[key]) return counterNames[key];
  const [track, metric] = key.split(".");
  const labels: Record<string, string> = {
    input_buffers: "输入缓冲",
    dropped_buffers: "丢弃缓冲",
    empty_outputs: "暂未产出 PCM 的缓冲",
    format_changes: "格式变化",
    gap_frames: "文件时间轴补空",
  };
  return `${track === "system" ? "电脑音轨" : "麦克风音轨"}${labels[metric] || "计数"}`;
}
const stages: Record<string, string> = {
  startup: "启动",
  capture_stream: "系统采集流",
  audio_conversion: "音频转换",
  storage: "文件保存",
  timeline_limit: "时间轴长度限制",
  unknown: "未识别",
};
function AcceptanceDetails({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>回听与设备验收核对</summary>
      {open && <AcceptanceReviewPanel id={id} />}
    </details>
  );
}
export default function RecordingValidationPanel() {
  const client = useQueryClient();
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 5000);
    return () => window.clearInterval(timer);
  }, []);
  const checks = useQuery({
    queryKey: ["recording-checks"],
    queryFn: api.recordingChecks,
    refetchInterval: 5000,
  });
  const recordings = useQuery({
    queryKey: ["validation-native-recordings"],
    queryFn: api.nativeRecordings,
    refetchInterval: 5000,
  });
  const [rid, setRid] = useState("");
  const [target, setTarget] = useState(10800);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = checks.data?.find((item) => item.state === "observing");
  const act = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
      await client.invalidateQueries({ queryKey: ["recording-checks"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="management-section" aria-label="录音稳定性观察">
      <h3>录音稳定性观察</h3>
      <p>
        选择已运行的原生录音，后台观察采集、文件及 ASR
        状态。停止观察不会停止录音。
      </p>
      <details>
        <summary>开始观察与验收说明</summary>
        <p>
          先在录音工作台开始录音，再选取本次录音。此工具不申请设备权限、不启动模型，也不操作电源或其他软件。观察时长包含暂停时间；采样和文件长度不能证明全程声音完整。
        </p>
        <label>
          正在运行的录音{" "}
          <select
            aria-label="观察哪场录音"
            value={rid}
            disabled={busy || !!active || recordings.isError}
            onChange={(e) => setRid(e.target.value)}
          >
            <option value="">请选择已有录音</option>
            {recordings.data?.map((entry) => (
              <option key={entry.recording.id} value={entry.recording.id}>
                {new Date(entry.recording.created * 1000).toLocaleString()} ·{" "}
                {clock(entry.recording.duration)}
              </option>
            ))}
          </select>
        </label>
        <label>
          目标观察时长{" "}
          <select
            aria-label="目标观察时长"
            value={target}
            disabled={busy || !!active}
            onChange={(e) => setTarget(Number(e.target.value))}
          >
            <option value={60}>1 分钟 · 冒烟检查</option>
            <option value={1800}>30 分钟</option>
            <option value={10800}>3 小时</option>
            <option value={21600}>6 小时</option>
          </select>
        </label>
        <button
          disabled={
            busy ||
            !!active ||
            !rid ||
            checks.isError ||
            !checks.data ||
            recordings.isError ||
            !recordings.data?.some((e) => e.recording.id === rid)
          }
          onClick={() => void act(() => api.startRecordingCheck(rid, target))}
        >
          开始观察已有录音
        </button>
        {!recordings.data?.length && !recordings.isError && (
          <p>当前没有可选择的原生录音。</p>
        )}
        {recordings.isError && (
          <p>运行中的录音列表暂不可用，请重新检查连接。</p>
        )}
      </details>
      {error && <p role="alert">{error}</p>}
      {checks.isError ? (
        <p role="status">
          观察状态暂不可用，不能确认观察仍在进行。
          <button onClick={() => void checks.refetch()}>重新检查</button>
        </p>
      ) : !checks.data ? (
        <p>正在读取观察记录…</p>
      ) : checks.data.length === 0 ? (
        <p className="muted">
          尚未发起观察。合成时间轴测试与真实设备观察分别记录。
        </p>
      ) : (
        checks.data.map((item) => (
          <details key={item.id} open={item.state === "observing"}>
            <summary>
              {new Date(item.started_at * 1000).toLocaleString()} ·{" "}
              {item.state === "observing" &&
              (!item.status_fresh || now - item.updated_at > 20)
                ? "观察状态未更新，请检查服务"
                : observationStateLabel(item.state, item.end_reason)}
            </summary>
            {item.state !== "observing" && (
              <p role="status">{observationEndReason(item.end_reason)}</p>
            )}
            <p>
              观察 {clock(item.elapsed_seconds)} / {clock(item.target_seconds)}{" "}
              · {item.sample_count} 次采样 · {item.marker_count} 条现场标记 ·
              最近更新 {new Date(item.updated_at * 1000).toLocaleTimeString()}
            </p>
            <p>
              文件末端{" "}
              {item.last_sample?.saved_seconds != null
                ? clock(item.last_sample.saved_seconds)
                : "待核对"}{" "}
              · 已确认识别{" "}
              {item.last_sample?.covered_seconds != null
                ? clock(item.last_sample.covered_seconds)
                : "待核对"}
            </p>
            {Object.entries(item.flags).map(([key, count]) => (
              <p key={key}>
                {flags[key] || "异常观测"}：{count} 次采样
              </p>
            ))}
            <ObservationTimeline
              id={item.id}
              observing={item.state === "observing"}
            />
            <details>
              <summary>
                采集器内部诊断 ·{" "}
                {item.last_sample?.native_diagnostics?.available
                  ? "已提供"
                  : "未提供"}
              </summary>
              {!item.last_sample?.native_diagnostics?.available ? (
                <p>
                  此录音未提供内部诊断。更新采集器后，新开始的录音可提供计数；旧记录不会补造设备事件。
                </p>
              ) : (
                <>
                  <p>
                    以下是观察期内新增的计数。设备变化不是麦克风被抢占的证明，文件补空也不是已确认的人声缺失。
                  </p>
                  {item.last_sample.native_diagnostics.counters_complete ===
                    false && <p>部分内部计数缺失，不能按零处理。</p>}
                  {Object.entries(item.native_counter_increases || {}).map(
                    ([key, value]) => (
                      <p key={key}>
                        {counterLabel(key)}：
                        {key.endsWith(".gap_frames")
                          ? `${(value / 16000).toFixed(3)} 秒`
                          : value}
                      </p>
                    ),
                  )}
                  {!Object.keys(item.native_counter_increases || {}).length && (
                    <p>暂未观察到可计算的计数增量。</p>
                  )}
                  {item.last_sample.native_diagnostics.failure_stage &&
                    item.last_sample.native_diagnostics.failure_stage !==
                      "none" && (
                      <p>
                        采集器错误阶段：
                        {stages[
                          item.last_sample.native_diagnostics.failure_stage
                        ] || "未识别"}
                      </p>
                    )}
                </>
              )}
              <p>
                与上次采样相比的文件变化：
                {item.last_sample?.file_growth_bytes == null
                  ? "未记录／首次采样"
                  : `${item.last_sample.file_growth_bytes} 字节`}
                。文件增长不能证明声音完整。
              </p>
            </details>
            {item.state === "observing" && !checks.isError && (
              <>
                <div className="quality-review-controls">
                  {Object.entries(markers).map(([kind, label]) => (
                    <button
                      disabled={busy}
                      key={kind}
                      onClick={() =>
                        void act(() =>
                          api.markRecordingCheck(
                            item.id,
                            kind as keyof typeof markers,
                          ),
                        )
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="muted">
                  标记只表示你报告进行了该操作，不能证明操作已被系统验证。
                </p>
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(() => api.stopRecordingCheck(item.id))
                  }
                >
                  停止观察，录音继续
                </button>
              </>
            )}
            {item.state !== "observing" && <AcceptanceDetails id={item.id} />}
            <button
              disabled={busy || checks.isError}
              onClick={() =>
                download(`/runtime/recording-checks/${item.id}/report`)
              }
            >
              导出脱敏观察报告
            </button>
            <p className="muted">
              观察结束仍需人工回听。当前不自动判定音频完整、睡眠唤醒或软件争用验证通过。
            </p>
          </details>
        ))
      )}
      <small>
        保留最近 20 次观察、每次最多 9000 条采样与 200
        条人工标记；不会写入会议文字或纪要。
      </small>
    </section>
  );
}
