/** Extrapolate only a short, healthy heartbeat interval; stale state is not recorded audio. */
export function nativeCaptureSeconds(
  saved: number,
  lastPoll: number,
  now: number,
  healthy: boolean,
) {
  return (
    saved + (healthy ? Math.min(3, Math.max(0, (now - lastPoll) / 1000)) : 0)
  );
}
