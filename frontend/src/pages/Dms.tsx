import { useEffect, useMemo, useState } from "react";
import { t } from "../i18n";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { href, navigate } from "../lib/router";
import { ago, splitHops } from "../lib/format";
import { contactName, hopName, useContacts } from "../lib/contacts";
import { useMessages } from "../lib/useMessages";
import { errText } from "../lib/errors";
import { live } from "../lib/ws";
import type { Contact, Message } from "../lib/types";
import { Composer, Feed } from "../components/MessageView";
import { IconBack } from "../components/Icons";
import { toast } from "../components/Toast";

type Conv = { contact_key: string; last_ts: number; unread: number; last_text: string };

export function routeText(c: Contact, contacts: Contact[]): string {
  if (c.out_path_len < 0) return t("contact.route.flood");
  if (c.out_path_len === 0) return t("contact.route.direct");
  const hops = splitHops(c.out_path, c.out_path_hash_mode + 1).map((h) => hopName(h, contacts));
  return t("contact.route.hops", { n: c.out_path_len, p: hops.join(" → ") });
}

function initials(name: string) {
  return (name.trim()[0] ?? "?").toUpperCase();
}

export function DmsPage({ param }: { param?: string }) {
  const contacts = useContacts();
  const unread = useStore((s) => s.unread);
  const connected = useStore((s) => s.status?.state === "connected");
  const [convs, setConvs] = useState<Conv[]>([]);
  const [maxText, setMaxText] = useState(150);
  const [q, setQ] = useState("");
  const key = param || null;
  const byKey = useMemo(() => new Map(contacts.map((c) => [c.public_key, c])), [contacts]);
  const current = key ? byKey.get(key) : undefined;

  useEffect(() => {
    const load = () => api.get<{ conversations: Conv[]; max_text: number }>("/api/dm").then((r) => {
      setConvs(r.conversations);
      setMaxText(r.max_text);
    }).catch(() => {});
    load();
    const off = live.on((ev) => {
      if ((ev.type === "message" && ev.data?.kind === "dm") || ev.type === "read") load();
    });
    return () => {
      off();
    };
  }, []);

  const { messages, hasMore, loadMore } = useMessages(
    key ? `/api/dm/${key}/messages` : null,
    (m: Message) => m.kind === "dm" && m.contact_key === key,
    key ? `/api/dm/${key}/read` : null,
  );

  const withConv = new Set(convs.map((c) => c.contact_key));
  const others = contacts
    .filter((c) => !withConv.has(c.public_key))
    .filter((c) => !q || c.name.toLowerCase().includes(q.toLowerCase()) || c.public_key.startsWith(q.toLowerCase()))
    .sort((a, b) => (a.type === 1 ? 0 : 1) - (b.type === 1 ? 0 : 1) || b.last_heard - a.last_heard);

  async function send(text: string) {
    try {
      await api.post(`/api/dm/${key}/messages`, { text });
      return true;
    } catch (e) {
      toast(errText(e));
      return false;
    }
  }

  return (
    <div className={`split ${key ? "has-detail" : ""}`}>
      <div className="list">
        {convs.length > 0 && <div className="list-head">{t("dm.conversations")}</div>}
        {convs.map((cv) => {
          const c = byKey.get(cv.contact_key);
          const n = unread.dms[cv.contact_key] ?? 0;
          return (
            <a key={cv.contact_key} href={href("dm", cv.contact_key)} className={`list-item ${cv.contact_key === key ? "on" : ""}`}>
              <div className="avatar">{initials(contactName(c))}</div>
              <div className="grow">
                <div className="row"><span className="grow ellipsis">{contactName(c, cv.contact_key.slice(0, 8))}</span><span className="small faint">{ago(cv.last_ts)}</span></div>
                <div className="small muted ellipsis">{cv.last_text}</div>
              </div>
              {n > 0 && <span className="badge">{n}</span>}
            </a>
          );
        })}
        <div className="list-head">{t("dm.contacts")}</div>
        <div style={{ padding: "0 14px 8px" }}>
          <input className="input" placeholder={t("dm.search")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {others.map((c) => (
          <a key={c.public_key} href={href("dm", c.public_key)} className={`list-item ${c.public_key === key ? "on" : ""}`}>
            <div className="avatar">{initials(contactName(c))}</div>
            <div className="grow">
              <div className="ellipsis">{contactName(c)}</div>
              <div className="small muted ellipsis">{t(`ctype.${c.type}`)} · {ago(c.last_heard || c.last_advert)}</div>
            </div>
          </a>
        ))}
      </div>
      <div className="detail">
        {key ? (
          <>
            <div className="detail-head">
              <button className="btn ghost icon mobile-only" onClick={() => navigate("dm")} aria-label={t("common.back")}><IconBack /></button>
              <div className="avatar">{initials(contactName(current))}</div>
              <div className="grow">
                <div className="ellipsis" style={{ fontWeight: 650 }}>{contactName(current, key.slice(0, 8))}</div>
                {current && (
                  <div className="small muted ellipsis">
                    {t(`ctype.${current.type}`)} · {routeText(current, contacts)}{current.on_device ? "" : ` · ${t("dm.not_on_device")}`}
                  </div>
                )}
              </div>
            </div>
            <Feed messages={messages} contacts={contacts} showSender={false} hasMore={hasMore} onLoadMore={loadMore} empty={t("dm.no_messages")} />
            <Composer placeholder={t("dm.placeholder", { name: contactName(current, "") })} maxBytes={maxText} disabled={!connected || !current} onSend={send} />
          </>
        ) : (
          <div className="center-empty">{t("dm.select")}</div>
        )}
      </div>
    </div>
  );
}
