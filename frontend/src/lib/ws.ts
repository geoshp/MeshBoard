// Live-канал: WebSocket с автопереподключением.

export type LiveEvent = { type: string; data?: any };
type Handler = (ev: LiveEvent) => void;

export class LiveSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private stateHandlers = new Set<(open: boolean) => void>();
  private retry = 1000;
  private closed = false;
  private timer: number | undefined;
  open = false;

  connect() {
    this.closed = false;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/api/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 1000;
      this.setOpen(true);
    };
    ws.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as LiveEvent;
        if (ev.type === "ping") return;
        this.handlers.forEach((h) => h(ev));
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      this.setOpen(false);
      if (this.closed) return;
      this.timer = window.setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 2, 15000);
    };
  }

  close() {
    this.closed = true;
    window.clearTimeout(this.timer);
    this.ws?.close();
  }

  private setOpen(v: boolean) {
    this.open = v;
    this.stateHandlers.forEach((h) => h(v));
  }

  on(h: Handler) {
    this.handlers.add(h);
    return () => {
      this.handlers.delete(h);
    };
  }

  onState(h: (open: boolean) => void) {
    this.stateHandlers.add(h);
    return () => {
      this.stateHandlers.delete(h);
    };
  }
}

export const live = new LiveSocket();
