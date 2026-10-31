import { useState } from "react";
import { useParams } from "react-router-dom";
import { del, errorText, post } from "../api";
import { useApi, useModuleConfig, useUnsavedWarning } from "../hooks";
import { Icon } from "../icons";
import type { Paged } from "../types";
import {
  Card, ChannelSelect, Empty, ErrorBox, Field, Loading, NumberInput, PageHeader, Pagination, SaveBar, Tabs, Toggle, ToggleRow,
  fmtDate, useConfirm, useToast,
} from "../ui";

interface WarnAction { count: number; action: "timeout" | "kick" | "ban"; duration_minutes: number }
interface ModerationConfig { enabled: boolean; log_channel_id: string | null; dm_on_action: boolean; warn_actions: WarnAction[]; warning_expiry_days: number }
interface WarningRow { id: number; user_id: string; user_name: string | null; moderator_name: string | null; moderator_id: string; reason: string; active: boolean; created_at: string; removed_at: string | null }
interface CaseRow { id: number; case_number: number; action: string; target_id: string | null; target_name: string; moderator_name: string; reason: string; duration_seconds: number | null; created_at: string }

export const ACTION_LABELS: Record<string, string> = {
  kick: "Кик", ban: "Бан", unban: "Разбан", timeout: "Тайм-аут", untimeout: "Снятие тайм-аута", warn: "Предупреждение",
  unwarn: "Снятие предупреждения", clearwarnings: "Сброс предупреждений", clear: "Очистка", purge: "Очистка",
  slowmode: "Медленный режим", lock: "Блокировка канала", unlock: "Разблокировка", nickname: "Никнейм",
  role_add: "Выдача роли", role_remove: "Снятие роли", automod_timeout: "Тайм-аут (автомод)",
};

const LIMIT = 30;

