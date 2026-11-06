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
  end_reason: string | null;
  last_sample?: {
    capture: string;
    storage: string;
    asr: string;
    saved_bytes: number | null;
    saved_seconds: number | null;
    covered_seconds: number | null;
  };
}
const markers = {
  background_return: "从后台返回",
  device_changed: "设备已变化",
  other_app_changed: "其他软件已开启／退出",
  pause_resume: "已暂停／恢复",
  wake_return: "系统唤醒后返回",
};
const flags: Record<string, string> = {
  observation_gap: "观察调度长间隔",
  capture_not_running: "采集进程不在运行",
  capture_ended: "采集已结束",
  capture_unknown: "采集状态未知",
  storage_unconfirmed: "文件保存待核对",
  asr_attention: "ASR 需检查",
  asr_lag_observed: "识别覆盖落后采集超过一分钟",
  system_no_data: "系统音轨无数据",
  microphone_no_data: "麦克风音轨无数据",
  status_unavailable: "状态读取失败",
};
const states: Record<string, string> = {
  observing: "观察中",
  completed: "目标时长已观察，待回听核验",
  stopped: "已停止观察",
  interrupted: "观察已中断",
};
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
                : states[item.state] || "状态待核对"}
            </summary>
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
