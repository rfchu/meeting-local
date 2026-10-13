/** Plan cumulative previews; only confirmed windows consume captured PCM. */
export function responsivePlan(
  available: number,
  rate: number,
  lastVoice: number,
  lastPreview: number,
  force = false,
): { count: number; confirmed: boolean } | null {
  if (available < rate * (force ? 0.5 : 3)) return null;
  if (force || available >= rate * 15 || available - lastVoice >= rate * 0.8)
    return { count: Math.min(available, rate * 15), confirmed: true };
  const count = Math.min(
    rate * 15,
    Math.floor(available / (rate * 5)) * rate * 5,
  );
  return count > lastPreview ? { count, confirmed: false } : null;
}
export function takeFrames(frames: Float32Array[], count: number) {
  const selected: Float32Array[] = [],
    remaining: Float32Array[] = [];
  for (const frame of frames) {
    if (count <= 0) remaining.push(frame);
    else if (frame.length <= count) {
      selected.push(frame);
      count -= frame.length;
    } else {
      selected.push(frame.slice(0, count));
      remaining.push(frame.slice(count));
      count = 0;
    }
  }
  return { selected, remaining };
}
