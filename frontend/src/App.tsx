import { useCallback, useEffect, useState, type ReactNode } from "react";
import { t } from "./i18n";
import { api, onUnauthorized } from "./lib/api";
import { live } from "./lib/ws";
import { setState, useStore } from "./lib/store";
import { href, useRoute } from "./lib/router";
import type { Config, NodeStatus, Unread } from "./lib/types";
import { Login } from "./pages/Login";
import { NodePage } from "./pages/NodePage";
import { Placeholder } from "./pages/Placeholder";
import { StatusBadge } from "./components/StatusBadge";
import { ToastHost } from "./components/Toast";
import { IconChannels, IconCharts, IconDm, IconMap, IconNode, IconRepeater, Logo } from "./components/Icons";

function useAuth() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => {
    api.get<{ authenticated: boolean }>("/api/session").then((r) => setAuthed(r.authenticated)).catch(() => setAuthed(false));
    return onUnauthorized(() => setAuthed(false));
  }, []);
  return [authed, setAuthed] as const;
}

export function refreshUnread() {
  api.get<Unread>("/api/unread").then((unread) => setState({ unread })).catch(() => {});
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const route = useRoute();
  const section = route[0] || "channels";
  const liveOpen = useStore((s) => s.liveOpen);
  const unread = useStore((s) => s.unread);

  useEffect(() => {
    api.get<NodeStatus>("/api/status").then((status) => setState({ status })).catch(() => {});
    api.get<Config>("/api/config").then((config) => setState({ config })).catch(() => {});
    refreshUnread();
    live.connect();
    const offState = live.onState((open) => {
      setState({ liveOpen: open });
      if (open) refreshUnread();
    });
    const off = live.on((ev) => {
      switch (ev.type) {
        case "status":
          setState({ status: ev.data });
          break;
        case "message":
        case "read":
          refreshUnread();
          break;
        case "contacts":
        case "advert":
          setState((s) => ({ contactsRev: s.contactsRev + 1 }));
          break;
        case "channels":
          setState((s) => ({ channelsRev: s.channelsRev + 1 }));
          break;
        case "repeater":
          setState((s) => ({ repeatersRev: s.repeatersRev + 1 }));
          break;
      }
    });
    return () => {
      off();
      offState();
      live.close();
    };
  }, []);

  const nav: [string, string, ReactNode, number][] = [
    ["channels", t("nav.channels"), <IconChannels />, unread.total_channels],
    ["dm", t("nav.dms"), <IconDm />, unread.total_dms],
    ["repeaters", t("nav.repeaters"), <IconRepeater />, 0],
    ["charts", t("nav.charts"), <IconCharts />, 0],
    ["map", t("nav.map"), <IconMap />, 0],
    ["node", t("nav.node"), <IconNode />, 0],
  ];

  let page: ReactNode;
  switch (section) {
    case "node":
      page = <NodePage onLogout={onLogout} />;
      break;
    default:
      page = <Placeholder title={nav.find((n) => n[0] === section)?.[1] ?? ""} />;
  }

  return (
    <div className="shell">
      <header className="header">
        <div className="brand"><Logo />{t("app.title")}</div>
        <div className="grow" />
        <StatusBadge />
      </header>
      {!liveOpen && <div className="live-banner">{t("status.live_lost")}</div>}
      <div className="body">
        <main className="main">{page}</main>
        <nav className="nav">
          {nav.map(([key, label, icon, badge]) => (
            <a key={key} href={href(key)} className={section === key ? "on" : ""}>
              {icon}
              <span>{label}</span>
              {badge > 0 && <span className="badge">{badge > 99 ? "99+" : badge}</span>}
            </a>
          ))}
        </nav>
      </div>
      <ToastHost />
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useAuth();
  const logout = useCallback(async () => {
    await api.post("/api/logout").catch(() => {});
    setAuthed(false);
  }, [setAuthed]);

  if (authed === null) return <div className="center-empty">{t("common.loading")}</div>;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return <Shell onLogout={logout} />;
}
