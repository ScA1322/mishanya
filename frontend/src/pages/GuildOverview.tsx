import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorText, get, put } from "../api";
import { useMeta } from "../App";
import { useApi } from "../hooks";
import { Icon, type IconName } from "../icons";
import type { Issue, ModuleName } from "../types";
import { Card, Empty, ErrorBox, GuildIcon, Loading, PageHeader, Toggle, fmtDate, fmtNum, useToast } from "../ui";
import { MODULE_LABELS } from "./Servers";
import { useGuilds } from "../App";

interface Overview {
  id: string; name: string; icon_url: string | null; member_count: number | null; present: boolean; connected: boolean;
  owner_id: string | null; created_at: string | null; channels: number | null; roles: number | null;
  modules: Record<ModuleName, boolean>;
  stats: { leveling: { members: number; total_xp: number; messages: number; max_level: number }; active_warnings: number;
    cases: number; custom_commands: number; events_7d: Record<string, number> };
  recent_events: { id: number; type: string; summary: string; created_at: string }[];
  issues: Issue[];
}

const MODULES: [ModuleName, string, IconName, string][] = [
  ["leveling", "leveling", "trending", "XP за сообщения, уровни, награды"],
  ["moderation", "moderation", "shield", "Команды модерации и предупреждения"],
  ["automod", "automod", "bot", "Автоматическая проверка сообщений"],
  ["welcome", "welcome", "userPlus", "Сообщения при входе и выходе"],
  ["autoroles", "autoroles", "tag", "Роли при вступлении"],
  ["custom_commands", "commands", "terminal", "Команды-шаблоны сервера"],
  ["logging", "logs", "file", "Журнал событий сервера"],
  ["tickets", "tickets", "ticket", "Обращения в приватных каналах"],
  ["music", "music", "music", "Музыка из SoundCloud"],
];

/** Модули с единым переключателем enabled; приветствия включаются по отдельности на своей странице. */
const TOGGLEABLE = new Set<ModuleName>(["leveling", "moderation", "automod", "autoroles", "custom_commands", "logging", "tickets", "music"]);

export default function GuildOverview() {
  const { guildId } = useParams();
  const meta = useMeta();
  const toast = useToast();
  const guilds = useGuilds();
  const { data, error, loading, reload, setData } = useApi<Overview>(`/api/guilds/${guildId}`, [guildId]);
  const [busy, setBusy] = useState<ModuleName | null>(null);

  const toggle = async (m: ModuleName, enabled: boolean) => {
    setBusy(m);
    try {
      const cfg = await get<Record<string, unknown>>(`/api/guilds/${guildId}/config/${m}`);
      await put(`/api/guilds/${guildId}/config/${m}`, { ...cfg, enabled });
      setData((d) => (d ? { ...d, modules: { ...d.modules, [m]: enabled } } : d));
      guilds.reload();
      toast("ok", `${MODULE_LABELS[m]}: ${enabled ? "включено" : "выключено"}`);
    } catch (e) {
      toast("err", `${MODULE_LABELS[m]}: ${errorText(e)}`);
    } finally {
      setBusy(null);
    }
  };
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (loading && !data) return <Loading />;
  const g = data!;
  const eventsTotal = Object.values(g.stats.events_7d).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="guild-hero">
        <GuildIcon name={g.name} url={g.icon_url} />
        <div className="spacer"><PageHeader title={g.name} subtitle={<span className="mono">{g.id}</span>}
        actions={<button className="btn" onClick={reload}><Icon name="refresh" size={15} />Обновить</button>} /></div>
      </div>

      {!g.present && <div className="alert alert-warn" style={{ marginBottom: 16 }}>Бот покинул этот сервер. Данные сохранены и доступны только для просмотра и экспорта.</div>}
      {g.issues.length > 0 && (
        <Card title="Проблемы конфигурации" subtitle="Исправьте их, чтобы модули работали корректно" className="mt">
          {g.issues.map((i, n) => (
            <div key={n} className={`alert ${i.level === "error" ? "alert-err" : "alert-warn"}`}>
              <strong>{MODULE_LABELS[i.module as ModuleName] ?? "Общее"}:</strong> {i.message}
            </div>
          ))}
        </Card>
      )}

      <div className="grid grid-4 mt">
        <Card><div className="stat"><span className="stat-label"><Icon name="users" size={15} />Участников</span><span className="stat-value">{fmtNum(g.member_count)}</span>
          <span className="small muted">{g.connected ? `${g.channels} каналов · ${g.roles} ролей` : "нет связи с сервером"}</span></div></Card>
        <Card><div className="stat"><span className="stat-label"><Icon name="trending" size={15} />Участников с XP</span><span className="stat-value">{fmtNum(g.stats.leveling.members)}</span>
          <span className="small muted">макс. уровень {g.stats.leveling.max_level} · {fmtNum(g.stats.leveling.messages)} сообщ.</span></div></Card>
        <Card><div className="stat"><span className="stat-label"><Icon name="shield" size={15} />Активных предупреждений</span><span className="stat-value">{fmtNum(g.stats.active_warnings)}</span>
          <span className="small muted">случаев модерации: {fmtNum(g.stats.cases)}</span></div></Card>
        <Card><div className="stat"><span className="stat-label"><Icon name="file" size={15} />События за 7 дней</span><span className="stat-value">{fmtNum(eventsTotal)}</span>
          <span className="small muted">команд сервера: {g.stats.custom_commands}</span></div></Card>
      </div>

      <div className="grid grid-2 mt">
        <Card title="Модули" subtitle="Включайте и выключайте модули прямо здесь">
          {MODULES.map(([m, path, icon, hint]) => (
            <div key={m} className="list-item module-row">
              <span className={`module-icon ${g.modules[m] ? "on" : ""}`}><Icon name={icon} size={17} /></span>
              <Link className="spacer module-name" to={`/servers/${g.id}/${path}`}>
                <span>{MODULE_LABELS[m]}</span>
                <span className="small muted">{hint}</span>
              </Link>
              {TOGGLEABLE.has(m)
                ? <Toggle checked={g.modules[m]} disabled={busy === m} onChange={(v) => toggle(m, v)} label={MODULE_LABELS[m]} />
                : <span className={`badge ${g.modules[m] ? "badge-ok" : ""}`}>{g.modules[m] ? "включено" : "выключено"}</span>}
            </div>
          ))}
        </Card>
        <Card title="Последние события" actions={<Link className="btn btn-sm" to={`/servers/${g.id}/logs`}>Все журналы</Link>}>
          {g.recent_events.length === 0 ? <Empty>Событий нет</Empty> : g.recent_events.map((e) => (
            <div key={e.id} className="list-item">
              <span className="badge">{meta.log_events[e.type] ?? e.type}</span>
              <div style={{ minWidth: 0 }}>
                <div className="small" style={{ wordBreak: "break-word" }}>{e.summary}</div>
                <div className="small muted">{fmtDate(e.created_at)}</div>
              </div>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
