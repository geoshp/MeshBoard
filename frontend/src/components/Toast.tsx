import { useEffect, useState } from "react";

let push: ((s: string) => void) | null = null;
export const toast = (s: string) => push?.(s);

export function ToastHost() {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    let timer: number | undefined;
    push = (s) => {
      setMsg(s);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setMsg(null), 3500);
    };
    return () => {
      push = null;
    };
  }, []);
  return msg ? <div className="toast">{msg}</div> : null;
}
