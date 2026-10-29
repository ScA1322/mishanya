import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, del, errorText, post, put } from "../api";
import { useMeta } from "../App";
import { useApi, useModuleConfig, usePreview } from "../hooks";
import type { EmbedConfig } from "../types";
import {
  Card, DiscordPreview, EmbedEditor, Empty, ErrorBox, Field, Loading, Modal, MultiChannelSelect, MultiRoleSelect, NumberInput,
  PageHeader, SaveBar, TemplateInput, Toggle, ToggleRow, useConfirm, useToast,
} from "../ui";

interface Command {
  id: number; name: string; description: string; response: string; embed: EmbedConfig | null; enabled: boolean;
  allowed_roles: string[]; allowed_channels: string[]; cooldown_seconds: number; delete_trigger: boolean; send_dm: boolean; uses: number;
}
type Draft = Omit<Command, "id" | "uses" | "embed"> & { embed: EmbedConfig };

const EMPTY_EMBED: EmbedConfig = { enabled: false, title: "", description: "", color: "#5865F2", footer: "", image_url: "", show_avatar: false };
const VARS = ["user", "username", "display_name", "user_id", "server", "member_count", "channel", "args", "choose"];

function toDraft(c: Command): Draft {
  return {
    name: c.name, description: c.description, response: c.response, embed: c.embed ?? EMPTY_EMBED, enabled: c.enabled,
    allowed_roles: c.allowed_roles, allowed_channels: c.allowed_channels, cooldown_seconds: c.cooldown_seconds,
    delete_trigger: c.delete_trigger, send_dm: c.send_dm,
  };
}

