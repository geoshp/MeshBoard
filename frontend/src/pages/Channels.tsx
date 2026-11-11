import { useEffect, useState } from "react";
import { t } from "../i18n";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { href, navigate } from "../lib/router";
import { ago } from "../lib/format";
import { useContacts } from "../lib/contacts";
import { useMessages } from "../lib/useMessages";
import { errText } from "../lib/errors";
import type { Channel, Message } from "../lib/types";
import { Composer, Feed } from "../components/MessageView";
import { IconBack } from "../components/Icons";
import { toast } from "../components/Toast";

export function ChannelsPage({ param }: { param?: string }) {
  const rev = useStore((s) => s.channelsRev);
  const unread = useStore((s) => s.unread);
  const connected = useStore((s) => s.status?.state === "connected");
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [maxText, setMaxText] = useState(140);
  const contacts = useContacts();
  const idx = param != null && param !== "" ? Number(param) : null;
  const current = channels?.find((c) => c.idx === idx) ?? null;

  useEffect(() => {
    api.get<{ channels: Channel[]; max_text: number }>("/api/channels").then((r) => {
      setChannels(r.channels);
      setMaxText(r.max_text);
    }).catch(() => setChannels([]));
  }, [rev]);

  // на широком экране сразу открываем первый канал
  useEffect(() => {
    if (idx == null && channels?.length && window.matchMedia("(min-width: 720px)").matches) navigate("channels", channels[0].idx);
  }, [idx, channels]);

  const { messages, hasMore, loadMore } = useMessages(
    idx != null ? `/api/channels/${idx}/messages` : null,
    (m: Message) => m.kind === "chan" && m.channel_idx === idx,
    idx != null ? `/api/channels/${idx}/read` : null,
  );

  async function send(text: string) {
    try {
      await api.post(`/api/channels/${idx}/messages`, { text });
      return true;
    } catch (e) {
      toast(errText(e));
      return false;
    }
  }

  return (
    <div className={`split ${idx != null ? "has-detail" : ""}`}>
      <div className="list">
        <div className="list-head">{t("chan.title")}</div>
        {channels === null && <div className="center-empty">{t("common.loading")}</div>}
        {channels?.length === 0 && <div className="center-empty">{t("chan.none")}</div>}
        {channels?.map((c) => {
          const n = unread.channels[String(c.idx)] ?? 0;
          return (
            <a key={c.idx} href={href("channels", c.idx)} className={`list-item ${c.idx === idx ? "on" : ""}`}>
              <div className="avatar chan">{c.name.startsWith("#") ? "#" : c.idx}</div>
              <div className="grow">
                <div className="ellipsis">{c.name}</div>
                <div className="small muted">{c.last_ts ? ago(c.last_ts) : t("chan.no_messages")}</div>
              </div>
              {n > 0 && <span className="badge">{n}</span>}
            </a>
          );
        })}
      </div>
      <div className="detail">
        {current ? (
          <>
            <div className="detail-head">
              <button className="btn ghost icon mobile-only" onClick={() => navigate("channels")} aria-label={t("common.back")}><IconBack /></button>
              <div className="avatar chan">{current.name.startsWith("#") ? "#" : current.idx}</div>
              <div className="grow ellipsis" style={{ fontWeight: 650 }}>{current.name}</div>
            </div>
            <Feed messages={messages} contacts={contacts} showSender hasMore={hasMore} onLoadMore={loadMore} empty={t("chan.no_messages")} />
            <Composer placeholder={t("chan.placeholder", { name: current.name })} maxBytes={maxText} disabled={!connected} onSend={send} />
          </>
        ) : (
          <div className="center-empty">{t("chan.select")}</div>
        )}
      </div>
    </div>
  );
}
