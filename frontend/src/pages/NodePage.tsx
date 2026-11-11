import { useEffect, useState } from "react";
import { t } from "../i18n";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { ago, duration, fmt, volts } from "../lib/format";
import { live } from "../lib/ws";

type Latest = { values: Record<string, number>; ts: number | null };

export function NodePage({ onLogout }: { onLogout: () => void }) {
  const st = useStore((s) => s.status);
  const cfg = useStore((s) => s.config);
  const [m, setM] = useState<Latest | null>(null);

  useEffect(() => {
    const load = () => api.get<Latest>("/api/node/metrics").then(setM).catch(() => {});
    load();
    const off = live.on((ev) => {
      if (ev.type === "metrics" && ev.data?.source === "self") load();
    });
    return () => {
      off();
    };
  }, []);

  const si = st?.self_info;
  const di = st?.device_info;
  const v = m?.values ?? {};

  return (
    <div className="page">
      <div className="page-title">{t("node.title")}</div>
      <div className="grid">
        <div className="card">
          <h3>{t("node.connection")}</h3>
          <div className="kv">
            <span>{t("node.connection")}</span>
            <span className="row">
              <span className={`dot ${st?.state ?? "starting"}`} />
              {t(`status.${st?.state ?? "starting"}`)} · {st ? t(`status.mode.${st.mode}`) : ""}
            </span>
            <span>{t("node.target")}</span>
            <span className="mono">{st?.target ?? "—"}</span>
            <span>{t("node.since")}</span>
            <span>{st ? ago(st.since) : "—"}</span>
            {st?.last_error && (
              <>
                <span>{t("node.last_error")}</span>
                <span className="warn">{st.last_error}</span>
              </>
            )}
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn sm" onClick={() => api.post("/api/node/reconnect")}>{t("status.reconnect")}</button>
          </div>
        </div>

        <div className="card">
          <h3>{t("node.title")}</h3>
          {si ? (
            <div className="kv">
              <span>{t("node.name")}</span>
              <span>{si.name}</span>
              <span>{t("node.key")}</span>
              <span className="mono">{si.public_key}</span>
              <span>{t("node.model")}</span>
              <span>{di?.model ?? "—"}</span>
              <span>{t("node.firmware")}</span>
              <span>{di ? `${di.ver} (${di.fw_build}, v${di.fw_ver})` : "—"}</span>
              <span>{t("node.radio")}</span>
              <span>{t("node.radio_fmt", { f: si.radio_freq, bw: si.radio_bw, sf: si.radio_sf, cr: si.radio_cr })}</span>
              <span>{t("node.tx_power")}</span>
              <span>{si.tx_power} / {si.max_tx_power} dBm</span>
              <span>{t("node.position")}</span>
              <span className="mono">{si.adv_lat || si.adv_lon ? `${si.adv_lat.toFixed(5)}, ${si.adv_lon.toFixed(5)}` : "—"}</span>
              <span>{t("node.max")}</span>
              <span>{di ? t("node.max_fmt", { c: di.max_contacts, ch: di.max_channels }) : "—"}</span>
            </div>
          ) : (
            <div className="muted">{t("common.node_offline")}</div>
          )}
        </div>

        <div className="card">
          <h3>{t("node.metrics")} {m?.ts ? <span className="muted small">· {ago(m.ts)}</span> : null}</h3>
          <div className="stats">
            <div className="stat"><div className="v">{volts(v.battery_mv)}</div><div className="l">{t("node.battery")}</div></div>
            <div className="stat"><div className="v">{duration(v.uptime)}</div><div className="l">{t("node.uptime")}</div></div>
            <div className="stat"><div className="v">{fmt(v.noise_floor, 0)}</div><div className="l">{t("node.noise")}</div></div>
            <div className="stat"><div className="v">{duration(v.tx_air_secs)}</div><div className="l">{t("node.airtime_tx")}</div></div>
            <div className="stat"><div className="v">{duration(v.rx_air_secs)}</div><div className="l">{t("node.airtime_rx")}</div></div>
            <div className="stat"><div className="v">{fmt(v.recv, 0)} / {fmt(v.sent, 0)}</div><div className="l">{t("node.packets")}</div></div>
          </div>
        </div>

        <div className="card">
          <h3>{t("node.settings")}</h3>
          <div className="kv">
            <span>{t("node.retention")}</span>
            <span>{cfg ? t("node.retention_fmt", { m: cfg.retention_messages_days, x: cfg.retention_metrics_days }) : "—"}</span>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn sm" onClick={onLogout}>{t("logout")}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
