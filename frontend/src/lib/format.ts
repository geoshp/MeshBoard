import { t } from "../i18n";

export const nowSec = () => Math.floor(Date.now() / 1000);

export function ago(ts: number | null | undefined): string {
  if (!ts) return t("common.never");
  const d = Math.max(0, nowSec() - ts);
  if (d < 10) return t("time.now");
  if (d < 60) return t("time.s", { n: d });
  if (d < 3600) return t("time.m", { n: Math.floor(d / 60) });
  if (d < 86400) return t("time.h", { n: Math.floor(d / 3600) });
  return t("time.d", { n: Math.floor(d / 86400) });
}

export function duration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${t("dur.d", { n: d })} ${t("dur.h", { n: h })}`;
  if (h > 0) return `${t("dur.h", { n: h })} ${t("dur.m", { n: m })}`;
  if (m > 0) return t("dur.m", { n: m });
  return t("dur.s", { n: Math.floor(sec) });
}

export function clock(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const hm = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return hm;
  return `${d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })} ${hm}`;
}

export const fmt = (v: number | null | undefined, digits = 1) =>
  v == null || Number.isNaN(v) ? "—" : v.toFixed(digits).replace(/\.0+$/, "");

export const volts = (mv: number | null | undefined) =>
  mv == null ? "—" : t("units.v", { v: (mv / 1000).toFixed(2) });

export const shortKey = (k: string | null | undefined, n = 8) => (k ? k.slice(0, n) : "—");

/** Длина строки в байтах UTF-8 (лимиты MeshCore считаются в байтах). */
export const utf8Len = (s: string) => new TextEncoder().encode(s).length;

/** Цвет по давности активности: свежий — акцент, далее приглушается. */
export function ageClass(ts: number | null | undefined): "fresh" | "day" | "week" | "old" {
  if (!ts) return "old";
  const d = nowSec() - ts;
  if (d < 3600) return "fresh";
  if (d < 86400) return "day";
  if (d < 7 * 86400) return "week";
  return "old";
}

export function splitHops(path: string | null | undefined, size: number | null | undefined): string[] {
  if (!path) return [];
  const n = Math.max(1, size ?? 1) * 2;
  const out: string[] = [];
  for (let i = 0; i + n <= path.length; i += n) out.push(path.slice(i, i + n));
  return out;
}
