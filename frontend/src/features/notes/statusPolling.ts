/** Owns requests only; cancelling observation never sends a recording command. */
export class StatusPolling<T> {
  private controller: AbortController | null = null;
  private generation = 0;
  private closed = false;
  constructor(
    private read: (signal: AbortSignal) => Promise<T>,
    private apply: (value: T) => void,
    private failure: (error: unknown) => void,
    private checking: () => void = () => {},
  ) {}
  async poll(replace = false) {
    if (this.closed || (this.controller && !replace)) return;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const generation = ++this.generation;
    if (replace) this.checking();
    try {
      const value = await this.read(controller.signal);
      if (!this.closed && generation === this.generation) this.apply(value);
    } catch (error) {
      if (!this.closed && generation === this.generation) this.failure(error);
    } finally {
      if (generation === this.generation) this.controller = null;
    }
  }
  close() {
    this.closed = true;
    ++this.generation;
    this.controller?.abort();
    this.controller = null;
  }
}