function Editor({ guildId, initial, prefix, onClose, onSaved }: { guildId: string; initial: Command | null; prefix: string; onClose: () => void; onSaved: () => void }) {
  const meta = useMeta();
  const toast = useToast();
  const vars = Object.fromEntries(Object.entries(meta.template_variables).filter(([k]) => VARS.includes(k)));
  const [d, setD] = useState<Draft>(() => initial ? toDraft(initial)
    : { name: "", description: "", response: "", embed: EMPTY_EMBED, enabled: true, allowed_roles: [], allowed_channels: [], cooldown_seconds: 0, delete_trigger: false, send_dm: false });
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const preview = usePreview(guildId, d.response, d.embed, { args: "пример" });
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const nameOk = /^[a-z0-9_\-а-яё]{1,32}$/.test(d.name);

  const save = async () => {
    setBusy(true);
    setErrors([]);
    const body = { ...d, embed: d.embed.enabled ? d.embed : null };
    try {
      if (initial) await put(`/api/guilds/${guildId}/commands/${initial.id}`, body);
      else await post(`/api/guilds/${guildId}/commands`, body);
      toast("ok", initial ? "Команда обновлена" : "Команда создана");
      onSaved();
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.details.length) setErrors(e.details.map((x) => x.msg));
      else setErrors([errorText(e)]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={initial ? `Команда ${prefix}${initial.name}` : "Новая команда"} onClose={onClose} wide>
      <div className="stack">
        <div className="grid grid-2">
          <Field label="Название" hint={`Вызов: ${prefix}${d.name || "название"} или /cmd ${d.name || "название"}`}
            error={d.name && !nameOk ? "Только строчные буквы, цифры, «-» и «_», до 32 символов" : undefined}>
            <input className="input" value={d.name} maxLength={32} onChange={(e) => set("name", e.target.value.toLowerCase().replace(/\s/g, ""))} />
          </Field>
          <Field label="Описание" hint="Показывается в подсказках /cmd"><input className="input" value={d.description} maxLength={100} onChange={(e) => set("description", e.target.value)} /></Field>
        </div>
        <div className="grid grid-2">
          <div className="stack">
            <Field label="Текст ответа"><TemplateInput value={d.response} vars={vars} onChange={(v) => set("response", v)} rows={4} /></Field>
            <EmbedEditor value={d.embed} vars={vars} onChange={(e) => set("embed", e)} />
          </div>
          <div className="field"><span className="field-label">Предпросмотр</span><DiscordPreview data={preview.data} loading={preview.loading} /></div>
        </div>
        <div className="grid grid-3">
          <Field label="Задержка на участника, сек"><NumberInput value={d.cooldown_seconds} min={0} max={86400} onChange={(v) => set("cooldown_seconds", v)} /></Field>
          <Field label="Доступна ролям" hint="Пусто — всем"><MultiRoleSelect value={d.allowed_roles} onChange={(v) => set("allowed_roles", v)} /></Field>
          <Field label="Доступна в каналах" hint="Пусто — везде"><MultiChannelSelect value={d.allowed_channels} onChange={(v) => set("allowed_channels", v)} /></Field>
        </div>
        <div>
          <ToggleRow title="Команда включена" checked={d.enabled} onChange={(v) => set("enabled", v)} />
          <ToggleRow title="Удалять сообщение с вызовом" hint="Только для вызова через префикс" checked={d.delete_trigger} onChange={(v) => set("delete_trigger", v)} />
          <ToggleRow title="Отвечать в личные сообщения" checked={d.send_dm} onChange={(v) => set("send_dm", v)} />
        </div>
        {errors.map((e) => <div key={e} className="alert alert-err">{e}</div>)}
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>Отмена</button>
        <button className="btn btn-primary" disabled={busy || !nameOk} onClick={save}>{busy ? "Сохранение…" : "Сохранить"}</button>
      </div>
    </Modal>
  );
}

export default function Commands() {
  const { guildId } = useParams();
  const toast = useToast();
  const confirm = useConfirm();
  const cfg = useModuleConfig<{ enabled: boolean; prefix: string }>(guildId!, "custom_commands");
  const { data, error, loading, reload, setData } = useApi<Command[]>(`/api/guilds/${guildId}/commands`, [guildId]);
  const [editing, setEditing] = useState<Command | "new" | null>(null);
  const prefix = cfg.saved?.prefix ?? "!";

  const toggle = async (cmd: Command, enabled: boolean) => {
    try {
      const draft = toDraft(cmd);
      const upd = await put<Command>(`/api/guilds/${guildId}/commands/${cmd.id}`, { ...draft, embed: cmd.embed, enabled });
      setData((list) => list?.map((x) => (x.id === cmd.id ? upd : x)) ?? null);
    } catch (e) {
      toast("err", errorText(e));
    }
  };
  const remove = async (cmd: Command) => {
    if (!(await confirm({ title: `Удалить команду ${prefix}${cmd.name}?`, danger: true, confirmText: "Удалить" }))) return;
    try {
      await del(`/api/guilds/${guildId}/commands/${cmd.id}`);
      toast("ok", "Команда удалена");
      reload();
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  return (
    <>
      <PageHeader title="Пользовательские команды" subtitle="Ответы-шаблоны без выполнения кода. Вызов через префикс или /cmd."
        actions={<button className="btn btn-primary" onClick={() => setEditing("new")}>+ Новая команда</button>} />
      <div className="stack">
        {cfg.draft && (
          <Card>
            <ToggleRow title="Пользовательские команды включены" checked={cfg.draft.enabled} onChange={(v) => cfg.update((x) => { x.enabled = v; })} />
            <div className="mt" style={{ maxWidth: 240 }}>
              <Field label="Префикс" hint="Например, ! — тогда команда вызывается как !правила">
                <input className="input" value={cfg.draft.prefix} maxLength={5} onChange={(e) => cfg.update((x) => { x.prefix = e.target.value.replace(/\s/g, ""); })} />
              </Field>
            </div>
          </Card>
        )}
        <Card title={`Команды${data ? ` (${data.length})` : ""}`}>
          {error && <ErrorBox error={error} onRetry={reload} />}
          {loading && !data ? <Loading /> : data && data.length === 0 ? <Empty icon="✨">Команд пока нет — создайте первую</Empty> : data && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Команда</th><th>Ответ</th><th>Ограничения</th><th>Вызовов</th><th>Вкл.</th><th></th></tr></thead>
                <tbody>
                  {data.map((cmd) => (
                    <tr key={cmd.id}>
                      <td><code>{prefix}{cmd.name}</code><div className="small muted">{cmd.description}</div></td>
                      <td className="small" style={{ maxWidth: 320, wordBreak: "break-word" }}>{cmd.response.slice(0, 120) || (cmd.embed ? "[embed]" : "")}{cmd.response.length > 120 && "…"}</td>
                      <td className="small muted">
                        {cmd.cooldown_seconds > 0 && <div>задержка {cmd.cooldown_seconds} с</div>}
                        {cmd.allowed_roles.length > 0 && <div>ролей: {cmd.allowed_roles.length}</div>}
                        {cmd.allowed_channels.length > 0 && <div>каналов: {cmd.allowed_channels.length}</div>}
                        {cmd.send_dm && <div>в ЛС</div>}
                      </td>
                      <td>{cmd.uses}</td>
                      <td><Toggle checked={cmd.enabled} onChange={(v) => toggle(cmd, v)} label="Включена" /></td>
                      <td className="nowrap">
                        <button className="btn btn-sm" onClick={() => setEditing(cmd)}>Изменить</button>{" "}
                        <button className="btn btn-sm btn-danger" onClick={() => remove(cmd)}>Удалить</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
      {editing && <Editor guildId={guildId!} initial={editing === "new" ? null : editing} prefix={prefix} onClose={() => setEditing(null)} onSaved={reload} />}
      <SaveBar dirty={cfg.dirty} saving={cfg.saving} onSave={() => cfg.save()} onReset={cfg.reset} />
    </>
  );
}
