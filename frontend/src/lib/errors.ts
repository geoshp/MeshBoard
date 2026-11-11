import { t } from "../i18n";
import { ApiError } from "./api";

export function errText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return t("err.network");
    const k = `err.${e.detail}`;
    const s = t(k);
    return s === k ? `${t("common.error")}: ${e.detail}` : s;
  }
  return t("common.error");
}
