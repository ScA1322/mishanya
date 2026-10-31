import { Link } from "react-router-dom";
import { useEffect } from "react";
import { useApi } from "../hooks";
import { Icon, type IconName } from "../icons";
import { Card, Empty, ErrorBox, Loading, PageHeader, fmtBytes, fmtDate } from "../ui";

interface Status {
  bot: {
    token_configured: boolean; error: string | null; connected: boolean; latency_ms: number | null; user: string | null;
    avatar_url: string | null; guild_count: number; commands: string | null; sync_error: string | null; invite_url: string | null;
  };
  panel: { url: string; host: string };
  uptime_seconds: number;
  guilds: { total: number; present: number };
  db: { ok: boolean; error?: string; size_bytes: number; schema_version: number; event_logs: number; pending_reminders: number;
    backups: number; last_backup: string | null; last_backup_at: string | null };
  errors: { time: string; level: string; source: string; message: string }[];
  recent_events: { id: number; guild_id: string; guild_name: string; type: string; summary: string; created_at: string }[];
  settings: { log_retention_days: number; auto_backup_enabled: boolean; auto_backup_interval_hours: number; store_message_content: boolean };
}

const QUICK: [string, IconName, string][] = [
  ["/servers", "servers", "Серверы и их модули"],
  ["/settings", "database", "Резервные копии и общие настройки"],
  ["/diagnostics", "activity", "Проверка прав бота и конфигурации"],
  ["/help", "help", "Справка по настройке"],
];

