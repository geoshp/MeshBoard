import { useEffect, useMemo, useState } from "react";
import { t } from "../i18n";
import { api } from "../lib/api";
import { navigate } from "../lib/router";
import { live } from "../lib/ws";
import { C, EChart, baseOption } from "../components/EChart";

const PERIODS = ["1h", "6h", "24h", "7d", "30d"] as const;
type Series = Record<string, [number, number | null][]>;
type Source = { key: string; name: string | null };

const ms = (pts: [number, number | null][] | undefined, f: (v: number) => number = (v) => v) =>
  (pts ?? []).map(([ts, v]) => [ts * 1000, v == null ? null : +f(v).toFixed(3)]);

function line(name: string, data: unknown[], color: string, extra: object = {}) {
  return { name, type: "line", data, showSymbol: false, smooth: 0.2, connectNulls: false, lineStyle: { width: 2, color }, itemStyle: { color }, ...extra };
}

function bar(name: string, data: unknown[], color: string, stack?: string) {
  return { name, type: "bar", data, stack, itemStyle: { color, borderRadius: stack ? 0 : [3, 3, 0, 0] }, barMaxWidth: 18 };
}

function Card({ title, empty, children }: { title: string; empty: boolean; children: React.ReactNode }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {empty ? <div className="muted small" style={{ padding: "30px 0", textAlign: "center" }}>{t("charts.no_data")}</div> : children}
    </div>
  );
}

const hasData = (...arrs: unknown[][]) => arrs.some((a) => a.some((p) => Array.isArray(p) && p[1] != null));

