/** Visible-document supplement; macOS protection is held separately by the local server. */
export class ScreenAwake {
  private sentinel: WakeLockSentinel | null = null;
  private pending = false;
  private stopped = false;
  private changed = () => {
    void this.ensure();
  };
  constructor(
    private page: Pick<
      Document,
      "visibilityState" | "addEventListener" | "removeEventListener"
    >,
    private request: (() => Promise<WakeLockSentinel>) | undefined,
    private report: (active: boolean) => void,
  ) {
    page.addEventListener("visibilitychange", this.changed);
  }
  async ensure() {
    if (
      this.stopped ||
      this.pending ||
      this.sentinel ||
      !this.request ||
      this.page.visibilityState !== "visible"
    )
      return;
    this.pending = true;
    try {
      const lock = await this.request();
      if (
        this.stopped ||
        this.page.visibilityState !== "visible" ||
        lock.released
      ) {
        await lock.release();
        return;
      }
      this.sentinel = lock;
      this.report(true);
      lock.addEventListener(
        "release",
        () => {
          if (this.sentinel === lock) this.sentinel = null;
          if (!this.stopped) this.report(false);
        },
        { once: true },
      );
    } catch {
      if (!this.stopped) this.report(false);
    } finally {
      this.pending = false;
    }
  }
  stop() {
    this.stopped = true;
    this.page.removeEventListener("visibilitychange", this.changed);
    const lock = this.sentinel;
    this.sentinel = null;
    if (lock) void lock.release().catch(() => {});
  }
}
