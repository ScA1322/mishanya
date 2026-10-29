import { useState } from "react";
import { useParams } from "react-router-dom";
import { del, errorText } from "../api";
import { useMeta } from "../App";
import { useApi, useModuleConfig, useUnsavedWarning } from "../hooks";
import type { Paged } from "../types";
import {
  Card, ChannelName, ChannelSelect, Empty, ErrorBox, Field, Loading, MultiChannelSelect, PageHeader, Pagination, SaveBar, Tabs, Toggle,
  ToggleRow, fmtDate, useConfirm, useToast,
} from "../ui";

interface LogRow { id: number; type: string; summary: string; user_id: string | null; user_name: string | null; channel_id: string | null; details: Record<string, unknown> | null; created_at: string }
interface LoggingConfig { enabled: boolean; default_channel_id: string | null; events: Record<string, { enabled: boolean; channel_id: string | null }>; ignored_channels: string[] }
interface ReminderRow { id: number; user_id: string; user_name: string | null; text: string; due_at: string; status: string; error: string | null; channel_id: string | null }

const LIMIT = 50;
const STATUS: Record<string, [string, string]> = {
  pending: ["ожидает", "badge-accent"], sent: ["доставлено", "badge-ok"], failed: ["ошибка", "badge-err"], cancelled: ["отменено", ""],
};

