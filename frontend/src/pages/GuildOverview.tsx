import { Link, useParams } from "react-router-dom";
import { useMeta } from "../App";
import { useApi } from "../hooks";
import type { Issue, ModuleName } from "../types";
import { Card, Empty, ErrorBox, GuildIcon, Loading, PageHeader, fmtDate, fmtNum } from "../ui";
import { MODULE_LABELS } from "./Servers";

interface Overview {
  id: string; name: string; icon_url: string | null; member_count: number | null; present: boolean; connected: boolean;
  owner_id: string | null; created_at: string | null; channels: number | null; roles: number | null;
  modules: Record<ModuleName, boolean>;
  stats: { leveling: { members: number; total_xp: number; messages: number; max_level: number }; active_warnings: number;
    cases: number; custom_commands: number; events_7d: Record<string, number> };
  recent_events: { id: number; type: string; summary: string; created_at: string }[];
  issues: Issue[];
}

const MODULE_LINKS: Partial<Record<ModuleName, string>> = {
  leveling: "leveling", moderation: "moderation", automod: "automod", welcome: "welcome", autoroles: "autoroles",
  custom_commands: "commands", logging: "logs",
};

export default function GuildOverview() {
  const { guildId } = useParams();
  const meta = useMeta();
  const { data, error, loading, reload } = useApi<Overview>(`/api/guilds/${guildId}`, [guildId]);
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (loading && !data) return <Loading />;
  const g = data!;
  const eventsTotal = Object.values(g.stats.events_7d).reduce((a, b) => a + b, 0);
  return (
    <>
      <PageHeader title={g.name} subtitle={<span className="mono">{g.id}</span>}
        actions={<><GuildIcon name={g.name} url={g.icon_url} /><button className="btn" onClick={reload}>Обновить</button></>} />

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
        <Card><div className="stat"><span className="stat-label">Участников</span><span className="stat-value">{fmtNum(g.member_count)}</span>
          <span className="small muted">{g.connected ? `${g.channels} каналов · ${g.roles} ролей` : "нет связи с сервером"}</span></div></Card>
        <Card><div className="stat"><span className="stat-label">Участников с XP</span><span className="stat-value">{fmtNum(g.stats.leveling.members)}</span>
          <span className="small muted">макс. уровень {g.stats.leveling.max_level} · {fmtNum(g.stats.leveling.messages)} сообщ.</span></div></Card>
        <Card><div className="stat"><span className="stat-label">Активных предупреждений</span><span className="stat-value">{fmtNum(g.stats.active_warnings)}</span>
          <span className="small muted">случаев модерации: {fmtNum(g.stats.cases)}</span></div></Card>
        <Card><div className="stat"><span className="stat-label">События за 7 дней</span><span className="stat-value">{fmtNum(eventsTotal)}</span>
          <span className="small muted">команд сервера: {g.stats.custom_commands}</span></div></Card>
      </div>

      <div className="grid grid-2 mt">
        <Card title="Модули">
          {(Object.keys(MODULE_LINKS) as ModuleName[]).map((m) => (
            <div key={m} className="list-item">
              <span className={`dot ${g.modules[m] ? "dot-ok" : ""}`} />
              <span className="spacer">{MODULE_LABELS[m]}</span>
              <span className={`badge ${g.modules[m] ? "badge-ok" : ""}`}>{g.modules[m] ? "включён" : "выключен"}</span>
              <Link className="btn btn-sm" to={`/servers/${g.id}/${MODULE_LINKS[m]}`}>Настроить</Link>
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
