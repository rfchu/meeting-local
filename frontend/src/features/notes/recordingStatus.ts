import type { RecordingRuntime, RecordingHealth } from "../../types";
import { coordinateRecording } from "./recordingCoordinator";
import { clock } from "../../utils";
export function lifecycleLabel(code: string) {
  const events: Record<string, string> = {
    "page.detached": "页面已失联，原生采集继续托管，请返回核对音轨",
    "page.returned": "页面已恢复连接",
    "service.observation_gap": "服务观测出现较长间隔，已重新核对录音状态",
    "service.started": "服务已启动",
    "service.shutdown_requested": "服务开始关闭",
    "service.clean_shutdown": "服务已正常关闭",
    "service.previous_unclosed": "上次运行未留下正常关闭记录，原因待核查",
    "service.shutdown_failed": "服务关闭未完整完成",
    "recording.created": "录音会话已创建",
    "capture.started": "采集已启动",
    "capture.resumed": "采集已继续",
    "capture.stop_requested": "已请求停止采集",
    "capture.exited": "观察到采集进程退出",
    "capture.reconciled": "遗留会话已标记中断",
    "storage.completed": "会议文件已保存",
  };
  if (events[code]) return events[code];
  const [part, state] = code.split(".");
  const parts: Record<string, string> = {
    system_input: "系统音轨",
    microphone_input: "麦克风音轨",
    capture: "采集",
    storage: "文件",
    asr: "实时识别",
  };
  const states: Record<string, string> = {
    receiving: "正在接收音频",
    quiet: "音频较安静",
    no_data: "超过 10 秒未收到音频，请检查设备和音源",
    stalled: "采集心跳未更新",
    inactive: "未在采集",
    recording: "正在运行",
    starting: "准备中",
    paused: "已暂停",
    unknown: "状态未知",
    interrupted: "已中断",
    stopped: "已结束",
    waiting: "等待中",
    running: "正在识别",
    behind: "存在积压",
    failed: "发生失败",
    disabled: "未开启",
    writing: "持续落盘中",
    unconfirmed: "保存状态待核对，采集上报与文件更新不一致",
    available: "已有内容已落盘",
    partial: "存在缺段",
    saved: "已保存",
    missing: "保存文件待检查",
  };
  return `${parts[part] || "运行"}：${states[state] || "状态已变化"}`;
}

const phases: Record<string, string> = {
  starting: "正在准备",
  recording: "录音中",
  paused: "已暂停",
  unknown: "状态未知",
  interrupted: "录音已中断",
  saving: "采集已结束，等待保存或收尾",
  saved: "会议已保存",
};
const inputs: Record<string, string> = {
  starting: "等待采集启动",
  recording: "正在采集",
  paused: "采集已暂停",
  unknown: "心跳或连接异常，不能确认继续采集",
  interrupted: "采集已中断，已有音轨保留",
  stopping: "正在停止采集",
  stopped: "采集已结束",
};
export function recordingActive(health: RecordingHealth | undefined) {
  if (health?.native_running) return true;
  const runtime = health?.runtime;
  if (runtime?.schema_version != null && runtime.schema_version !== 1)
    return false;
  return ["starting", "recording", "paused"].includes(
    runtime?.capture.state || "",
  );
}

export function controlPresentation(
  runtime: RecordingRuntime | undefined,
  unavailable: boolean,
  feedback?: string,
) {
  if (runtime?.control?.status === "unconfirmed")
    return {
      label: unavailable ? "上次记录：控制结果待确认" : "控制结果待确认",
      message:
        "上一次录音操作尚未得到确认。请核对当前采集状态，可回原页面重查同一操作，必要时主动结束录音。刷新只更新状态，不会重新执行或清除待确认操作。",
      persistent: true,
    };
  if (feedback === "conflict")
    return {
      label: "操作未执行：状态已变化",
      message:
        "本次操作遇到状态冲突或已不再适用。请核对最新状态后，再决定下一步；不会自动重试旧操作。",
      persistent: false,
    };
  if (feedback === "unconfirmed" || feedback === "unavailable")
    return {
      label: "本次操作结果待核对",
      message:
        "暂时无法确认本次操作的结果，已有音频保留。请核对最新状态；同一操作可返回原页面重查，必要时主动结束录音。",
      persistent: false,
    };
  return null;
}

export function recordingStatus(
  runtime: RecordingRuntime | undefined,
  local: string,
  unavailable: boolean,
) {
  const coordinated = coordinateRecording(runtime, local, unavailable);
  const phase = coordinated.phase;
  const state = coordinated.capture;
  unavailable = coordinated.unavailable;

  return {
    phase,
    label:
      local === "failed" && phase === "saving"
        ? "保存待重试"
        : local === "saving" && phase === "saving"
          ? "正在保存会议"
          : phase === "recording" && runtime?.storage.state === "unconfirmed"
            ? "录音保存待核对"
            : phase === "recording" &&
                runtime?.capture.tracks?.some((t) => t.state === "no_data")
              ? "录音输入待检查"
              : !unavailable && runtime?.control?.status === "unconfirmed"
                ? "控制结果待确认"
                : phases[phase] || "正在核对状态",
    input:
      state === "recording" &&
      runtime?.capture.tracks?.some((t) => t.state === "no_data")
        ? "部分音轨超过 10 秒未收到音频，请检查设备和音源"
        : state
          ? inputs[state] || "采集状态待核对"
          : null,
    asr: unavailable
      ? "识别状态暂不可用，已确认结果保留"
      : runtime
        ? {
            disabled: "本次未开启实时识别",
            running: "正在识别，原音独立保存",
            waiting: "等待后续音频",
            behind: "识别有积压，音频仍按采集状态保存",
            failed: "识别失败，已有原音与确认结果保留",
            stopped: "实时识别已结束，缺段可会后补识别",
          }[runtime.asr.state] || "识别状态待核对"
        : null,
    storage: unavailable
      ? "文件状态暂不可用，已有内容保留"
      : runtime
        ? (
            {
              saved: "会议文件已保存",
              missing: "保存文件待检查",
              partial: "文件分段待补齐",
              writing: "音频持续落盘中",
              available: "已有音频已落盘",
              waiting: "等待音频落盘",
              failed: "文件写盘失败，已有内容保留",
              unconfirmed: "文件保存待核对",
            } as Record<string, string>
          )[runtime.storage.state] || "文件状态待核对"
        : "文件状态待核对",
    savedPosition:
      runtime?.storage.last_saved_seconds != null
        ? `音轨已落盘末端 ${clock(runtime.storage.last_saved_seconds)}`
        : runtime
          ? `已落盘 ${runtime.storage.saved_chunks} 段，时间位置待合并核对`
          : "",
  };
}
