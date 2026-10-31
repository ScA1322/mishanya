import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorText, post } from "../api";
import { useMeta } from "../App";
import { useApi, useModuleConfig, useUnsavedWarning } from "../hooks";
import { Icon } from "../icons";
import {
  Card, Empty, ErrorBox, Field, Loading, NumberInput, PageHeader, Picker, SaveBar, TemplateInput, ToggleRow, fmtDate, useConfirm,
  useResources, useToast,
} from "../ui";

interface Hub { channel_id: string; category_id: string | null; name_template: string; user_limit: number; owner_manage: boolean }
interface TempVoiceConfig { enabled: boolean; hubs: Hub[] }
interface Room { channel_id: string; name: string | null; members: number | null; owner_id: string; owner_name: string | null; created_at: string }

const VARS = ["username", "display_name", "server", "choose"];

function VoiceChannelSelect({ value, onChange, exclude = [] }: { value: string | null; onChange: (v: string | null) => void; exclude?: string[] }) {
  const { channels, connected } = useResources();
  const items = channels.filter((c) => c.type === "voice" && (c.id === value || !exclude.includes(c.id)))
    .map((c) => ({ id: c.id, label: c.name, group: c.category ?? null, icon: "volume" as const }));
  return <Picker items={items} value={value} onChange={onChange} placeholder="— голосовой канал —" missingLabel="Канал"
    searchPlaceholder="Поиск канала…" emptyText={connected ? "Голосовых каналов нет" : "Бот не подключён — каналы недоступны"} />;
}

function CategorySelect({ value, onChange, placeholder }: { value: string | null; onChange: (v: string | null) => void; placeholder: string }) {
  const { categories, connected } = useResources();
  const items = categories.map((c) => ({ id: c.id, label: c.name, icon: "folder" as const }));
  return <Picker items={items} value={value} onChange={onChange} placeholder={placeholder} clearLabel={placeholder} missingLabel="Категория"
    searchPlaceholder="Поиск категории…" emptyText={connected ? "Категорий нет" : "Бот не подключён — категории недоступны"} />;
}

function CreateHub({ guildId, dirty }: { guildId: string; dirty: boolean }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [name, setName] = useState("➕ Создать комнату");
  const [category, setCategory] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    if (dirty && !(await confirm({ title: "Есть несохранённые изменения", confirmText: "Продолжить",
      text: "Создание канала перезагрузит страницу, несохранённые изменения пропадут." }))) return;
    setBusy(true);
    try {
      await post(`/api/guilds/${guildId}/tempvoice/hub`, { name, category_id: category });
      toast("ok", "Канал-создатель создан и добавлен в настройки");
      setTimeout(() => window.location.reload(), 700);
    } catch (e) {
      toast("err", errorText(e));
      setBusy(false);
    }
  };
  return (
    <Card title="Создать канал-создатель" subtitle="Бот сам создаст голосовой канал и сразу включит голосовые комнаты">
      <div className="grid grid-3" style={{ alignItems: "end" }}>
        <Field label="Название канала"><input className="input" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Категория"><CategorySelect value={category} onChange={setCategory} placeholder="— без категории —" /></Field>
        <div><button className="btn btn-primary" disabled={busy || !name.trim()} onClick={create}><Icon name="plus" size={15} />{busy ? "Создаю…" : "Создать канал"}</button></div>
      </div>
    </Card>
  );
}

