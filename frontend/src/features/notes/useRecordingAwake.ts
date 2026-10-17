import { useEffect, useState } from "react";
import { api } from "../../api";
import { ScreenAwake } from "./screenAwake";
import { queueAwake } from "./awakeQueue";
import { watchRecordingRecovery } from "./recordingRecovery";
export function useRecordingAwake(
  active: boolean,
  rid: string,
  enabled: boolean,
) {
  const [browser, setBrowser] = useState(false),
    [native, setNative] = useState(false),
    [checking, setChecking] = useState(false);
  useEffect(() => {
    setBrowser(false);
    setNative(false);
    setChecking(false);
    if (!active || !rid || !enabled) return;
    let live = true,
      busy = false;
    setChecking(true);
    const screen = new ScreenAwake(
      document,
      navigator.wakeLock
        ? () => navigator.wakeLock.request("screen")
        : undefined,
      (value) => {
        if (live) setBrowser(value);
      },
    );
    const renew = async () => {
      if (!live) return;
      void screen.ensure();
      if (busy) return;
      busy = true;
      try {
        const status = await queueAwake(rid, () =>
          live
            ? api.recordingKeepAwake(rid, true)
            : Promise.resolve({ active: false }),
        );
        if (live) setNative(status.active);
      } catch {
        if (live) setNative(false);
      } finally {
        busy = false;
        if (live) setChecking(false);
      }
    };
    void renew();
    const timer = window.setInterval(() => void renew(), 20000);
    const unwatch = watchRecordingRecovery(() => void renew());
    return () => {
      live = false;
      screen.stop();
      window.clearInterval(timer);
      unwatch();
      void queueAwake(rid, () => api.recordingKeepAwake(rid, false)).catch(
        () => {},
      );
    };
  }, [active, rid, enabled]);
  if (!enabled) return "保持亮屏已关闭";
  if (!active || !rid) return "录音时自动保持亮屏";
  if (native) return "已保持亮屏 · 本机防空闲休眠";
  if (browser) return "已保持亮屏 · 浏览器保护";
  return checking
    ? "正在启用亮屏保护…"
    : "亮屏保护暂不可用，请保持页面可见并检查节能设置";
}
