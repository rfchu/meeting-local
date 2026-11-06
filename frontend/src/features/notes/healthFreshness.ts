/** Local receipt times only: a cached snapshot is not evidence of current capture. */
export function healthFreshness(
  updatedAt: number,
  now: number,
  failed: boolean,
  rechecking = false,
) {
  if (rechecking) return "checking";
  if (failed) return "unavailable";
  if (!Number.isFinite(updatedAt) || updatedAt <= 0) return "checking";
  if (!Number.isFinite(now) || now < updatedAt || now - updatedAt > 20000)
    return "stale";
  return "current";
}
