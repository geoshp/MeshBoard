import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { t } from "../i18n";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { navigate } from "../lib/router";
import { ago, ageClass, fmt } from "../lib/format";
import { live } from "../lib/ws";
import { errText } from "../lib/errors";
import type { Contact } from "../lib/types";
import { routeText } from "./Dms";
import { toast } from "../components/Toast";

type MapData = {
  self: { public_key: string | null; name: string | null; lat: number | null; lon: number | null };
  contacts: Contact[];
  routes: { public_key: string; hops: (string | null)[] }[];
  message_paths: { sender: string | null; sender_name: string | null; hops: (string | null)[]; raw: string[]; count: number; last_ts: number }[];
  neighbours: { from: string; to: string; snr: number | null }[];
  traces: { id: number; ts: number; target_key: string; hops: (string | null)[]; snr: (number | null)[] }[];
};

type Resolved = { hash: string; candidates: { public_key: string; name: string }[] };
type Trace = {
  id: number; ts: number; target_key: string | null; status: "pending" | "ok" | "timeout" | "error";
  path_sent: string | null; hops: { hash: string | null; snr: number | null }[] | null;
  resolved: Resolved[]; path_resolved: Resolved[];
};

const AGE_COLORS = { fresh: "#2ee6a6", day: "#7fbfa8", week: "#6a7480", old: "#444a52" } as const;
const LAYERS = ["routes", "messages", "traces", "neighbours"] as const;
type Layer = (typeof LAYERS)[number];

function loadLayers(): Record<Layer, boolean> {
  try {
    const v = JSON.parse(localStorage.getItem("mb.map.layers") ?? "null");
    if (v) return v;
  } catch {
    /* storage unavailable */
  }
  return { routes: true, messages: true, traces: true, neighbours: false };
}

