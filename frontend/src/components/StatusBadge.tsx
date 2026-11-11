import { t } from "../i18n";
import { useStore } from "../lib/store";
import { navigate } from "../lib/router";

export function StatusBadge() {
  const st = useStore((s) => s.status);
  const state = st?.state ?? "starting";
  let label = t(`status.${state}`);
  if (state === "disconnected" && st?.retry_in != null) label += ` · ${t("status.retry_in", { s: st.retry_in })}`;
  return (
    <button className="status" onClick={() => navigate("node")} title={st?.last_error ?? ""}>
      <span className={`dot ${state}`} />
      <span className="ellipsis">{label}</span>
    </button>
  );
}
