import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { t } from "../i18n";
import { clock, fmt, splitHops, utf8Len } from "../lib/format";
import { hopName } from "../lib/contacts";
import type { Contact, Message } from "../lib/types";
import { IconSend } from "./Icons";

export function MessageMeta({ m, contacts }: { m: Message; contacts: Contact[] }) {
  const hops = splitHops(m.path, m.path_hash_size);
  const parts: JSX.Element[] = [<span key="t">{clock(m.ts)}</span>];
  if (m.direction === "in") {
    if (m.snr != null) parts.push(<span key="snr">{t("msg.snr", { v: fmt(m.snr, 2) })}</span>);
    if (m.rssi != null) parts.push(<span key="rssi">{t("msg.rssi", { v: fmt(m.rssi, 0) })}</span>);
    if (hops.length) parts.push(<span key="p" title={m.path ?? ""}>{t("msg.path", { p: hops.map((h) => hopName(h, contacts)).join(" → ") })}</span>);
    else if (m.path_len === -1) parts.push(<span key="d">{t("msg.direct")}</span>);
    else if (m.path_len === 0) parts.push(<span key="z">{t("msg.zero_hops")}</span>);
    else if (m.path_len != null) parts.push(<span key="h">{t("msg.hops", { n: m.path_len })}</span>);
  } else if (m.status) {
    const key = m.kind === "dm" ? `dm.status.${m.status}` : `chan.status.${m.status}`;
    parts.push(<span key="s" className={m.status === "failed" ? "failed" : m.status === "delivered" ? "accent" : ""}>{t(key)}{m.status === "delivered" ? " ✓✓" : m.status === "sent" ? " ✓" : ""}</span>);
    if (m.trip_ms) parts.push(<span key="trip">{t("dm.trip", { v: m.trip_ms })}</span>);
    if (m.attempts && m.attempts > 1) parts.push(<span key="a">{t("dm.attempts", { n: m.attempts })}</span>);
  }
  return <div className="meta">{parts}</div>;
}

export function Feed({
  messages, contacts, showSender, hasMore, onLoadMore, empty,
}: {
  messages: Message[]; contacts: Contact[]; showSender: boolean; hasMore: boolean; onLoadMore: () => void; empty: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const lastId = useRef<number | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const newest = messages.length ? messages[messages.length - 1].id : null;
    if (newest !== lastId.current && stick.current) el.scrollTop = el.scrollHeight;
    lastId.current = newest;
  }, [messages]);

  return (
    <div className="feed" ref={ref} onScroll={(e) => {
      const el = e.currentTarget;
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }}>
      {hasMore && <button className="btn sm" style={{ alignSelf: "center" }} onClick={onLoadMore}>{t("common.load_more")}</button>}
      {messages.length === 0 && <div className="center-empty">{empty}</div>}
      {messages.map((m) => (
        <div key={m.id} className={`msg ${m.direction}`}>
          {showSender && m.direction === "in" && <div className="who">{m.sender_name ?? "?"}</div>}
          <div className="text">{m.text}</div>
          <MessageMeta m={m} contacts={contacts} />
        </div>
      ))}
    </div>
  );
}

export function Composer({ placeholder, maxBytes, disabled, onSend }: {
  placeholder: string; maxBytes: number; disabled: boolean; onSend: (text: string) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const n = utf8Len(text.trim());
  const over = n > maxBytes;

  useEffect(() => {
    const el = ta.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 120) + "px";
    }
  }, [text]);

  async function send() {
    if (!text.trim() || over || busy || disabled) return;
    setBusy(true);
    const ok = await onSend(text.trim());
    setBusy(false);
    if (ok) setText("");
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !("ontouchstart" in window)) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="composer">
      <div className="grow" style={{ position: "relative" }}>
        <textarea ref={ta} className="input" rows={1} placeholder={disabled ? t("common.node_offline") : placeholder}
          value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} disabled={disabled} />
        {n > maxBytes * 0.7 && <span className={`counter ${over ? "err" : ""}`}>{n}/{maxBytes}</span>}
      </div>
      <button className="btn primary icon" onClick={send} disabled={!text.trim() || over || busy || disabled} aria-label={t("common.send")}>
        <IconSend />
      </button>
    </div>
  );
}
