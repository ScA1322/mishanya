import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
import { errorText, get, post, setCsrf, setUnauthorizedHandler } from "./api";
import { useApi } from "./hooks";
import type { DiscordResources, GuildSummary, Meta } from "./types";
import { ConfirmProvider, ErrorBox, GuildIcon, Loading, ResourcesCtx, ToastProvider, useToast } from "./ui";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Servers from "./pages/Servers";
import GuildOverview from "./pages/GuildOverview";
import Leveling from "./pages/Leveling";
import Leaderboard from "./pages/Leaderboard";
import Rewards from "./pages/Rewards";
import Moderation from "./pages/Moderation";
import Automod from "./pages/Automod";
import Welcome from "./pages/Welcome";
import Autoroles from "./pages/Autoroles";
import Commands from "./pages/Commands";
import Logs from "./pages/Logs";
import Settings from "./pages/Settings";
import Diagnostics from "./pages/Diagnostics";
import Help from "./pages/Help";

const MetaCtx = createContext<Meta | null>(null);
export const useMeta = () => useContext(MetaCtx)!;

const GuildsCtx = createContext<{ guilds: GuildSummary[]; reload: () => void }>({ guilds: [], reload: () => {} });
export const useGuilds = () => useContext(GuildsCtx);

const GLOBAL_NAV: [string, string, string][] = [
  ["/", "🏠", "Главная"],
  ["/servers", "🗂️", "Серверы"],
  ["/settings", "⚙️", "Общие настройки"],
  ["/diagnostics", "🩺", "Диагностика"],
  ["/help", "❔", "Справка"],
];

const GUILD_NAV: [string, string, string][] = [
  ["", "📊", "Обзор"],
  ["leveling", "📈", "Уровни и XP"],
  ["leaderboard", "🏆", "Таблица лидеров"],
  ["rewards", "🎁", "Роли за уровни"],
  ["moderation", "🛡️", "Модерация"],
  ["automod", "🤖", "Автомодерация"],
  ["welcome", "👋", "Приветствия"],
  ["autoroles", "🎭", "Автороли"],
  ["commands", "✨", "Команды"],
  ["logs", "📜", "Журналы"],
];

function Sidebar({ open, onNavigate, onLogout }: { open: boolean; onNavigate: () => void; onLogout: () => void }) {
  const { guilds } = useGuilds();
  const loc = useLocation();
  const match = loc.pathname.match(/^\/servers\/(\d+)/);
  const guild = match ? guilds.find((g) => g.id === match[1]) : undefined;
  return (
    <nav className={`sidebar ${open ? "open" : ""}`} aria-label="Навигация">
      <div className="brand"><div className="brand-logo">M</div>Mishanya</div>
      {GLOBAL_NAV.map(([to, icon, label]) => (
        <NavLink key={to} to={to} end className="nav-link" onClick={onNavigate}>
          <span className="nav-icon">{icon}</span>{label}
        </NavLink>
      ))}
      {match && (
        <>
          <div className="nav-section">Сервер</div>
          <div className="guild-switch">
            <GuildIcon name={guild?.name ?? "?"} url={guild?.icon_url ?? null} small />
            <span className="name">{guild?.name ?? match[1]}</span>
          </div>
          {GUILD_NAV.map(([to, icon, label]) => (
            <NavLink key={to} to={`/servers/${match[1]}${to ? "/" + to : ""}`} end className="nav-link" onClick={onNavigate}>
              <span className="nav-icon">{icon}</span>{label}
            </NavLink>
          ))}
        </>
      )}
      <div className="sidebar-footer">
        <button className="btn btn-ghost" style={{ width: "100%", justifyContent: "flex-start" }} onClick={onLogout}>
          <span className="nav-icon">↩</span>Выйти
        </button>
      </div>
    </nav>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const { data: guilds, reload } = useApi<GuildSummary[]>("/api/guilds");
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  return (
    <GuildsCtx.Provider value={{ guilds: guilds ?? [], reload }}>
      <div className="app">
        <Sidebar open={open} onNavigate={() => setOpen(false)} onLogout={onLogout} />
        {open && <div className="backdrop" onClick={() => setOpen(false)} />}
        <div className="main">
          <div className="topbar">
            <button className="icon-btn" onClick={() => setOpen(true)} aria-label="Меню">☰</button>
            <strong>Mishanya</strong>
          </div>
          <main className="content">
            <Outlet />
          </main>
        </div>
      </div>
    </GuildsCtx.Provider>
  );
}

/** Загружает каналы и роли выбранного сервера для всех страниц его настроек. */
function GuildScope() {
  const { guildId } = useParams();
  const { data, error, loading, reload } = useApi<DiscordResources>(`/api/guilds/${guildId}/discord`, [guildId]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <ResourcesCtx.Provider value={data!}>
      {!data!.connected && (
        <div className="alert alert-warn" style={{ marginBottom: 16 }}>
          Бот сейчас не подключён к этому серверу: списки каналов и ролей недоступны, но настройки можно менять — они применятся после подключения.
        </div>
      )}
      <Outlet />
    </ResourcesCtx.Provider>
  );
}

function AuthedApp({ onLogout }: { onLogout: () => void }) {
  const { data: meta, error, reload } = useApi<Meta>("/api/meta");
  if (error) return <div className="content"><ErrorBox error={error} onRetry={reload} /></div>;
  if (!meta) return <div className="content"><Loading /></div>;
  return (
    <MetaCtx.Provider value={meta}>
      <Routes>
        <Route element={<Shell onLogout={onLogout} />}>
          <Route index element={<Home />} />
          <Route path="servers" element={<Servers />} />
          <Route path="servers/:guildId" element={<GuildScope />}>
            <Route index element={<GuildOverview />} />
            <Route path="leveling" element={<Leveling />} />
            <Route path="leaderboard" element={<Leaderboard />} />
            <Route path="rewards" element={<Rewards />} />
            <Route path="moderation" element={<Moderation />} />
            <Route path="automod" element={<Automod />} />
            <Route path="welcome" element={<Welcome />} />
            <Route path="autoroles" element={<Autoroles />} />
            <Route path="commands" element={<Commands />} />
            <Route path="logs" element={<Logs />} />
          </Route>
          <Route path="settings" element={<Settings />} />
          <Route path="diagnostics" element={<Diagnostics />} />
          <Route path="help" element={<Help />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </MetaCtx.Provider>
  );
}

function Root() {
  const [state, setState] = useState<"checking" | "in" | "out">("checking");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const check = useCallback(async () => {
    try {
      const me = await get<{ authenticated: boolean; csrf_token: string | null }>("/api/auth/me");
      setCsrf(me.csrf_token);
      setState(me.authenticated ? "in" : "out");
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setCsrf(null);
      setState("out");
    });
    check();
  }, [check]);

  const logout = async () => {
    try {
      await post("/api/auth/logout");
    } catch (e) {
      toast("err", errorText(e));
    }
    setCsrf(null);
    setState("out");
  };

  if (error) return <div className="content"><ErrorBox error={error} onRetry={check} /></div>;
  if (state === "checking") return <div className="content"><Loading /></div>;
  if (state === "out") return <Login onLogin={(csrf) => { setCsrf(csrf); setState("in"); }} />;
  return <AuthedApp onLogout={logout} />;
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <ConfirmProvider>
          <Root />
        </ConfirmProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
