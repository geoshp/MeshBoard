import { useState, type FormEvent } from "react";
import { t } from "../i18n";
import { api, ApiError } from "../lib/api";
import { Logo } from "../components/Icons";

export function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/login", { password });
      onDone();
    } catch (err) {
      const d = err instanceof ApiError ? err.detail : "";
      setError(d === "bad_password" ? t("login.bad_password") : d === "too_many_attempts" ? t("login.too_many_attempts") : t("login.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form onSubmit={submit} className="card">
        <div className="logo"><Logo /></div>
        <div className="page-title" style={{ textAlign: "center" }}>{t("login.title")}</div>
        <input
          className="input" type="password" autoComplete="current-password" autoFocus
          placeholder={t("login.password")} value={password} onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div className="err small">{error}</div>}
        <button className="btn primary" disabled={busy || !password}>{t("login.submit")}</button>
      </form>
    </div>
  );
}
