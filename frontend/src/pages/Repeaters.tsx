import { useEffect, useState } from "react";
import { t } from "../i18n";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { navigate } from "../lib/router";
import { ago, duration, fmt, volts } from "../lib/format";
import { useContacts } from "../lib/contacts";
import { errText } from "../lib/errors";
import type { Contact } from "../lib/types";
import { routeText } from "./Dms";
import { toast } from "../components/Toast";

type Poll = {
  id: number; ts: number; login_ok: number | null; error: string | null;
  status: Record<string, number> | null;
  telemetry: { channel: number; type: string; value: unknown }[] | null;
};
type Repeater = Contact & {
  own: boolean; password_configured: boolean; polling: boolean;
  last_poll: Poll | null; last_ok: Poll | null; next_poll: number | null; neighbours_ts: number | null;
  neighbours: { prefix: string; snr: number; secs_ago: number; public_key: string | null; name: string | null }[];
};

function tmValue(v: unknown): string {
  if (typeof v === "number") return fmt(v, 2);
  if (v && typeof v === "object") return Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(", ");
  return String(v);
}

function RepeaterCard({ r, contacts }: { r: Repeater; contacts: Contact[] }) {
  const s = r.last_ok?.status;
  const lp = r.last_poll;
  async function toggleOwn(own: boolean) {
    await api.put(`/api/repeaters/${r.public_key}/own`, { own }).catch((e) => toast(errText(e)));
  }
  async function pollNow() {
    await api.post(`/api/repeaters/${r.public_key}/poll`).catch((e) => toast(errText(e)));
  }
  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 8 }}>
        <div className="grow">
          <div style={{ fontWeight: 650 }} className="ellipsis">{r.name || r.public_key.slice(0, 8)}</div>
          <div className="small muted ellipsis">
            <span className="mono">{r.public_key.slice(0, 8)}</span> · {t(`ctype.${r.type}`)} · {t("rpt.heard_ago", { t: ago(r.last_heard || r.last_advert) })}
          </div>
        </div>
        <label className="row small muted" title={t("rpt.mark_own")}>
          <span className="switch"><input type="checkbox" checked={r.own} onChange={(e) => toggleOwn(e.target.checked)} /><span /></span>
        </label>
      </div>
      <div className="small muted" style={{ marginBottom: 8 }}>{t("contact.route")}: {routeText(r, contacts)}</div>

      {r.own && (
        <>
          <div className="row wrap small" style={{ marginBottom: 10, gap: 6 }}>
            <span className="chip">{t("rpt.last_poll")}: {lp ? ago(lp.ts) : t("common.never")}</span>
            {r.next_poll && <span className="chip">{t("rpt.next_poll")}: {new Date(r.next_poll * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</span>}
            <span className={`chip ${lp?.login_ok ? "accent" : ""}`}>
              {t("rpt.login")}: {!r.password_configured ? t("rpt.no_password") : lp?.login_ok == null ? "—" : lp.login_ok ? t("rpt.login_ok") : t("rpt.login_fail")}
            </span>
            {lp?.error && <span className="chip warn">{t("rpt.error", { e: lp.error })}</span>}
          </div>
          {s ? (
            <div className="stats" style={{ marginBottom: 10 }}>
              <div className="stat"><div className="v">{volts(s.bat)}</div><div className="l">{t("node.battery")}</div></div>
              <div className="stat"><div className="v">{duration(s.uptime)}</div><div className="l">{t("node.uptime")}</div></div>
              <div className="stat"><div className="v">{fmt(s.last_snr, 2)}</div><div className="l">SNR / RSSI {fmt(s.last_rssi, 0)}</div></div>
              <div className="stat"><div className="v">{fmt(s.noise_floor, 0)}</div><div className="l">{t("node.noise")}</div></div>
              <div className="stat"><div className="v">{duration(s.airtime)}</div><div className="l">{t("node.airtime_tx")}</div></div>
              <div className="stat"><div className="v">{duration(s.rx_airtime)}</div><div className="l">{t("node.airtime_rx")}</div></div>
              <div className="stat"><div className="v">{fmt(s.nb_recv, 0)} / {fmt(s.nb_sent, 0)}</div><div className="l">{t("node.packets")}</div></div>
              <div className="stat"><div className="v">{fmt(s.tx_queue_len, 0)}</div><div className="l">{t("rpt.queue")}</div></div>
              <div className="stat"><div className="v">{fmt(s.direct_dups, 0)} / {fmt(s.flood_dups, 0)}</div><div className="l">{t("rpt.dups")}</div></div>
            </div>
          ) : (
            <div className="muted small" style={{ marginBottom: 10 }}>{t("rpt.no_data")}</div>
          )}
          {r.last_ok?.telemetry && r.last_ok.telemetry.length > 0 && (
            <>
              <div className="list-head" style={{ padding: "4px 0" }}>{t("rpt.telemetry")}</div>
              <div className="kv small" style={{ marginBottom: 10 }}>
                {r.last_ok.telemetry.map((x, i) => [
                  <span key={`k${i}`}>{x.type} #{x.channel}</span>,
                  <span key={`v${i}`}>{tmValue(x.value)}</span>,
                ])}
              </div>
            </>
          )}
          {r.neighbours.length > 0 && (
            <>
              <div className="list-head" style={{ padding: "4px 0" }}>{t("rpt.neighbours")} · {ago(r.neighbours_ts)}</div>
              <table className="table" style={{ marginBottom: 10 }}>
                <tbody>
                  {r.neighbours.map((n) => (
                    <tr key={n.prefix}>
                      <td className="ellipsis" style={{ maxWidth: 160 }}>{n.name ?? <span className="mono">{n.prefix}</span>}</td>
                      <td>SNR {fmt(n.snr, 2)}</td>
                      <td className="muted">{duration(n.secs_ago)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          <div className="row wrap">
            <button className="btn sm" disabled={r.polling} onClick={pollNow}>{r.polling ? t("rpt.polling") : t("rpt.poll_now")}</button>
            <button className="btn sm" onClick={() => navigate("charts", r.public_key)}>{t("nav.charts")}</button>
            <button className="btn sm" onClick={() => navigate("map", r.public_key)}>{t("nav.map")}</button>
          </div>
        </>
      )}
    </div>
  );
}

export function RepeatersPage() {
  const rev = useStore((s) => s.repeatersRev + s.contactsRev);
  const contacts = useContacts();
  const [data, setData] = useState<{ repeaters: Repeater[]; interval_min: number } | null>(null);
  const [interval, setInterval_] = useState("");

  useEffect(() => {
    api.get<{ repeaters: Repeater[]; interval_min: number }>("/api/repeaters").then((d) => {
      setData(d);
      setInterval_((v) => v || String(d.interval_min));
    }).catch(() => {});
  }, [rev]);

  async function saveInterval() {
    const n = Number(interval);
    if (!n || n < 1) return;
    await api.put("/api/settings", { repeater_poll_min: n }).catch((e) => toast(errText(e)));
  }

  const own = data?.repeaters.filter((r) => r.own) ?? [];
  const others = data?.repeaters.filter((r) => !r.own) ?? [];

  return (
    <div className="page">
      <div className="page-title">{t("rpt.title")}</div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row wrap">
          <span className="muted">{t("rpt.interval")}</span>
          <input className="input" style={{ width: 90 }} inputMode="numeric" value={interval} onChange={(e) => setInterval_(e.target.value.replace(/\D/g, ""))} />
          <button className="btn sm" onClick={saveInterval} disabled={!interval || Number(interval) === data?.interval_min}>{t("common.save")}</button>
        </div>
        <div className="small faint" style={{ marginTop: 8 }}>{t("rpt.readonly")}</div>
      </div>
      {data === null && <div className="muted">{t("common.loading")}</div>}
      <div className="list-head" style={{ paddingLeft: 0 }}>{t("rpt.own")}</div>
      {own.length === 0 && data && <div className="card muted">{t("rpt.none_own")}</div>}
      <div className="grid">{own.map((r) => <RepeaterCard key={r.public_key} r={r} contacts={contacts} />)}</div>
      {others.length > 0 && <div className="list-head" style={{ paddingLeft: 0, marginTop: 14 }}>{t("rpt.others")}</div>}
      <div className="grid">{others.map((r) => <RepeaterCard key={r.public_key} r={r} contacts={contacts} />)}</div>
    </div>
  );
}