function Settings({ guildId }: { guildId: string }) {
  const c = useModuleConfig<ModerationConfig>(guildId, "moderation");
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  const counts = d.warn_actions.map((a) => a.count);
  const dup = counts.length !== new Set(counts).size;
  return (
    <div className="stack">
      <Card>
        <ToggleRow title="Модуль модерации включён" hint="Если выключено, команды /kick, /ban, /timeout, /warn и др. будут отвечать, что модуль отключён"
          checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
        <ToggleRow title="Уведомлять участника в личных сообщениях" hint="О предупреждениях, тайм-аутах, кике и бане (если ЛС открыты)"
          checked={d.dm_on_action} onChange={(v) => c.update((x) => { x.dm_on_action = v; })} />
        <div className="grid grid-2 mt">
          <Field label="Канал журнала модерации" hint="Сюда отправляется каждое действие модерации">
            <ChannelSelect value={d.log_channel_id} onChange={(v) => c.update((x) => { x.log_channel_id = v; })} placeholder="— не отправлять —" />
          </Field>
          <Field label="Срок действия предупреждений, дней" hint="0 — предупреждения не истекают">
            <NumberInput value={d.warning_expiry_days} min={0} max={3650} onChange={(v) => c.update((x) => { x.warning_expiry_days = v; })} />
          </Field>
        </div>
      </Card>
      <Card title="Автоматические действия за предупреждения" subtitle="Срабатывают, когда число активных предупреждений участника достигает порога"
        actions={<button className="btn btn-sm" disabled={d.warn_actions.length >= 20} onClick={() => c.update((x) => {
          const next = Math.max(0, ...x.warn_actions.map((a) => a.count)) + 1;
          x.warn_actions.push({ count: next, action: "timeout", duration_minutes: 60 });
        })}><Icon name="plus" size={14} />Добавить порог</button>}>
        {d.warn_actions.length === 0 ? <Empty icon="⚖️">Автоматических действий нет</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Предупреждений</th><th>Действие</th><th>Длительность тайм-аута, мин</th><th></th></tr></thead>
              <tbody>
                {d.warn_actions.map((a, i) => (
                  <tr key={i}>
                    <td><NumberInput className="input input-sm" value={a.count} min={1} max={100} onChange={(v) => c.update((x) => { x.warn_actions[i].count = v; })} /></td>
                    <td>
                      <select className="select" value={a.action} onChange={(e) => c.update((x) => { x.warn_actions[i].action = e.target.value as WarnAction["action"]; })}>
                        <option value="timeout">Тайм-аут</option><option value="kick">Кик</option><option value="ban">Бан</option>
                      </select>
                    </td>
                    <td>{a.action === "timeout" ? <NumberInput className="input input-sm" value={a.duration_minutes} min={1} max={40320} onChange={(v) => c.update((x) => { x.warn_actions[i].duration_minutes = v; })} /> : <span className="muted">—</span>}</td>
                    <td><button className="btn btn-sm btn-danger" onClick={() => c.update((x) => { x.warn_actions.splice(i, 1); })}>Удалить</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {dup && <div className="alert alert-err mt">Пороги не должны повторяться.</div>}
        <p className="small muted mt">Максимальный тайм-аут в Discord — 28 дней (40320 минут).</p>
      </Card>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </div>
  );
}

function Warnings({ guildId }: { guildId: string }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [offset, setOffset] = useState(0);
  const [userId, setUserId] = useState("");
  const [filter, setFilter] = useState("");
  const [inactive, setInactive] = useState(false);
  const qs = new URLSearchParams({ offset: String(offset), limit: String(LIMIT), include_inactive: String(inactive) });
  if (filter) qs.set("user_id", filter);
  const { data, error, loading, reload } = useApi<Paged<WarningRow>>(`/api/guilds/${guildId}/warnings?${qs}`);

  const remove = async (w: WarningRow) => {
    if (!(await confirm({ title: `Снять предупреждение #${w.id}?`, confirmText: "Снять" }))) return;
    try { await del(`/api/guilds/${guildId}/warnings/${w.id}`); toast("ok", "Предупреждение снято"); reload(); } catch (e) { toast("err", errorText(e)); }
  };
  const clearUser = async () => {
    if (!(await confirm({ title: "Снять все предупреждения участника?", text: `ID ${filter}`, danger: true, confirmText: "Снять все" }))) return;
    try { const r = await post<{ removed: number }>(`/api/guilds/${guildId}/warnings/clear`, { user_id: filter }); toast("ok", `Снято: ${r.removed}`); reload(); } catch (e) { toast("err", errorText(e)); }
  };

  return (
    <Card>
      <form className="row" style={{ marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); if (!userId || /^\d{15,22}$/.test(userId)) { setFilter(userId); setOffset(0); } else toast("err", "ID — это число из 15–22 цифр"); }}>
        <input className="input mono" style={{ maxWidth: 260 }} placeholder="Фильтр по ID участника" value={userId} onChange={(e) => setUserId(e.target.value.trim())} />
        <button className="btn">Применить</button>
        {filter && <button type="button" className="btn btn-danger" onClick={clearUser}>Снять все у участника</button>}
        <span className="spacer" />
        <label className="row small"><Toggle checked={inactive} onChange={(v) => { setInactive(v); setOffset(0); }} label="Показывать снятые" />Показывать снятые и истёкшие</label>
      </form>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.items.length === 0 ? <Empty icon="✅">Предупреждений нет</Empty> : data && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>№</th><th>Участник</th><th>Причина</th><th>Модератор</th><th>Дата</th><th></th></tr></thead>
            <tbody>
              {data.items.map((w) => (
                <tr key={w.id} style={{ opacity: w.active ? 1 : 0.6 }}>
                  <td className="mono">#{w.id}</td>
                  <td><div>{w.user_name ?? "—"}</div><div className="small muted mono">{w.user_id}</div></td>
                  <td style={{ wordBreak: "break-word" }}>{w.reason || <span className="muted">без причины</span>}</td>
                  <td className="small">{w.moderator_name ?? w.moderator_id}</td>
                  <td className="small nowrap">{fmtDate(w.created_at)}</td>
                  <td>{w.active ? <button className="btn btn-sm" onClick={() => remove(w)}>Снять</button> : <span className="badge">снято</span>}</td>
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

function Cases({ guildId }: { guildId: string }) {
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const qs = new URLSearchParams({ offset: String(offset), limit: String(LIMIT) });
  if (action) qs.set("action", action);
  const { data, error, loading, reload } = useApi<Paged<CaseRow>>(`/api/guilds/${guildId}/cases?${qs}`);
  const dur = (s: number | null) => (s == null ? "" : s >= 86400 ? `${Math.round(s / 86400)} д` : s >= 3600 ? `${Math.round(s / 3600)} ч` : `${Math.round(s / 60)} мин`);
  return (
    <Card>
      <div className="row" style={{ marginBottom: 12 }}>
        <select className="select" style={{ maxWidth: 260 }} value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }}>
          <option value="">Все действия</option>
          {Object.entries(ACTION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="spacer" />
        <button className="btn" onClick={reload}>Обновить</button>
      </div>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.items.length === 0 ? <Empty icon="📒">Журнал модерации пуст</Empty> : data && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Случай</th><th>Действие</th><th>Участник</th><th>Модератор</th><th>Причина</th><th>Дата</th></tr></thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.id}>
                  <td className="mono">#{c.case_number}</td>
                  <td><span className="badge badge-accent">{ACTION_LABELS[c.action] ?? c.action}</span> <span className="small muted">{dur(c.duration_seconds)}</span></td>
                  <td>{c.target_name || "—"}{c.target_id && <div className="small muted mono">{c.target_id}</div>}</td>
                  <td className="small">{c.moderator_name || "—"}</td>
                  <td style={{ wordBreak: "break-word" }}>{c.reason || <span className="muted">—</span>}</td>
                  <td className="small nowrap">{fmtDate(c.created_at)}</td>
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

export default function Moderation() {
  const { guildId } = useParams();
  const [tab, setTab] = useState<"settings" | "warnings" | "cases">("settings");
  return (
    <>
      <PageHeader title="Модерация" subtitle="Команды /kick, /ban, /timeout, /warn и другие. Права проверяются при каждом вызове команды." />
      <Tabs tabs={[["settings", "Настройки"], ["warnings", "Предупреждения"], ["cases", "Журнал модерации"]]} value={tab} onChange={setTab} />
      {tab === "settings" && <Settings guildId={guildId!} />}
      {tab === "warnings" && <Warnings guildId={guildId!} />}
      {tab === "cases" && <Cases guildId={guildId!} />}
    </>
  );
}
