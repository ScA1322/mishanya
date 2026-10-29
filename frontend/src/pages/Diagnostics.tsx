import { Link } from "react-router-dom";
import { errorText, post } from "../api";
import { useApi } from "../hooks";
import type { Issue } from "../types";
import { Card, Empty, ErrorBox, Loading, PageHeader, fmtBytes, fmtDate, useToast } from "../ui";

interface Diag {
  versions: Record<string, string>;
  bot: { token_configured: boolean; error: string | null; connected: boolean; latency_ms: number | null; user: string | null; guild_count: number; commands: string | null; sync_error: string | null };
  intents: { members: boolean; message_content: boolean; guilds: boolean };
  db: { path: string; size_bytes: number; schema_version: number; integrity: string; event_logs: number; pending_reminders: number; backups: number; last_backup_at: string | null };
  config: { owner_id_set: boolean; panel_host: string; data_dir: string; backup_dir: string; frontend_built: boolean };
  guilds: { id: string; name: string; connected: boolean; bot_top_role: string | null; issues: Issue[] }[];
  errors: { time: string; level: string; source: string; message: string }[];
}

const ok = (v: boolean, yes = "да", no = "нет") => <span className={`badge ${v ? "badge-ok" : "badge-err"}`}>{v ? yes : no}</span>;

export default function Diagnostics() {
  const toast = useToast();
  const { data, error, loading, reload } = useApi<Diag>("/api/diagnostics");
  const clearErrors = async () => {
    try { await post("/api/errors/clear"); toast("ok", "Список ошибок очищен"); reload(); } catch (e) { toast("err", errorText(e)); }
  };
  if (error && !data) return <><PageHeader title="Диагностика" /><ErrorBox error={error} onRetry={reload} /></>;
  if (loading && !data) return <><PageHeader title="Диагностика" /><Loading text="Проверяю систему…" /></>;
  const d = data!;
  return (
    <>
      <PageHeader title="Диагностика" subtitle="Проверка подключения, прав бота, базы данных и конфигурации" actions={<button className="btn" onClick={reload}>Проверить снова</button>} />
      <div className="grid grid-2">
        <Card title="Discord">
          <div className="list-item"><span className="spacer">Токен задан</span>{ok(d.bot.token_configured)}</div>
          <div className="list-item"><span className="spacer">Подключение</span>{ok(d.bot.connected, "в сети", "нет")}</div>
          <div className="list-item"><span className="spacer">Аккаунт бота</span><span>{d.bot.user ?? "—"}</span></div>
          <div className="list-item"><span className="spacer">Задержка</span><span>{d.bot.latency_ms != null ? `${d.bot.latency_ms} мс` : "—"}</span></div>
          <div className="list-item"><span className="spacer">Intent Server Members</span>{ok(d.intents.members, "запрошен", "нет")}</div>
          <div className="list-item"><span className="spacer">Intent Message Content</span>{ok(d.intents.message_content, "запрошен", "нет")}</div>
          <div className="list-item"><span className="spacer">Slash-команды</span><span>{d.bot.commands ?? "—"}</span></div>
          {d.bot.sync_error && <div className="alert alert-err mt">Ошибка синхронизации команд: {d.bot.sync_error}</div>}
          {d.bot.error && <div className="alert alert-err mt">{d.bot.error}</div>}
        </Card>
        <Card title="База данных">
          <div className="list-item"><span className="spacer">Целостность</span>{ok(d.db.integrity === "ok", "в порядке", d.db.integrity)}</div>
          <div className="list-item"><span className="spacer">Версия схемы</span><span>v{d.db.schema_version}</span></div>
          <div className="list-item"><span className="spacer">Размер</span><span>{fmtBytes(d.db.size_bytes)}</span></div>
          <div className="list-item"><span className="spacer">Записей журнала</span><span>{d.db.event_logs}</span></div>
          <div className="list-item"><span className="spacer">Активных напоминаний</span><span>{d.db.pending_reminders}</span></div>
          <div className="list-item"><span className="spacer">Резервных копий</span><span>{d.db.backups} · последняя {fmtDate(d.db.last_backup_at)}</span></div>
          <div className="small muted mt mono" style={{ wordBreak: "break-all" }}>{d.db.path}</div>
        </Card>
      </div>

      <Card className="mt" title="Серверы" subtitle="Права бота, доступность каналов и ролей из настроек">
        {d.guilds.length === 0 ? <Empty>Нет активных серверов</Empty> : d.guilds.map((g) => (
          <div key={g.id} className="list-item" style={{ alignItems: "flex-start" }}>
            <div style={{ minWidth: 200 }}>
              <Link to={`/servers/${g.id}`}><strong>{g.name}</strong></Link>
              <div className="small muted">{g.connected ? `Высшая роль бота: ${g.bot_top_role}` : "нет связи"}</div>
            </div>
            <div className="spacer stack" style={{ gap: 6 }}>
              {g.issues.length === 0 ? <span className="badge badge-ok">проблем не найдено</span> : g.issues.map((i, n) => (
                <div key={n} className={`alert ${i.level === "error" ? "alert-err" : "alert-warn"} small`}>{i.message}</div>
              ))}
            </div>
          </div>
        ))}
      </Card>

      <div className="grid grid-2 mt">
        <Card title="Окружение">
          {Object.entries(d.versions).map(([k, v]) => <div key={k} className="list-item"><span className="spacer">{k}</span><span className="mono">{v}</span></div>)}
          <div className="list-item"><span className="spacer">Адрес панели</span><span className="mono">{d.config.panel_host}</span></div>
          <div className="list-item"><span className="spacer">OWNER_ID указан</span>{ok(d.config.owner_id_set)}</div>
          <div className="list-item"><span className="spacer">Frontend собран</span>{ok(d.config.frontend_built)}</div>
        </Card>
        <Card title="Журнал ошибок приложения" subtitle="С момента запуска. Полный журнал — data/mishanya.log"
          actions={d.errors.length > 0 && <button className="btn btn-sm" onClick={clearErrors}>Очистить</button>}>
          {d.errors.length === 0 ? <Empty icon="✅">Ошибок нет</Empty> : (
            <div style={{ maxHeight: 420, overflowY: "auto" }}>
              {d.errors.map((e, i) => (
                <div key={i} className="list-item" style={{ alignItems: "flex-start" }}>
                  <span className={`badge ${e.level === "WARNING" ? "badge-warn" : "badge-err"}`}>{e.level}</span>
                  <div style={{ minWidth: 0 }}><div className="small" style={{ wordBreak: "break-word" }}>{e.message}</div>
                    <div className="small muted">{fmtDate(e.time)} · {e.source}</div></div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
