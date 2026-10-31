import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorText, post } from "../api";
import { useMeta } from "../App";
import { useApi, useModuleConfig, useUnsavedWarning } from "../hooks";
import { Icon } from "../icons";
import type { Paged } from "../types";
import {
  Card, ChannelName, ChannelSelect, Empty, ErrorBox, Field, Loading, MultiRoleSelect, NumberInput, PageHeader, Pagination, Picker, SaveBar,
  Tabs, TemplateInput, ToggleRow, fmtDate, useConfirm, useResources, useToast,
} from "../ui";

interface TicketsConfig {
  enabled: boolean; category_id: string | null; support_roles: string[]; log_channel_id: string | null; max_open_per_user: number;
  ask_topic: boolean; channel_name: string; opening_message: string; ping_support: boolean; transcript: boolean; delete_delay_seconds: number;
}
interface Ticket {
  id: number; number: number; status: "open" | "closed"; topic: string; owner_id: string; owner_name: string; channel_id: string | null;
  claimed_by: string | null; claimed_name: string | null; closed_by_name: string | null; close_reason: string; created_at: string; closed_at: string | null;
}

const VARS = ["user", "username", "display_name", "server", "number", "topic", "choose"];
const LIMIT = 25;

function CategorySelect({ value, onChange }: { value: string | null; onChange: (v: string | null) => void }) {
  const { categories, connected } = useResources();
  const items = categories.map((c) => ({ id: c.id, label: c.name, icon: "folder" as const }));
  return <Picker items={items} value={value} onChange={onChange} placeholder="— без категории —" clearLabel="— без категории —" missingLabel="Категория"
    searchPlaceholder="Поиск категории…" emptyText={connected ? "Категория не найдена" : "Бот не подключён — категории недоступны"} />;
}

