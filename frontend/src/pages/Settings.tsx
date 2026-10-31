import { useEffect, useState } from "react";
import { del, errorText, post, put } from "../api";
import { useApi, useUnsavedWarning } from "../hooks";
import { Icon } from "../icons";
import { Card, Empty, ErrorBox, Field, Loading, NumberInput, PageHeader, SaveBar, ToggleRow, fmtBytes, fmtDate, useConfirm, useToast } from "../ui";

interface GlobalSettings {
  log_retention_days: number; max_event_logs_per_guild: number; store_message_content: boolean; auto_backup_enabled: boolean;
  auto_backup_interval_hours: number; auto_backup_keep: number; bot_status_text: string;
  auto_update_enabled: boolean; update_check_interval_hours: number;
}
interface UpdateStatus {
  supported: boolean; reason: string | null; branch: string | null; upstream: string | null; current: string | null; remote: string | null;
  behind: number; ahead: number; commits: { sha: string; author: string; date: string; subject: string }[]; last_check: string | null;
  last_error: string | null; last_update: string | null; busy: boolean; restart_required: boolean; launcher: boolean;
}

/** Ждёт, пока приложение перезапустится после обновления, и перезагружает страницу. */
async function waitForRestart() {
  await new Promise((r) => setTimeout(r, 4000));
  for (let i = 0; i < 90; i++) {
    try {
      const r = await fetch("/api/auth/me", { credentials: "same-origin" });
      if (r.ok) { window.location.reload(); return; }
    } catch { /* приложение ещё перезапускается */ }
    await new Promise((r) => setTimeout(r, 2000));
  }
  window.location.reload();
}

function Updates({ draft, set }: { draft: GlobalSettings; set: <K extends keyof GlobalSettings>(k: K, v: GlobalSettings[K]) => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, loading, reload, setData } = useApi<UpdateStatus>("/api/update");
  const [busy, setBusy] = useState<"check" | "apply" | "restart" | null>(null);

  const check = async () => {
    setBusy("check");
    try { const s = await post<UpdateStatus>("/api/update/check"); setData(s); if (!s.last_error) toast("ok", s.behind ? `Доступно обновление: ${s.behind} изм.` : "Установлена последняя версия"); }
    catch (e) { toast("err", errorText(e)); } finally { setBusy(null); }
  };
  const apply = async () => {
    if (!(await confirm({ title: "Установить обновление?", confirmText: "Обновить",
      text: "Перед обновлением создаётся резервная копия базы. Бот перезапустится и будет недоступен несколько секунд." }))) return;
    setBusy("apply");
    try {
      const r = await post<{ restarting: boolean; version: string; status: UpdateStatus }>("/api/update/apply");
      setData(r.status);
      if (r.restarting) { toast("ok", `Обновлено до ${r.version}. Перезапуск…`); setBusy("restart"); await waitForRestart(); return; }
      toast("ok", `Файлы обновлены до ${r.version}. Перезапустите Mishanya (stop.bat, затем start.bat).`);
    } catch (e) { toast("err", errorText(e)); reload(); }
    setBusy(null);
  };

  return (
    <Card title="Обновления" subtitle="Новые версии загружаются с GitHub (git pull)"
      actions={data?.supported && <button className="btn btn-sm" disabled={!!busy} onClick={check}><Icon name="refresh" size={14} />{busy === "check" ? "Проверка…" : "Проверить сейчас"}</button>}>
      <ToggleRow title="Обновляться автоматически" hint="Mishanya сама проверяет GitHub, ставит обновление и перезапускается. Работает при запуске через start.bat."
        checked={draft.auto_update_enabled} onChange={(v) => set("auto_update_enabled", v)} />
      {draft.auto_update_enabled && (
        <div className="mt" style={{ maxWidth: 260 }}>
          <Field label="Проверять каждые, часов"><NumberInput value={draft.update_check_interval_hours} min={1} max={168} onChange={(v) => set("update_check_interval_hours", v)} /></Field>
        </div>
      )}
      <div className="mt">
        {error && <ErrorBox error={error} onRetry={reload} />}
        {loading && !data ? <Loading text="Проверяю обновления…" /> : data && (
          <div className="stack">
            {!data.supported && <div className="alert alert-warn">{data.reason}</div>}
            {data.supported && (
              <div className="small">
                Версия: <code>{data.current}</code> · ветка <code>{data.upstream}</code>
                {data.last_check && <span className="muted"> · проверено {fmtDate(data.last_check)}</span>}
              </div>
            )}
            {data.last_error && <div className="alert alert-err">{data.last_error}</div>}
            {!data.launcher && data.supported && (
              <div className="alert alert-info">Приложение запущено не через start.bat — после обновления перезапустите его вручную.</div>
            )}
            {data.restart_required && busy !== "restart" && <div className="alert alert-warn">Обновление установлено — нужен перезапуск Mishanya.</div>}
            {busy === "restart" && <Loading text="Перезапуск после обновления… Страница обновится сама." />}
            {data.supported && data.behind === 0 && !data.last_error && !data.restart_required && <div className="alert alert-ok">Установлена последняя версия.</div>}
            {data.behind > 0 && (
              <>
                <div className="alert alert-info row" style={{ flexWrap: "nowrap" }}>
                  <Icon name="download" size={16} /><span>Доступно обновление: изменений — {data.behind}.</span>
                  <span className="spacer" />
                  <button className="btn btn-sm btn-primary" disabled={!!busy} onClick={apply}>{busy === "apply" ? "Обновление…" : "Обновить"}</button>
                </div>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  {data.commits.map((c) => <li key={c.sha}><code>{c.sha}</code> {c.subject} <span className="muted">— {c.author}, {fmtDate(c.date)}</span></li>)}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </Card>
  );
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
          <Updates draft={draft} set={set} />
          <Backups />
        </div>
      )}
      <SaveBar dirty={dirty} saving={saving} onSave={save} onReset={() => data && setDraft({ ...data })} />
    </>
  );
}