export function MapPage({ param }: { param?: string }) {
  const cfg = useStore((s) => s.config);
  const connected = useStore((s) => s.status?.state === "connected");
  const rev = useStore((s) => s.contactsRev + s.repeatersRev);
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const fitted = useRef(false);
  const [data, setData] = useState<MapData | null>(null);
  const [layers, setLayers] = useState(loadLayers);
  const [selected, setSelected] = useState<string | null>(param ?? null);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [showLayers, setShowLayers] = useState(false);

  const byKey = useMemo(() => new Map((data?.contacts ?? []).map((c) => [c.public_key, c])), [data]);

  // данные
  useEffect(() => {
    api.get<MapData>("/api/map?period=7d").then(setData).catch(() => {});
  }, [rev]);
  useEffect(() => {
    api.get<Trace[]>("/api/traces?period=7d").then(setTraces).catch(() => {});
    const off = live.on((ev) => {
      if (ev.type === "trace") {
        api.get<Trace>(`/api/traces/${ev.data.id}`).then((tr) => {
          setTraces((prev) => [tr, ...prev.filter((x) => x.id !== tr.id)]);
          if (tr.status === "ok") api.get<MapData>("/api/map?period=7d").then(setData).catch(() => {});
        }).catch(() => {});
      }
    });
    return () => {
      off();
    };
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("mb.map.layers", JSON.stringify(layers));
    } catch {
      /* ignore */
    }
  }, [layers]);

  // инициализация карты
  useEffect(() => {
    if (!el.current || !cfg) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, preferCanvas: true });
    L.tileLayer(cfg.tile_url, { attribution: cfg.tile_attribution, maxZoom: 19 }).addTo(m);
    m.setView([55.75, 37.62], 10);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    m.on("click", () => setSelected(null));
    return () => {
      m.remove();
      map.current = null;
    };
  }, [cfg]);

  // отрисовка
  useEffect(() => {
    const m = map.current;
    const g = layer.current;
    if (!m || !g || !data) return;
    g.clearLayers();
    const pos = (key: string | null | undefined): L.LatLngExpression | null => {
      if (!key) return null;
      if (key === data.self.public_key && data.self.lat != null && data.self.lon != null) return [data.self.lat, data.self.lon];
      const c = byKey.get(key);
      return c && c.lat != null && c.lon != null ? [c.lat, c.lon] : null;
    };
    const selfPos = data.self.lat != null && data.self.lon != null ? ([data.self.lat, data.self.lon] as L.LatLngExpression) : null;
    const chain = (pts: (L.LatLngExpression | null)[]) => pts.filter((p): p is L.LatLngExpression => p != null);

    if (layers.neighbours) {
      for (const n of data.neighbours) {
        const a = pos(n.from), b = pos(n.to);
        if (a && b) L.polyline([a, b], { color: "#9aa3ad", weight: 1.5, opacity: 0.6, dashArray: "4 5" })
          .bindTooltip(`SNR ${fmt(n.snr, 2)}`, { sticky: true }).addTo(g);
      }
    }
    if (layers.routes) {
      for (const r of data.routes) {
        const pts = chain([selfPos, ...r.hops.map(pos), pos(r.public_key)]);
        const hl = selected === r.public_key;
        if (pts.length > 1) L.polyline(pts, { color: "#2ee6a6", weight: hl ? 3.5 : 1.5, opacity: hl ? 0.95 : 0.35 }).addTo(g);
      }
    }
    if (layers.messages) {
      for (const p of data.message_paths) {
        const pts = chain([pos(p.sender), ...p.hops.map(pos), selfPos]);
        if (pts.length > 1) L.polyline(pts, { color: "#c9d1d9", weight: Math.min(1 + Math.log2(p.count + 1), 5), opacity: 0.35 })
          .bindTooltip(`${p.sender_name ?? "?"} · ${p.count}× · ${ago(p.last_ts)}`, { sticky: true }).addTo(g);
      }
    }
    if (layers.traces) {
      for (const tr of data.traces.slice(0, 10)) {
        const pts = chain([selfPos, ...tr.hops.map(pos)]);
        if (pts.length > 1) L.polyline(pts, { color: "#2ee6a6", weight: 3, opacity: 0.9, dashArray: "2 6", lineCap: "round" })
          .bindTooltip(`trace · ${ago(tr.ts)} · SNR ${tr.snr.map((s) => fmt(s, 1)).join(" / ")}`, { sticky: true }).addTo(g);
      }
    }

    const bounds: L.LatLngExpression[] = [];
    for (const c of data.contacts) {
      if (c.lat == null || c.lon == null) continue;
      const color = AGE_COLORS[ageClass(c.last_heard || c.last_advert)];
      const rep = c.type === 2 || c.type === 3;
      const mk = L.circleMarker([c.lat, c.lon], {
        radius: rep ? 8 : 6, color: c.own ? "#ffffff" : color, weight: c.own ? 2.5 : rep ? 2 : 1,
        fillColor: color, fillOpacity: rep ? 0.35 : 0.85,
      });
      mk.bindTooltip(c.name || c.public_key.slice(0, 8), { permanent: true, direction: "right", offset: [8, 0], className: "map-label" });
      mk.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        setSelected(c.public_key);
      });
      mk.addTo(g);
      bounds.push([c.lat, c.lon]);
    }
    if (selfPos) {
      L.circleMarker(selfPos, { radius: 9, color: "#ffffff", weight: 3, fillColor: "#2ee6a6", fillOpacity: 1 })
        .bindTooltip(data.self.name ?? t("map.self"), { permanent: true, direction: "right", offset: [10, 0], className: "map-label" })
        .addTo(g);
      bounds.push(selfPos);
    }
    if (!fitted.current && bounds.length) {
      const target = param ? pos(param) : null;
      if (target) m.setView(target, 13);
      else m.fitBounds(L.latLngBounds(bounds), { padding: [30, 30], maxZoom: 14 });
      fitted.current = true;
    }
  }, [data, layers, selected, byKey, param]);

  const sel = selected ? byKey.get(selected) : undefined;
  const selTraces = traces.filter((tr) => tr.target_key === selected).slice(0, 3);
  const noGps = data ? data.contacts.filter((c) => c.lat == null).length : 0;

  async function runTrace(key: string) {
    try {
      const tr = await api.post<Trace>(`/api/contacts/${key}/trace`);
      setTraces((prev) => [tr, ...prev]);
    } catch (e) {
      toast(errText(e));
    }
  }

  return (
    <div className="map-wrap">
      <div ref={el} className="map dark-tiles" />
      <div className="map-panel">
        <button className="btn sm ghost" onClick={() => setShowLayers((v) => !v)}>{t("map.layers")}</button>
        {showLayers && (
          <div className="col" style={{ marginTop: 6 }}>
            {LAYERS.map((l) => (
              <label key={l} className="row small">
                <span className="switch"><input type="checkbox" checked={layers[l]} onChange={(e) => setLayers({ ...layers, [l]: e.target.checked })} /><span /></span>
                {t(`map.layer.${l}`)}
              </label>
            ))}
            <div className="row wrap small" style={{ gap: 10, marginTop: 4 }}>
              {(["fresh", "day", "week", "old"] as const).map((a) => (
                <span key={a} className="row" style={{ gap: 4 }}><span className="legend-dot" style={{ background: AGE_COLORS[a] }} />{t(`map.legend.${a}`)}</span>
              ))}
            </div>
            {noGps > 0 && <div className="small faint">{t("map.no_gps", { n: noGps })}</div>}
          </div>
        )}
      </div>

      {sel && (
        <div className="map-panel bottom" style={{ right: 10, maxHeight: "55%", overflow: "auto" }}>
          <div className="row">
            <div className="grow">
              <div style={{ fontWeight: 650 }} className="ellipsis">{sel.name || sel.public_key.slice(0, 8)}</div>
              <div className="small muted"><span className="mono">{sel.public_key.slice(0, 8)}</span> · {t(`ctype.${sel.type}`)} · {ago(sel.last_heard || sel.last_advert)}</div>
            </div>
            <button className="btn sm ghost" onClick={() => setSelected(null)}>✕</button>
          </div>
          <div className="small muted" style={{ margin: "6px 0" }}>{t("contact.route")}: {routeText(sel, data?.contacts ?? [])}</div>
          <div className="row wrap">
            <button className="btn sm primary" disabled={!connected} onClick={() => runTrace(sel.public_key)}>{t("map.trace")}</button>
            <button className="btn sm" onClick={() => navigate("dm", sel.public_key)}>{t("contact.open_dm")}</button>
          </div>
          {sel.type !== 2 && sel.type !== 3 && <div className="small faint" style={{ marginTop: 6 }}>{t("trace.only_repeaters")}</div>}
          {selTraces.map((tr) => <TraceView key={tr.id} tr={tr} />)}
        </div>
      )}
    </div>
  );
}