export function ChartsPage({ param }: { param?: string }) {
  const source = param || "self";
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>("24h");
  const [sources, setSources] = useState<Source[]>([]);
  const [m, setM] = useState<Series>({});
  const [msgs, setMsgs] = useState<{ t: number; kind: string; direction: string; n: number }[]>([]);
  const [signal, setSignal] = useState<{ t: number; snr: number | null; rssi: number | null }[]>([]);
  const [act, setAct] = useState<{ series: { t: number; contacts: number; events: number }[]; top: { name: string; adverts: number; messages: number }[] }>({ series: [], top: [] });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api.get<Source[]>("/api/charts/sources").then(setSources).catch(() => {});
    const off = live.on((ev) => {
      if (ev.type === "metrics" || ev.type === "repeater") setTick((x) => x + 1);
    });
    return () => {
      off();
    };
  }, []);

  useEffect(() => {
    const names = "battery_mv,voltage,uptime,tx_air_secs,rx_air_secs,recv,sent,noise_floor,last_snr,rx_log_packets";
    api.get<{ series: Series }>(`/api/charts/metrics?source=${source}&names=${names}&period=${period}`).then((r) => setM(r.series)).catch(() => {});
    api.get<{ rows: typeof msgs }>(`/api/charts/messages?period=${period}`).then((r) => setMsgs(r.rows)).catch(() => {});
    api.get<{ rows: typeof signal }>(`/api/charts/signal?period=${period}`).then((r) => setSignal(r.rows)).catch(() => {});
    api.get<typeof act>(`/api/charts/activity?period=${period}`).then(setAct).catch(() => {});
  }, [source, period, tick]);

  const battery = ms(m.battery_mv, (v) => v / 1000);
  const tmVolt = ms(m.voltage);
  const uptime = ms(m.uptime, (v) => v / 3600);
  const txAir = ms(m.tx_air_secs);
  const rxAir = ms(m.rx_air_secs);
  const recv = ms(m.recv);
  const sent = ms(m.sent);
  const noise = ms(m.noise_floor);
  const lsnr = ms(m.last_snr);
  const rxlog = ms(m.rx_log_packets);

  const opts = useMemo(() => {
    const b = baseOption;
    const msgSeries = (kind: string, dir: string) => msgs.filter((r) => r.kind === kind && r.direction === dir).map((r) => [r.t * 1000, r.n]);
    const sig = signal.map((r) => [r.t * 1000, r.snr == null ? null : +r.snr.toFixed(2)]);
    const sigR = signal.map((r) => [r.t * 1000, r.rssi == null ? null : +r.rssi.toFixed(1)]);
    return {
      battery: { ...b(), series: [line(t("charts.battery"), battery, C.accent, { areaStyle: { color: "rgba(46,230,166,.08)" } }), ...(tmVolt.length ? [line("LPP", tmVolt, C.second)] : [])] },
      signal: {
        ...b(),
        yAxis: [{ ...(b().yAxis as object), name: "" }, { ...(b().yAxis as object), splitLine: { show: false } }],
        grid: { left: 40, right: 44, top: 30, bottom: 26 },
        series: [line(t("charts.snr"), sig, C.accent), line(t("charts.rssi"), sigR, C.second, { yAxisIndex: 1 })],
      },
      radio: {
        ...b(),
        yAxis: [{ ...(b().yAxis as object) }, { ...(b().yAxis as object), splitLine: { show: false } }],
        grid: { left: 40, right: 44, top: 30, bottom: 26 },
        series: [line(t("charts.noise"), noise, C.accent), line("SNR", lsnr, "rgba(154,163,173,.55)", { yAxisIndex: 1, lineStyle: { width: 1, color: "rgba(154,163,173,.55)" } })],
      },
      uptime: { ...b(), series: [line(t("charts.uptime"), uptime, C.accent, { step: "end" })] },
      airtime: { ...b(), series: [bar(t("charts.tx"), txAir, C.accent), bar(t("charts.rx"), rxAir, C.second)] },
      packets: { ...b(), series: [bar(t("charts.recv"), recv, C.second), bar(t("charts.sent"), sent, C.accent), ...(rxlog.length ? [line("RX log", rxlog, C.fourth)] : [])] },
      messages: {
        ...b(),
        series: [
          bar(t("charts.chan_in"), msgSeries("chan", "in"), C.second, "m"),
          bar(t("charts.dm_in"), msgSeries("dm", "in"), C.third, "m"),
          bar(t("charts.chan_out"), msgSeries("chan", "out"), C.accent, "m"),
          bar(t("charts.dm_out"), msgSeries("dm", "out"), C.fourth, "m"),
        ],
      },
      activity: {
        ...b(),
        series: [line(t("charts.contacts_active"), act.series.map((r) => [r.t * 1000, r.contacts]), C.accent, { step: "middle" }), bar(t("charts.events"), act.series.map((r) => [r.t * 1000, r.events]), "rgba(154,163,173,.35)")],
      },
      top: {
        ...b(),
        grid: { left: 110, right: 14, top: 26, bottom: 20 },
        tooltip: { ...(b().tooltip as object), trigger: "axis", axisPointer: { type: "shadow" } },
        xAxis: { type: "value", axisLabel: { color: C.axis, fontSize: 10 }, splitLine: { lineStyle: { color: C.grid } } },
        yAxis: { type: "category", inverse: true, data: act.top.map((r) => r.name), axisLabel: { color: C.text, fontSize: 11, width: 100, overflow: "truncate" }, axisLine: { show: false }, axisTick: { show: false } },
        series: [
          { name: t("charts.msgs"), type: "bar", stack: "a", data: act.top.map((r) => r.messages), itemStyle: { color: C.accent }, barMaxWidth: 14 },
          { name: t("charts.adverts"), type: "bar", stack: "a", data: act.top.map((r) => r.adverts), itemStyle: { color: C.second }, barMaxWidth: 14 },
        ],
      },
    };
  }, [m, msgs, signal, act]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page">
      <div className="page-title">{t("charts.title")}</div>
      <div className="toolbar">
        <div className="seg">
          {PERIODS.map((p) => (
            <button key={p} className={p === period ? "on" : ""} onClick={() => setPeriod(p)}>{t(`period.${p}`)}</button>
          ))}
        </div>
        <select className="input" style={{ width: "auto", minWidth: 160 }} value={source} onChange={(e) => navigate("charts", e.target.value)}>
          {sources.map((s) => (
            <option key={s.key} value={s.key}>{s.key === "self" ? t("charts.self") : s.name || s.key.slice(0, 8)}</option>
          ))}
          {!sources.some((s) => s.key === source) && <option value={source}>{source.slice(0, 8)}</option>}
        </select>
      </div>
      <div className="grid">
        <Card title={t("charts.battery")} empty={!hasData(battery, tmVolt)}><EChart option={opts.battery} /></Card>
        <Card title={t("charts.radio")} empty={!hasData(noise, lsnr)}><EChart option={opts.radio} /></Card>
        <Card title={t("charts.uptime")} empty={!hasData(uptime)}><EChart option={opts.uptime} /></Card>
        <Card title={t("charts.airtime")} empty={!hasData(txAir, rxAir)}><EChart option={opts.airtime} /></Card>
        <Card title={t("charts.packets")} empty={!hasData(recv, sent)}><EChart option={opts.packets} /></Card>
        <Card title={t("charts.signal")} empty={signal.length === 0}><EChart option={opts.signal} /></Card>
        <Card title={t("charts.messages")} empty={msgs.length === 0}><EChart option={opts.messages} /></Card>
        <Card title={t("charts.activity")} empty={act.series.length === 0}><EChart option={opts.activity} /></Card>
        <Card title={t("charts.activity_top")} empty={act.top.length === 0}><EChart option={opts.top} className="chart tall" /></Card>
      </div>
    </div>
  );
}
