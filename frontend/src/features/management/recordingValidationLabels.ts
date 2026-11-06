export const observationMarkers = {
  background_return: "从后台返回",
  device_changed: "设备已变化",
  other_app_changed: "其他软件已开启／退出",
  pause_resume: "已暂停／恢复",
  wake_return: "系统唤醒后返回",
};
export const observationFlags: Record<string, string> = {
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
  default_input_changed: "默认输入设备变化",
  default_output_changed: "默认输出设备变化",
  audio_device_changed: "音频设备连接状态变化",
  native_buffers_dropped: "原生音频缓冲丢弃",
  audio_format_changed: "音频格式变化",
  audio_timeline_padding: "单次采样间文件补空至少 0.1 秒",
  configured_mic_differs_from_default: "已配置麦克风与当前默认设备不同",
  native_device_monitor_limited: "部分设备监听未启用",
  native_device_read_failed: "设备信息读取失败",
  native_failure_reported: "采集器报告错误阶段",
  native_counters_reset: "内部计数出现回退",
  audio_file_shrank: "音轨文件大小回退",
};

export function observationEndReason(reason: string | null | undefined) {
  const reasons: Record<string, string> = {
    target_elapsed: "已达到目标观察时长；录音不受影响。",
    user_stopped_observation: "你已停止观察；此操作不会停止录音。",
    recording_stopped:
      "采集器已确认停止，观察随录音结束。仍需核对保存结果与回听。",
    capture_not_running: "未观察到运行中的采集进程，无法确认是否正常停止。",
    capture_ended: "采集状态已结束或中断，未获得正常停止确认。",
    service_restart: "服务重启后未接续观察；中间状态未知。",
    service_shutdown: "服务关闭时结束观察；不代表录音已正常保存。",
    observer_not_attached: "观察未能接续计时，已结束；中间状态未知。",
  };
  return reason
    ? reasons[reason] || "结束原因未识别，请核对保留采样。"
    : "未记录结束原因，请核对保留采样。";
}

export function observationStateLabel(state: string, reason?: string | null) {
  if (state === "stopped" && reason === "recording_stopped")
    return "录音已结束，观察已停止";
  return (
    (
      {
        observing: "观察中",
        completed: "目标时长已观察，待回听核验",
        stopped: "已停止观察",
        interrupted: "观察已中断",
      } as Record<string, string>
    )[state] || "状态待核对"
  );
}