function TicketList({ guildId }: { guildId: string }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [status, setStatus] = useState<"open" | "closed" | "all">("open");
  const [offset, setOffset] = useState(0);
  const q = `status=${status === "all" ? "" : status}&offset=${offset}&limit=${LIMIT}`;
  const { data, error, loading, reload } = useApi<Paged<Ticket> & { open: number }>(`/api/guilds/${guildId}/tickets?${q}`, [guildId, q]);

  const close = async (t: Ticket) => {
    if (!(await confirm({ title: `Закрыть тикет #${String(t.number).padStart(4, "0")}?`, danger: true, confirmText: "Закрыть",
      text: "Стенограмма уйдёт в канал журнала тикетов (если он выбран), канал тикета будет удалён." }))) return;
    try { await post(`/api/guilds/${guildId}/tickets/${t.id}/close`, {}); toast("ok", "Тикет закрывается"); setTimeout(reload, 800); }
    catch (e) { toast("err", errorText(e)); }
  };

  return (
    <Card title="Тикеты" subtitle={data ? `Открыто сейчас: ${data.open}` : undefined}
      actions={<button className="btn btn-sm" onClick={reload}><Icon name="refresh" size={14} />Обновить</button>}>
      <Tabs tabs={[["open", "Открытые"], ["closed", "Закрытые"], ["all", "Все"]]} value={status} onChange={(s) => { setStatus(s); setOffset(0); }} />
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.items.length === 0 ? <Empty icon="🎫">Тикетов нет</Empty> : data && (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>№</th><th>Автор</th><th>Тема</th><th>Ведёт</th><th>Создан</th><th>Статус</th><th></th></tr></thead>
              <tbody>
                {data.items.map((t) => (
                  <tr key={t.id}>
                    <td className="mono">#{String(t.number).padStart(4, "0")}</td>
                    <td>{t.owner_name}</td>
                    <td className="small" style={{ maxWidth: 280, wordBreak: "break-word" }}>{t.topic || <span className="muted">—</span>}</td>
                    <td className="small">{t.claimed_name ?? (t.claimed_by ? t.claimed_by : <span className="muted">—</span>)}</td>
                    <td className="small">{fmtDate(t.created_at)}</td>
                    <td>
                      {t.status === "open"
                        ? <span className="badge badge-ok">открыт</span>
                        : <span className="badge" title={t.close_reason}>закрыт {fmtDate(t.closed_at)}{t.closed_by_name ? ` · ${t.closed_by_name}` : ""}</span>}
                      {t.status === "open" && t.channel_id && <div className="small muted"><ChannelName id={t.channel_id} /></div>}
                    </td>
                    <td className="nowrap">{t.status === "open" && <button className="btn btn-sm btn-danger" onClick={() => close(t)}>Закрыть</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={data.total} offset={offset} limit={LIMIT} onChange={setOffset} />
        </>
      )}
    </Card>
  );
}

export default function Tickets() {
  const { guildId } = useParams();
  const meta = useMeta();
  const c = useModuleConfig<TicketsConfig>(guildId!, "tickets");
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  const vars = Object.fromEntries(Object.entries(meta.template_variables).filter(([k]) => VARS.includes(k)));
  return (
    <>
      <PageHeader title="Тикеты" subtitle="Участник нажимает кнопку — бот создаёт приватный канал, который видят только он и команда поддержки." />
      <div className="stack">
        <Card>
          <ToggleRow title="Тикеты включены" checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
          <div className="alert alert-info row mt" style={{ flexWrap: "nowrap" }}>
            <Icon name="info" size={16} />
            <span>Чтобы участники могли открывать тикеты, создайте в разделе <Link to={`/servers/${guildId}/menus`}>«Меню с кнопками»</Link> сообщение
              с кнопкой действия «Открыть тикет». Также работает команда <code>/ticket open</code>.</span>
          </div>
        </Card>
        <div className="grid grid-2">
          <Card title="Каналы и доступ">
            <div className="stack">
              <Field label="Категория для каналов тикетов" hint="Новые каналы появляются в этой категории"><CategorySelect value={d.category_id} onChange={(v) => c.update((x) => { x.category_id = v; })} /></Field>
              <Field label="Роли поддержки" hint="Видят все тикеты и могут их закрывать. Администраторы видят тикеты всегда.">
                <MultiRoleSelect value={d.support_roles} onChange={(v) => c.update((x) => { x.support_roles = v; })} />
              </Field>
              <Field label="Канал журнала тикетов" hint="Уведомления об открытии/закрытии и стенограммы переписки">
                <ChannelSelect value={d.log_channel_id} onChange={(v) => c.update((x) => { x.log_channel_id = v; })} />
              </Field>
              <div className="grid grid-2">
                <Field label="Открытых тикетов на участника"><NumberInput value={d.max_open_per_user} min={1} max={10} onChange={(v) => c.update((x) => { x.max_open_per_user = v; })} /></Field>
                <Field label="Удалять канал через, сек" hint="После закрытия"><NumberInput value={d.delete_delay_seconds} min={0} max={3600} onChange={(v) => c.update((x) => { x.delete_delay_seconds = v; })} /></Field>
              </div>
            </div>
          </Card>
          <Card title="Сообщения">
            <div className="stack">
              <Field label="Название канала" hint="{number} — номер тикета, {username} — имя автора">
                <input className="input" value={d.channel_name} maxLength={90} onChange={(e) => c.update((x) => { x.channel_name = e.target.value; })} />
              </Field>
              <Field label="Первое сообщение в тикете"><TemplateInput value={d.opening_message} vars={vars} onChange={(v) => c.update((x) => { x.opening_message = v; })} /></Field>
              <div>
                <ToggleRow title="Спрашивать тему обращения" hint="Перед созданием откроется окно с полем «Тема»" checked={d.ask_topic} onChange={(v) => c.update((x) => { x.ask_topic = v; })} />
                <ToggleRow title="Упоминать роли поддержки" checked={d.ping_support} onChange={(v) => c.update((x) => { x.ping_support = v; })} />
                <ToggleRow title="Сохранять стенограмму" hint="Файл с перепиской отправляется в канал журнала при закрытии" checked={d.transcript} onChange={(v) => c.update((x) => { x.transcript = v; })} />
              </div>
            </div>
          </Card>
        </div>
        <TicketList guildId={guildId!} />
      </div>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
