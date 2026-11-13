import type { EngineInfo, NativeCapability } from "../../types";
export type CaptureSource =
  "browser" | "macos-system" | "macos-both" | "macos-microphone";
export type Readiness = {
  native: NativeCapability;
  engines: EngineInfo[];
  storage: { writable: boolean; free_bytes: number };
  llm: { configured: boolean; verified: boolean };
  ffmpeg_ready: boolean;
};
export function recordingPlan(
  engineReady: boolean,
  mode: "none" | "quick" | "full",
  auto: boolean,
) {
  return {
    recordOnly: !engineReady,
    completionMode: engineReady ? mode : ("none" as const),
    autoProcess: engineReady && auto,
  };
}
export function captureState(
  source: CaptureSource,
  native: NativeCapability | undefined,
  browser: boolean,
  permission: string,
) {
  if (source === "browser")
    return !browser
      ? { state: "blocked", label: "当前浏览器不支持麦克风录音" }
      : permission === "denied"
        ? { state: "blocked", label: "麦克风权限被拒绝，请在浏览器设置中允许" }
        : permission === "granted"
          ? { state: "ready", label: "麦克风权限已允许" }
          : { state: "pending", label: "首次试录或开始录音时请求麦克风权限" };
  if (native?.ownership && native.ownership.lease !== "free")
    return {
      state: "blocked",
      label: "采集所有权待核对，请先检查正在进行的录音",
    };
  if (!native?.available || !native.supported)
    return { state: "blocked", label: "原生采集器尚未就绪" };
  if (source === "macos-microphone" && !native.microphone_only_supported)
    return { state: "blocked", label: "请更新采集器以使用仅麦克风录音" };
  if (
    (source !== "macos-microphone" && !native.screen_permission) ||
    (source !== "macos-system" && !native.microphone_permission)
  )
    return { state: "pending", label: "首次试录或开始录音时请求系统权限" };
  return { state: "ready", label: "所需系统权限已允许" };
}
