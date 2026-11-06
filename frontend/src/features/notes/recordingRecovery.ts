/** Resume polling promptly after backgrounding, back/forward cache or reconnecting. */
export function watchRecordingRecovery(
  recover: () => void,
  page: Pick<
    Document,
    "visibilityState" | "addEventListener" | "removeEventListener"
  > = document,
  target: Pick<Window, "addEventListener" | "removeEventListener"> = window,
) {
  const visible = () => {
    if (page.visibilityState === "visible") recover();
  };
  page.addEventListener("visibilitychange", visible);
  page.addEventListener("resume", visible);
  target.addEventListener("focus", visible);
  target.addEventListener("online", recover);
  target.addEventListener("pageshow", recover);
  return () => {
    page.removeEventListener("visibilitychange", visible);
    page.removeEventListener("resume", visible);
    target.removeEventListener("focus", visible);
    target.removeEventListener("online", recover);
    target.removeEventListener("pageshow", recover);
  };
}