function Events({ guildId }: { guildId: string }) {
  const meta = useMeta();
  const toast = useToast();
  const confirm = useConfirm();
  const [offset, setOffset] = useState(0);
  const [type, setType] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const qs = new URLSearchParams({ offset: String(offset), limit: String(LIMIT), search: query });
  if (type) qs.set("type", type);
  const { data, error, loading, reload } = useApi<Paged<LogRow>>(`/api/guilds/${guildId}/logs?${qs}`);

  const clear = async () => {
    const label = type ? `«${meta.log_events[type]}»` : "всех типов";
    if (!(await confirm({ title: `Очистить записи ${label}?`, text: "Удаляются только записи в базе Mishanya, сообщения в Discord-каналах остаются.", danger: true, confirmText: "Очистить" }))) return;
    try {
      const r = await del<{ removed: number }>(`/api/guilds/${guildId}/logs${type ? `?type=${type}` : ""}`);
      toast("ok", `Удалено записей: ${r.removed}`);
      reload();
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  return (
    <Card>
      <form className="row" style={{ marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); setOffset(0); setQuery(search.trim()); }}>
        <select className="select" style={{ maxWidth: 260 }} value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}>
          <option value="">Все события</option>
          {Object.entries(meta.log_events).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input className="input" style={{ maxWidth: 260 }} placeholder="Поиск по тексту или ID участника" value={search} onChange={(e) => setSearch(e.target.value)} />
        <button className="btn">Найти</button>
        <span className="spacer" />
        <button type="button" className="btn" onClick={reload}>Обновить</button>
        <button type="button" className="btn btn-danger" onClick={clear}>Очистить</button>
      </form>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.items.length === 0 ? <Empty icon="📜">Записей нет. Включите нужные типы событий на вкладке «Настройки».</Empty> : data && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Время</th><th>Тип</th><th>Событие</th><th>Участник</th></tr></thead>
            <tbody>
              {data.items.map((e) => (
                <tr key={e.id} onClick={() => setOpen(open === e.id ? null : e.id)} style={{ cursor: e.details ? "pointer" : undefined }}>
                  <td className="small nowrap">{fmtDate(e.created_at)}</td>
                  <td><span className="badge">{meta.log_events[e.type] ?? e.type}</span></td>
                  <td style={{ wordBreak: "break-word" }}>
                    {e.summary}
                    {open === e.id && e.details && <pre className="mono small muted" style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{JSON.stringify(e.details, null, 2)}</pre>}
                  </td>
                  <td className="small">{e.user_name ?? ""}{e.user_id && <div className="muted mono">{e.user_id}</div>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && <Pagination total={data.total} offset={offset} limit={LIMIT} onChange={setOffset} />}
    </Card>
  );
}

function SettingsTab({ guildId }: { guildId: string }) {
  const meta = useMeta();
  const c = useModuleConfig<LoggingConfig>(guildId, "logging");
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  return (
    <div className="stack">
      <Card>
        <ToggleRow title="Журналирование включено" hint="Модерация и автомодерация записываются в базу всегда — для истории"
          checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
        <div className="grid grid-2 mt">
          <Field label="Канал по умолчанию" hint="Используется для событий без собственного канала">
            <ChannelSelect value={d.default_channel_id} onChange={(v) => c.update((x) => { x.default_channel_id = v; })} placeholder="— только база данных —" />
          </Field>
          <Field label="Не журналировать события в каналах"><MultiChannelSelect value={d.ignored_channels} onChange={(v) => c.update((x) => { x.ignored_channels = v; })} /></Field>
        </div>
      </Card>
      <Card title="Типы событий" subtitle="Включённые события сохраняются в базу и отправляются в канал (свой или по умолчанию)">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Событие</th><th>Включено</th><th>Канал</th></tr></thead>
            <tbody>
              {Object.entries(meta.log_events).map(([k, label]) => {
                const ev = d.events[k] ?? { enabled: false, channel_id: null };
                return (
                  <tr key={k}>
                    <td>{label}</td>
                    <td><Toggle checked={ev.enabled} label={label} onChange={(v) => c.update((x) => { x.events[k] = { ...ev, enabled: v }; })} /></td>
                    <td style={{ minWidth: 220 }}>
                      <ChannelSelect value={ev.channel_id} placeholder={d.default_channel_id ? "— канал по умолчанию —" : "— только база данных —"}
                        onChange={(v) => c.update((x) => { x.events[k] = { ...ev, channel_id: v }; })} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="small muted mt">Текст удалённых и изменённых сообщений доступен, только если сообщение было в кэше бота (отправлено после запуска). Сохранение текста в базу настраивается в «Общих настройках».</p>
      </Card>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </div>
  );
}

function Reminders({ guildId }: { guildId: string }) {
  const toast = useToast();
  const { data, error, loading, reload } = useApi<ReminderRow[]>(`/api/guilds/${guildId}/reminders`);
  const cancel = async (r: ReminderRow) => {
    try { await del(`/api/guilds/${guildId}/reminders/${r.id}`); toast("ok", "Напоминание отменено"); reload(); } catch (e) { toast("err", errorText(e)); }
  };
  return (
    <Card title="Напоминания участников" subtitle="Созданы командой /remind на этом сервере. Хранятся в базе и переживают перезапуск.">
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.length === 0 ? <Empty icon="⏰">Напоминаний нет</Empty> : data && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>№</th><th>Участник</th><th>Текст</th><th>Когда</th><th>Куда</th><th>Статус</th><th></th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.id}</td>
                  <td className="small">{r.user_name ?? <span className="mono">{r.user_id}</span>}</td>
                  <td style={{ wordBreak: "break-word" }}>{r.text}</td>
                  <td className="small nowrap">{fmtDate(r.due_at)}</td>
                  <td className="small">{r.channel_id ? <ChannelName id={r.channel_id} /> : "ЛС"}</td>
                  <td><span className={`badge ${STATUS[r.status]?.[1] ?? ""}`} title={r.error ?? undefined}>{STATUS[r.status]?.[0] ?? r.status}</span></td>
                  <td>{r.status === "pending" && <button className="btn btn-sm" onClick={() => cancel(r)}>Отменить</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Logs() {
  const { guildId } = useParams();
  const [tab, setTab] = useState<"events" | "settings" | "reminders">("events");
  return (
    <>
      <PageHeader title="Журналы событий" subtitle="История событий сервера и настройки журналирования" />
      <Tabs tabs={[["events", "События"], ["settings", "Настройки"], ["reminders", "Напоминания"]]} value={tab} onChange={setTab} />
      {tab === "events" && <Events guildId={guildId!} />}
      {tab === "settings" && <SettingsTab guildId={guildId!} />}
      {tab === "reminders" && <Reminders guildId={guildId!} />}
    </>
  );
}
