import { ru, type Key } from "./ru";

const dictionaries: Record<string, Record<string, string>> = { ru };
let current: Record<string, string> = ru;

export function setLocale(locale: string) {
  current = dictionaries[locale] ?? ru;
}

/** Перевод по ключу с подстановкой {параметров}. */
export function t(key: Key | string, params?: Record<string, string | number>): string {
  let s = current[key] ?? (ru as Record<string, string>)[key] ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) s = s.split(`{${k}}`).join(String(v));
  }
  return s;
}

export type { Key };
