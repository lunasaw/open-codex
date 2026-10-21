export const STREAM_IDLE_TIMEOUT_MS = 45_000;
export const STREAM_REFRESH_INTERVAL_MS = 5 * 60_000;

// Own only the browser connection; callers retain their cursor and UI state.
export class StreamConnection {
  private attempt?: AbortController;
  private wakeRetry?: () => void;
  refreshRequested = false;

  constructor(private readonly signal: AbortSignal) {
    signal.addEventListener("abort", this.dispose, { once: true });
    if (typeof window !== "undefined") {
      window.addEventListener("online", this.refresh);
      window.addEventListener("focus", this.refresh);
      window.addEventListener("pageshow", this.refresh);
      document.addEventListener("visibilitychange", this.visible);
    }
    if (signal.aborted) this.dispose();
  }

  private visible = () => {
    if (document.visibilityState === "visible") this.refresh();
  };

  private refresh = () => {
    this.refreshRequested = true;
    this.attempt?.abort();
    this.wakeRetry?.();
  };

  async run(operation: (signal: AbortSignal, activity: () => void) => Promise<void>) {
    const attempt = new AbortController();
    this.attempt = attempt;
    this.refreshRequested = false;
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const activity = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => attempt.abort(), STREAM_IDLE_TIMEOUT_MS);
    };
    activity();
    const refreshTimer = setTimeout(this.refresh, STREAM_REFRESH_INTERVAL_MS);
    try {
      if (this.signal.aborted) attempt.abort();
      await operation(attempt.signal, activity);
    } finally {
      clearTimeout(idleTimer);
      clearTimeout(refreshTimer);
      this.attempt = undefined;
      attempt.abort();
    }
  }

  async wait(delay: number) {
    if (this.refreshRequested || this.signal.aborted) return;
    await new Promise<void>((resolve) => {
      const done = () => {
        clearTimeout(timer);
        this.wakeRetry = undefined;
        resolve();
      };
      const timer = setTimeout(done, delay);
      this.wakeRetry = done;
    });
  }

  dispose = () => {
    this.attempt?.abort();
    this.wakeRetry?.();
    this.signal.removeEventListener("abort", this.dispose);
    if (typeof window !== "undefined") {
      window.removeEventListener("online", this.refresh);
      window.removeEventListener("focus", this.refresh);
      window.removeEventListener("pageshow", this.refresh);
      document.removeEventListener("visibilitychange", this.visible);
    }
  };
}