/** Чек-лист первоначальной настройки: показывается, пока бот не подключён или не добавлен ни на один сервер. */
function Onboarding({ s }: { s: Status }) {
  const b = s.bot;
  const steps: { done: boolean; title: string; text: React.ReactNode }[] = [
    { done: b.token_configured, title: "Токен бота в .env",
      text: <>Developer Portal → Bot → Reset Token, затем строка <code>DISCORD_TOKEN=</code> в файле <code>.env</code> и перезапуск.</> },
    { done: b.connected, title: "Подключение к Discord",
      text: b.error && b.token_configured ? <span className="err-text">{b.error}</span>
        : <>Включите Server Members и Message Content Intent в Developer Portal.</> },
    { done: s.guilds.present > 0, title: "Бот добавлен на сервер",
      text: b.invite_url ? <a href={b.invite_url} target="_blank" rel="noreferrer">Открыть ссылку-приглашение <Icon name="external" size={12} /></a>
        : <>Ссылка-приглашение появится после подключения бота.</> },
    { done: false, title: "Роль бота выше выдаваемых ролей",
      text: <>Настройки сервера → Роли. Проверить права можно в разделе <Link to="/diagnostics">«Диагностика»</Link>.</> },
  ];
  const doneCount = steps.filter((x) => x.done).length;
  return (
    <section className="card onboarding">
      <div className="card-header">
        <div><h2>Первые шаги</h2><p>Выполнено {doneCount} из {steps.length - 1} обязательных · <Link to="/help">подробная инструкция</Link></p></div>
        <div className="progress" style={{ width: 160 }}><div style={{ width: `${(doneCount / (steps.length - 1)) * 100}%` }} /></div>
      </div>
      <ol className="steps">
        {steps.map((st, i) => (
          <li key={i} className={st.done ? "done" : ""}>
            <span className="step-mark">{st.done ? <Icon name="check" size={14} /> : i + 1}</span>
            <div><div className="step-title">{st.title}</div><div className="small muted">{st.text}</div></div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function uptime(s: number) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} д ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`;
}

export default function Home() {
  const { data, error, loading, reload } = useApi<Status>("/api/status");
  useEffect(() => {
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
  }, [reload]);

  if (error && !data) return <><PageHeader title="Главная" /><ErrorBox error={error} onRetry={reload} /></>;
  if (loading && !data) return <><PageHeader title="Главная" /><Loading /></>;
  const s = data!;
  const b = s.bot;
  const botBadge = b.connected ? <span className="badge badge-ok"><span className="dot dot-ok" />В сети</span>
    : !b.token_configured ? <span className="badge badge-warn">Токен не задан</span>
      : b.error ? <span className="badge badge-err">Ошибка</span>
        : <span className="badge badge-warn"><span className="dot dot-warn" />Подключение…</span>;

  return (
    <>
      <PageHeader title="Главная" subtitle="Состояние Mishanya и последние события"
        actions={<button className="btn" onClick={reload}><Icon name="refresh" size={15} />Обновить</button>} />

      {(!b.connected || s.guilds.present === 0) && <Onboarding s={s} />}

      <div className="grid grid-4">
        <Card><div className="stat"><span className="stat-label"><Icon name="bot" size={15} />Discord-бот</span><span>{botBadge}</span>
          <span className="small muted">{b.user ?? "—"}</span></div></Card>
        <Card><div className="stat"><span className="stat-label"><Icon name="zap" size={15} />Соединение с Discord</span>
          <span className="stat-value">{b.latency_ms != null ? `${b.latency_ms} мс` : "—"}</span>
          <span className="small muted">Работает {uptime(s.uptime_seconds)}</span></div></Card>
        <Card><div className="stat"><span className="stat-label"><Icon name="servers" size={15} />Серверы</span>
          <span className="stat-value">{s.guilds.present}</span>
          <span className="small muted">{s.guilds.total - s.guilds.present > 0 ? `ещё ${s.guilds.total - s.guilds.present} с сохранёнными данными` : "все активны"}</span></div></Card>
        <Card><div className="stat"><span className="stat-label"><Icon name="database" size={15} />База данных</span>
          <span>{s.db.ok ? <span className="badge badge-ok">Исправна</span> : <span className="badge badge-err">Ошибка</span>}</span>
          <span className="small muted">{s.db.ok ? `${fmtBytes(s.db.size_bytes)} · схема v${s.db.schema_version}` : s.db.error}</span></div></Card>
      </div>

      <div className="grid grid-2 mt">
        <Card title="Требует внимания" subtitle="Последние предупреждения и ошибки приложения"
          actions={<Link to="/diagnostics" className="btn btn-sm">Диагностика</Link>}>
          {s.errors.length === 0 ? <Empty icon="✅">Проблем не обнаружено</Empty> : (
            <div>
              {s.errors.slice(0, 6).map((e, i) => (
                <div key={i} className="list-item">
                  <span className={`badge ${e.level === "WARNING" ? "badge-warn" : "badge-err"}`}>{e.level === "WARNING" ? "Предупр." : "Ошибка"}</span>
                  <div style={{ minWidth: 0 }}>
                    <div className="small" style={{ wordBreak: "break-word" }}>{e.message}</div>
                    <div className="small muted">{fmtDate(e.time)} · {e.source}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Последние события" subtitle="Со всех серверов">
          {s.recent_events.length === 0 ? <Empty>Событий пока нет. Включите журналы в разделе «Журналы» сервера.</Empty> : (
            <div>
              {s.recent_events.map((e) => (
                <div key={e.id} className="list-item">
                  <div style={{ minWidth: 0 }}>
                    <div className="small" style={{ wordBreak: "break-word" }}>{e.summary}</div>
                    <div className="small muted">{fmtDate(e.created_at)} · <Link to={`/servers/${e.guild_id}/logs`}>{e.guild_name || e.guild_id}</Link></div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="grid grid-2 mt">
        <Card title="Основные настройки" actions={<Link to="/settings" className="btn btn-sm">Изменить</Link>}>
          <div className="list-item"><span className="spacer">Хранение журналов</span><strong>{s.settings.log_retention_days} дн.</strong></div>
          <div className="list-item"><span className="spacer">Автоматические копии</span>
            <strong>{s.settings.auto_backup_enabled ? `каждые ${s.settings.auto_backup_interval_hours} ч` : "выключены"}</strong></div>
          <div className="list-item"><span className="spacer">Последняя копия</span><strong>{fmtDate(s.db.last_backup_at)}</strong></div>
          <div className="list-item"><span className="spacer">Текст сообщений в журнале</span><strong>{s.settings.store_message_content ? "сохраняется" : "не сохраняется"}</strong></div>
          <div className="list-item"><span className="spacer">Slash-команды</span><strong>{b.commands ?? "—"}</strong></div>
          <div className="list-item"><span className="spacer">Адрес панели</span><code>{s.panel.url}</code></div>
        </Card>
        <Card title="Быстрые ссылки">
          <div className="stack" style={{ gap: 8 }}>
            {QUICK.map(([to, icon, label]) => (
              <Link key={to} to={to} className="quick-link"><Icon name={icon} size={17} /><span className="spacer">{label}</span><Icon name="chevronDown" size={14} className="rot-270 muted" /></Link>
            ))}
            {b.invite_url && <a href={b.invite_url} target="_blank" rel="noreferrer" className="btn btn-primary"><Icon name="plus" size={15} />Пригласить Mishanya на сервер</a>}
          </div>
        </Card>
      </div>
    </>
  );
}
