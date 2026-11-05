/** Silence is a reminder only. Missing callbacks and suspended pages are not silence. */
export class QuietMonitor {
  private since: number | null = null;
  private last: number | null = null;
  private mutedUntil = 0;
  reset() {
    this.since = null;
    this.last = null;
    this.mutedUntil = 0;
  }
  observe(now: number, quiet: boolean, available = true) {
    if (
      !available ||
      !quiet ||
      this.last === null ||
      now - this.last > 10000 ||
      now < this.last
    )
      this.since = null;
    this.last = available ? now : null;
    if (available && quiet && this.since === null) this.since = now;
  }
  snooze(now: number) {
    this.mutedUntil = now + 300000;
  }
  shouldRemind(now: number) {
    return (
      this.since !== null &&
      this.last !== null &&
      now - this.last <= 10000 &&
      now >= this.last &&
      now - this.since >= 300000 &&
      now >= this.mutedUntil
    );
  }
}

export function endCountdown(deadline: number, now: number, visible: boolean) {
  if (!visible || now - deadline > 2000)
    return { state: "cancelled" as const, seconds: 0 };
  if (now >= deadline) return { state: "finish" as const, seconds: 0 };
  return {
    state: "waiting" as const,
    seconds: Math.ceil((deadline - now) / 1000),
  };
}
