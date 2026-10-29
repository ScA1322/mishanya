import { useEffect, useState } from "react";
import { del, errorText, post, put } from "../api";
import { useApi, useUnsavedWarning } from "../hooks";
import { Card, Empty, ErrorBox, Field, Loading, NumberInput, PageHeader, SaveBar, ToggleRow, fmtBytes, fmtDate, useConfirm, useToast } from "../ui";

interface GlobalSettings {
  log_retention_days: number; max_event_logs_per_guild: number; store_message_content: boolean; auto_backup_enabled: boolean;
  auto_backup_interval_hours: number; auto_backup_keep: number; bot_status_text: string;
}
interface Backup { name: string; size_bytes: number; created_at: string }

function Backups() {
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, loading, reload } = useApi<Backup[]>("/api/backups");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try { const r = await post<{ name: string }>("/api/backups"); toast("ok", `Копия создана: ${r.name}`); reload(); }
    catch (e) { toast("err", errorText(e)); } finally { setBusy(false); }
  };
  const restore = async (b: Backup) => {
    const ok = await confirm({
      title: "Восстановить базу из копии?", danger: true, confirmText: "Восстановить", typeToConfirm: "ВОССТАНОВИТЬ",
      text: <>Текущие данные будут заменены содержимым <code>{b.name}</code>. Перед этим автоматически создаётся страховочная копия текущей базы. После восстановления может потребоваться снова войти в панель.</>,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const r = await post<{ safety_backup: string | null }>("/api/backups/restore", { name: b.name });
      toast("ok", `База восстановлена${r.safety_backup ? `. Страховочная копия: ${r.safety_backup}` : ""}`);
      setTimeout(() => window.location.reload(), 1200);
    } catch (e) { toast("err", errorText(e)); } finally { setBusy(false); }
  };
  const remove = async (b: Backup) => {
    if (!(await confirm({ title: `Удалить копию ${b.name}?`, danger: true, confirmText: "Удалить" }))) return;
    try { await del(`/api/backups/${encodeURIComponent(b.name)}`); toast("ok", "Копия удалена"); reload(); } catch (e) { toast("err", errorText(e)); }
  };

  return (
    <Card title="Резервные копии" subtitle="Хранятся в папке backups рядом с приложением"
      actions={<button className="btn btn-primary" disabled={busy} onClick={create}>Создать копию</button>}>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.length === 0 ? <Empty icon="💾">Копий пока нет</Empty> : data && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Файл</th><th>Размер</th><th>Создана</th><th></th></tr></thead>
            <tbody>
              {data.map((b) => (
                <tr key={b.name}>
                  <td className="mono">{b.name}</td>
                  <td>{fmtBytes(b.size_bytes)}</td>
                  <td className="small">{fmtDate(b.created_at)}</td>
                  <td className="nowrap">
                    <button className="btn btn-sm" disabled={busy} onClick={() => restore(b)}>Восстановить</button>{" "}
                    <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => remove(b)}>Удалить</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Settings() {
  const toast = useToast();
  const { data, error, loading, reload, setData } = useApi<GlobalSettings>("/api/settings");
  const [draft, setDraft] = useState<GlobalSettings | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (data) setDraft({ ...data }); }, [data]);
  const dirty = !!data && !!draft && JSON.stringify(data) !== JSON.stringify(draft);
  useUnsavedWarning(dirty);

  const save = async () => {
    setSaving(true);
    try { setData(await put<GlobalSettings>("/api/settings", draft)); toast("ok", "Настройки сохранены"); }
    catch (e) { toast("err", errorText(e)); } finally { setSaving(false); }
  };
  const cleanup = async () => {
    try { const r = await post<{ removed: number }>("/api/maintenance/cleanup"); toast("ok", `Удалено старых записей: ${r.removed}`); }
    catch (e) { toast("err", errorText(e)); }
  };
  const sync = async () => {
    try { const r = await post<{ count: number }>("/api/commands/sync"); toast("ok", `Синхронизировано команд: ${r.count}`); }
    catch (e) { toast("err", errorText(e)); }
  };

  const set = <K extends keyof GlobalSettings>(k: K, v: GlobalSettings[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));

  return (
    <>
      <PageHeader title="Общие настройки" subtitle="Параметры, общие для всех серверов" />
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !draft ? <Loading /> : draft && (
        <div className="stack">
          <Card title="Бот">
            <div style={{ maxWidth: 420 }}>
              <Field label="Статус бота в Discord"><input className="input" value={draft.bot_status_text} maxLength={128} onChange={(e) => set("bot_status_text", e.target.value)} /></Field>
            </div>
            <div className="row mt">
              <button className="btn" onClick={sync}>Пересинхронизировать slash-команды</button>
              <span className="small muted">Обычно не требуется: команды синхронизируются автоматически при изменениях.</span>
            </div>
          </Card>
          <Card title="Журналы и хранение данных">
            <div className="grid grid-2">
              <Field label="Хранить записи журналов, дней"><NumberInput value={draft.log_retention_days} min={1} max={3650} onChange={(v) => set("log_retention_days", v)} /></Field>
              <Field label="Максимум записей журнала на сервер"><NumberInput value={draft.max_event_logs_per_guild} min={100} max={1000000} onChange={(v) => set("max_event_logs_per_guild", v)} /></Field>
            </div>
            <div className="mt">
              <ToggleRow title="Сохранять текст удалённых и изменённых сообщений в базу" hint="По умолчанию выключено: в базе остаются только факты событий, текст виден лишь в Discord-канале журнала"
                checked={draft.store_message_content} onChange={(v) => set("store_message_content", v)} />
            </div>
            <div className="row mt"><button className="btn" onClick={cleanup}>Очистить устаревшие записи сейчас</button>
              <span className="small muted">Автоматически выполняется каждые 30 минут.</span></div>
          </Card>
          <Card title="Автоматическое резервное копирование">
            <ToggleRow title="Создавать копии автоматически" checked={draft.auto_backup_enabled} onChange={(v) => set("auto_backup_enabled", v)} />
            {draft.auto_backup_enabled && (
              <div className="grid grid-2 mt">
                <Field label="Интервал, часов"><NumberInput value={draft.auto_backup_interval_hours} min={1} max={720} onChange={(v) => set("auto_backup_interval_hours", v)} /></Field>
                <Field label="Хранить автоматических копий"><NumberInput value={draft.auto_backup_keep} min={1} max={200} onChange={(v) => set("auto_backup_keep", v)} /></Field>
              </div>
            )}
          </Card>
          <Backups />
        </div>
      )}
      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={() => data && setDraft({ ...data })} />
    </>
  );
}