function Rooms({ guildId }: { guildId: string }) {
  const { data, error, loading, reload } = useApi<Room[]>(`/api/guilds/${guildId}/tempvoice/rooms`, [guildId]);
  return (
    <Card title="Активные комнаты" actions={<button className="btn btn-sm" onClick={reload}><Icon name="refresh" size={14} />Обновить</button>}>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.length === 0 ? <Empty icon="🔊">Сейчас комнат нет</Empty> : data && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Комната</th><th>Владелец</th><th>Участников</th><th>Создана</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.channel_id}>
                  <td>{r.name ?? <span className="muted">канал {r.channel_id}</span>}</td>
                  <td>{r.owner_name ?? r.owner_id}</td>
                  <td>{r.members ?? "—"}</td>
                  <td className="small">{fmtDate(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function TempVoice() {
  const { guildId } = useParams();
  const meta = useMeta();
  const c = useModuleConfig<TempVoiceConfig>(guildId!, "tempvoice");
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  const vars = Object.fromEntries(Object.entries(meta.template_variables).filter(([k]) => VARS.includes(k)));
  const setHub = (i: number, fn: (h: Hub) => void) => c.update((x) => fn(x.hubs[i]));
  const used = d.hubs.map((h) => h.channel_id);

  return (
    <>
      <PageHeader title="Голосовые комнаты" subtitle="Участник заходит в канал-создатель — бот создаёт ему личный голосовой канал и переносит туда. Пустые комнаты удаляются автоматически." />
      <div className="stack">
        <Card>
          <ToggleRow title="Голосовые комнаты включены" checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
          <div className="small muted mt">
            Владелец комнаты может управлять ей командами <code>/voice rename</code>, <code>/voice limit</code>, <code>/voice lock</code>,{" "}
            <code>/voice unlock</code>, <code>/voice kick</code>. Если владелец вышел, любой оставшийся может написать <code>/voice claim</code>.
            Боту нужны права «Управлять каналами» и «Перемещать участников».
          </div>
        </Card>

        <CreateHub guildId={guildId!} dirty={c.dirty} />

        <Card title={`Каналы-создатели (${d.hubs.length}/10)`}
          actions={d.hubs.length < 10 && (
            <button className="btn btn-sm" onClick={() => c.update((x) => { x.hubs.push({ channel_id: "", category_id: null, name_template: "🔊 Комната {display_name}", user_limit: 0, owner_manage: true }); })}>
              <Icon name="plus" size={14} />Добавить существующий канал
            </button>
          )}>
          {d.hubs.length === 0 ? <div className="small muted">Пока нет ни одного. Создайте канал выше или выберите существующий голосовой канал.</div> : d.hubs.map((h, i) => (
            <div className="menu-item" key={i}>
              <div className="menu-item-head">
                <strong>Канал-создатель {i + 1}</strong><span className="spacer" />
                <button className="icon-btn" onClick={() => c.update((x) => { x.hubs.splice(i, 1); })} aria-label="Убрать"><Icon name="trash" size={15} /></button>
              </div>
              <div className="grid grid-2">
                <Field label="Голосовой канал, в который заходят"><VoiceChannelSelect value={h.channel_id || null} exclude={used} onChange={(v) => setHub(i, (x) => { x.channel_id = v ?? ""; })} /></Field>
                <Field label="Где создавать комнаты"><CategorySelect value={h.category_id} placeholder="— как у канала-создателя —" onChange={(v) => setHub(i, (x) => { x.category_id = v; })} /></Field>
                <Field label="Название комнаты"><TemplateInput value={h.name_template} vars={vars} rows={1} maxLength={100} onChange={(v) => setHub(i, (x) => { x.name_template = v; })} /></Field>
                <Field label="Лимит участников" hint="0 — без ограничения"><NumberInput value={h.user_limit} min={0} max={99} onChange={(v) => setHub(i, (x) => { x.user_limit = v; })} /></Field>
              </div>
              <ToggleRow title="Владелец управляет комнатой" hint="Может переименовать, менять лимит, отключать участников прямо в Discord"
                checked={h.owner_manage} onChange={(v) => setHub(i, (x) => { x.owner_manage = v; })} />
              {!h.channel_id && <div className="alert alert-err mt">Выберите голосовой канал</div>}
            </div>
          ))}
        </Card>

        <Rooms guildId={guildId!} />
      </div>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
