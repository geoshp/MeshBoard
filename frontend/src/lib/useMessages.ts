// Загрузка ленты + live-обновления + отметка прочитанного.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { live } from "./ws";
import type { Message } from "./types";

const PAGE = 50;

export function useMessages(
  url: string | null,
  belongs: (m: Message) => boolean,
  readUrl: string | null,
) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const belongsRef = useRef(belongs);
  belongsRef.current = belongs;

  const markRead = useCallback(() => {
    if (readUrl && document.visibilityState === "visible") api.post(readUrl).catch(() => {});
  }, [readUrl]);

  useEffect(() => {
    setMessages([]);
    setHasMore(false);
    if (!url) return;
    let alive = true;
    setLoading(true);
    api.get<Message[]>(`${url}?limit=${PAGE}`).then((list) => {
      if (!alive) return;
      setMessages(list);
      setHasMore(list.length === PAGE);
      markRead();
    }).catch(() => {}).finally(() => alive && setLoading(false));

    const off = live.on((ev) => {
      if (ev.type === "message" && belongsRef.current(ev.data)) {
        setMessages((prev) => (prev.some((m) => m.id === ev.data.id) ? prev : [...prev, ev.data]));
        if (ev.data.direction === "in") markRead();
      } else if (ev.type === "message_update" && ev.data && belongsRef.current(ev.data)) {
        setMessages((prev) => prev.map((m) => (m.id === ev.data.id ? ev.data : m)));
      }
    });
    const onVis = () => markRead();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      off();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [url, markRead]);

  const loadMore = useCallback(() => {
    if (!url || !messages.length) return;
    api.get<Message[]>(`${url}?limit=${PAGE}&before=${messages[0].id}`).then((older) => {
      setMessages((prev) => [...older, ...prev]);
      setHasMore(older.length === PAGE);
    }).catch(() => {});
  }, [url, messages]);

  return { messages, hasMore, loadMore, loading };
}