function hopLabel(r: Resolved | undefined, hash: string) {
  if (r && r.candidates.length === 1) return r.candidates[0].name || hash;
  if (r && r.candidates.length > 1) return `${hash} (${r.candidates.length}?)`;
  return hash;
}

export function TraceView({ tr }: { tr: Trace }) {
  const hops = tr.hops ?? [];
  let ri = 0;
  return (
    <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
      <div className="row small">
        <span className={`chip ${tr.status === "ok" ? "accent" : tr.status === "pending" ? "" : "warn"}`}>{t(`trace.status.${tr.status}`)}</span>
        <span className="muted">{ago(tr.ts)}</span>
      </div>
      <div className="small faint" style={{ marginTop: 4 }}>
        {t("trace.path_sent", { p: (tr.path_sent ?? "").split(",").map((h, i) => hopLabel(tr.path_resolved[i], h)).join(" → ") })}
      </div>
      {tr.status === "ok" && (
        <table className="table" style={{ marginTop: 4 }}>
          <tbody>
            {hops.map((h, i) => {
              const r = h.hash ? tr.resolved[ri++] : undefined;
              return (
                <tr key={i}>
                  <td>{h.hash ? hopLabel(r, h.hash) : t("trace.back")}</td>
                  <td>SNR {fmt(h.snr, 2)} дБ</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
