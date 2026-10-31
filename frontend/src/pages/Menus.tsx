import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, del, errorText, post, put } from "../api";
import { useMeta } from "../App";
import { useApi, usePreview } from "../hooks";
import { Icon } from "../icons";
import type { EmbedConfig } from "../types";
import {
  Card, ChannelName, ChannelSelect, DiscordPreview, EmbedEditor, Empty, ErrorBox, Field, Loading, Modal, NumberInput, PageHeader,
  RoleSelect, TemplateInput, ToggleRow, fmtDate, useConfirm, useResources, useToast,
} from "../ui";

type Action = "reply" | "role" | "ticket" | "link";
type Style = "primary" | "secondary" | "success" | "danger" | "link";
interface MenuButton { label: string; emoji: string; style: Style; action: Action; url: string; role_id: string | null; reply: string; row: number | null }
interface RoleOption { role_id: string; label: string; description: string; emoji: string }
interface RoleSelectCfg { enabled: boolean; placeholder: string; options: RoleOption[]; min_values: number; max_values: number }
interface Menu {
  id: number; name: string; channel_id: string | null; content: string; embed: EmbedConfig | null; buttons: MenuButton[];
  role_select: RoleSelectCfg | null; image_url: string | null; published: boolean; message_url: string | null;
  published_channel_id: string | null; published_at: string | null; updated_at: string;
}
interface Draft { name: string; channel_id: string | null; content: string; embed: EmbedConfig; buttons: MenuButton[]; role_select: RoleSelectCfg }

const EMPTY_EMBED: EmbedConfig = { enabled: false, title: "", description: "", color: "#5865F2", footer: "", image_url: "", show_avatar: false };
const EMPTY_SELECT: RoleSelectCfg = { enabled: false, placeholder: "Выберите роль", options: [], min_values: 0, max_values: 1 };
const NEW_BUTTON: MenuButton = { label: "Кнопка", emoji: "", style: "primary", action: "reply", url: "", role_id: null, reply: "", row: null };
const VARS = ["server", "member_count", "choose"];
const REPLY_VARS = ["user", "username", "display_name", "server", "member_count", "choose"];
const MAX_IMAGE = 8 * 1024 * 1024;

const ACTIONS: [Action, string][] = [["reply", "Ответить сообщением (видно только нажавшему)"], ["role", "Выдать / снять роль"],
  ["ticket", "Открыть тикет"], ["link", "Открыть ссылку"]];
const STYLES: [Style, string][] = [["primary", "Синяя"], ["secondary", "Серая"], ["success", "Зелёная"], ["danger", "Красная"]];

const PRESETS: { key: string; title: string; hint: string; draft: () => Draft }[] = [
  { key: "blank", title: "Пустое меню", hint: "Текст, изображение и кнопки на ваш выбор", draft: () => ({
    name: "Новое меню", channel_id: null, content: "", embed: { ...EMPTY_EMBED, enabled: true, title: "Заголовок" }, buttons: [], role_select: { ...EMPTY_SELECT } }) },
  { key: "roles", title: "Выбор ролей для новичков", hint: "Выпадающий список: участник сам выбирает себе роль", draft: () => ({
    name: "Выбор ролей", channel_id: null, content: "",
    embed: { ...EMPTY_EMBED, enabled: true, title: "Добро пожаловать на {server}!", description: "Выберите роль в списке ниже — её можно сменить в любой момент." },
    buttons: [], role_select: { ...EMPTY_SELECT, enabled: true } }) },
  { key: "tickets", title: "Поддержка (тикеты)", hint: "Кнопка, открывающая приватный тикет", draft: () => ({
    name: "Поддержка", channel_id: null, content: "",
    embed: { ...EMPTY_EMBED, enabled: true, title: "Нужна помощь?", description: "Нажмите кнопку ниже — откроется приватный канал с командой поддержки." },
    buttons: [{ ...NEW_BUTTON, label: "Открыть тикет", emoji: "🎫", action: "ticket", style: "success" }], role_select: { ...EMPTY_SELECT } }) },
];

function toDraft(m: Menu): Draft {
  return { name: m.name, channel_id: m.channel_id, content: m.content, embed: m.embed ?? EMPTY_EMBED, buttons: m.buttons,
    role_select: m.role_select ?? { ...EMPTY_SELECT } };
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error("Не удалось прочитать файл"));
    r.readAsDataURL(file);
  });
}

function move<T>(list: T[], i: number, d: number): T[] {
  const j = i + d;
  if (j < 0 || j >= list.length) return list;
  const copy = [...list];
  [copy[i], copy[j]] = [copy[j], copy[i]];
  return copy;
}

/* ------------------------------------------------------------------ редакторы частей */

function ButtonEditor({ b, index, total, selectOn, onChange, onMove, onRemove }: {
  b: MenuButton; index: number; total: number; selectOn: boolean; onChange: (b: MenuButton) => void; onMove: (d: number) => void; onRemove: () => void;
}) {
  const replyVars = useVars(REPLY_VARS);
  const set = <K extends keyof MenuButton>(k: K, v: MenuButton[K]) => onChange({ ...b, [k]: v });
  const rows = selectOn ? [1, 2, 3, 4] : [0, 1, 2, 3, 4];
  return (
    <div className="menu-item">
      <div className="menu-item-head">
        <strong>Кнопка {index + 1}</strong>
        <span className="spacer" />
        <button className="icon-btn" disabled={index === 0} onClick={() => onMove(-1)} aria-label="Выше"><Icon name="arrowUp" size={15} /></button>
        <button className="icon-btn" disabled={index === total - 1} onClick={() => onMove(1)} aria-label="Ниже"><Icon name="chevronDown" size={15} /></button>
        <button className="icon-btn" onClick={onRemove} aria-label="Удалить кнопку"><Icon name="trash" size={15} /></button>
      </div>
      <div className="grid grid-3">
        <Field label="Подпись"><input className="input" value={b.label} maxLength={80} onChange={(e) => set("label", e.target.value)} /></Field>
        <Field label="Эмодзи" hint="🎮 или <:имя:id>"><input className="input" value={b.emoji} maxLength={64} onChange={(e) => set("emoji", e.target.value)} /></Field>
        <Field label="Ряд">
          <select className="input" value={b.row ?? ""} onChange={(e) => set("row", e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">Автоматически</option>
            {rows.map((r) => <option key={r} value={r}>{r + 1}</option>)}
          </select>
        </Field>
      </div>
      <div className="grid grid-2 mt">
        <Field label="Действие">
          <select className="input" value={b.action} onChange={(e) => {
            const action = e.target.value as Action;
            onChange({ ...b, action, style: action === "link" ? "link" : b.style === "link" ? "primary" : b.style });
          }}>
            {ACTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        {b.action !== "link" && (
          <Field label="Цвет">
            <select className="input" value={b.style} onChange={(e) => set("style", e.target.value as Style)}>
              {STYLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
        )}
      </div>
      <div className="mt">
        {b.action === "link" && <Field label="Ссылка"><input className="input" value={b.url} placeholder="https://…" maxLength={512} onChange={(e) => set("url", e.target.value)} /></Field>}
        {b.action === "role" && <Field label="Роль" hint="Повторное нажатие снимает роль"><RoleSelect value={b.role_id} assignableOnly onChange={(v) => set("role_id", v)} /></Field>}
        {b.action === "reply" && <Field label="Текст ответа"><TemplateInput value={b.reply} rows={2} vars={replyVars} onChange={(v) => set("reply", v)} /></Field>}
        {b.action === "ticket" && <div className="small muted">Откроет тикет по настройкам раздела «Тикеты» (раздел должен быть включён).</div>}
      </div>
    </div>
  );
}

function useVars(keys: string[]) {
  const meta = useMeta();
  return Object.fromEntries(Object.entries(meta.template_variables).filter(([k]) => keys.includes(k)));
}

function RoleSelectEditor({ value, onChange }: { value: RoleSelectCfg; onChange: (v: RoleSelectCfg) => void }) {
  const { roles } = useResources();
  const set = <K extends keyof RoleSelectCfg>(k: K, v: RoleSelectCfg[K]) => onChange({ ...value, [k]: v });
  const setOpt = (i: number, o: RoleOption) => set("options", value.options.map((x, j) => (j === i ? o : x)));
  return (
    <div className="stack">
      <ToggleRow title="Выпадающий список ролей" hint="Участник открывает список под сообщением и выбирает себе роль. Повторный выбор меняет роль."
        checked={value.enabled} onChange={(v) => set("enabled", v)} />
      {value.enabled && (
        <>
          <div className="grid grid-3">
            <Field label="Подсказка в списке"><input className="input" value={value.placeholder} maxLength={150} onChange={(e) => set("placeholder", e.target.value)} /></Field>
            <Field label="Можно выбрать ролей, максимум" hint="1 — только одна роль из списка">
              <NumberInput value={value.max_values} min={1} max={Math.max(1, value.options.length)} onChange={(v) => set("max_values", v)} />
            </Field>
            <Field label="Минимум" hint="0 — можно снять все роли"><NumberInput value={value.min_values} min={0} max={value.max_values} onChange={(v) => set("min_values", v)} /></Field>
          </div>
          {value.options.map((o, i) => (
            <div className="menu-item" key={i}>
              <div className="menu-item-head">
                <strong>Вариант {i + 1}</strong><span className="spacer" />
                <button className="icon-btn" disabled={i === 0} onClick={() => set("options", move(value.options, i, -1))} aria-label="Выше"><Icon name="arrowUp" size={15} /></button>
                <button className="icon-btn" disabled={i === value.options.length - 1} onClick={() => set("options", move(value.options, i, 1))} aria-label="Ниже"><Icon name="chevronDown" size={15} /></button>
                <button className="icon-btn" onClick={() => set("options", value.options.filter((_, j) => j !== i))} aria-label="Удалить"><Icon name="trash" size={15} /></button>
              </div>
              <div className="grid grid-2">
                <Field label="Роль"><RoleSelect value={o.role_id || null} assignableOnly onChange={(v) => {
                  const role = roles.find((r) => r.id === v);
                  setOpt(i, { ...o, role_id: v ?? "", label: !o.label || roles.some((r) => r.name === o.label) ? role?.name ?? o.label : o.label });
                }} /></Field>
                <Field label="Название в списке"><input className="input" value={o.label} maxLength={100} onChange={(e) => setOpt(i, { ...o, label: e.target.value })} /></Field>
                <Field label="Описание" hint="Необязательно"><input className="input" value={o.description} maxLength={100} onChange={(e) => setOpt(i, { ...o, description: e.target.value })} /></Field>
                <Field label="Эмодзи" hint="Необязательно"><input className="input" value={o.emoji} maxLength={64} onChange={(e) => setOpt(i, { ...o, emoji: e.target.value })} /></Field>
              </div>
            </div>
          ))}
          {value.options.length < 25 && (
            <div><button className="btn btn-sm" onClick={() => set("options", [...value.options, { role_id: "", label: "", description: "", emoji: "" }])}>
              <Icon name="plus" size={14} />Добавить роль в список</button></div>
          )}
        </>
      )}
    </div>
  );
}

function ComponentsPreview({ d, image }: { d: Draft; image: string | null }) {
  const autoRows: MenuButton[][] = [];
  const fixed: Record<number, MenuButton[]> = {};
  for (const b of d.buttons) {
    if (b.row !== null) (fixed[b.row] ??= []).push(b);
  }
  let cur: MenuButton[] = [];
  for (const b of d.buttons.filter((x) => x.row === null)) {
    if (cur.length === 5) { autoRows.push(cur); cur = []; }
    cur.push(b);
  }
  if (cur.length) autoRows.push(cur);
  const rows = [...Object.keys(fixed).sort().map((k) => fixed[Number(k)]), ...autoRows];
  return (
    <>
      {image && !d.embed.enabled && <img className="dc-image" src={image} alt="" />}
      {(d.role_select.enabled || rows.length > 0) && (
        <div className="dc-components">
          {d.role_select.enabled && <div className="dc-select"><span>{d.role_select.placeholder || "Выберите…"}</span><Icon name="chevronDown" size={16} /></div>}
          {rows.map((r, i) => (
            <div className="dc-row" key={i}>
              {r.map((b, j) => (
                <span key={j} className={`dc-btn ${b.style}`}>{b.emoji && !b.emoji.startsWith("<") && <span>{b.emoji}</span>}{b.label}
                  {b.action === "link" && <Icon name="external" size={13} />}</span>
              ))}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ окно редактирования */

function Editor({ guildId, initial, preset, onClose, onSaved }: {
  guildId: string; initial: Menu | null; preset: Draft | null; onClose: () => void; onSaved: () => void;
}) {
  const toast = useToast();
  const [d, setD] = useState<Draft>(() => (initial ? toDraft(initial) : preset!));
  const [image, setImage] = useState<string | null>(initial?.image_url ?? null);
  const [imageChanged, setImageChanged] = useState<"none" | "new" | "removed">("none");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [createdId, setCreatedId] = useState<number | null>(null); // меню уже создано, но публикация не удалась
  const vars = useVars(VARS);
  const preview = usePreview(guildId, d.content, d.embed.enabled ? { ...d.embed, show_avatar: false } : null);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const maxButtons = d.role_select.enabled ? 20 : 25;

  const pickImage = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_IMAGE) { toast("err", "Изображение больше 8 МБ"); return; }
    if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type)) { toast("err", "Поддерживаются PNG, JPEG, GIF и WebP"); return; }
    try { setImage(await readFile(file)); setImageChanged("new"); } catch (e) { toast("err", errorText(e)); }
  };

  const save = async (publish: boolean) => {
    setBusy(true);
    setErrors([]);
    try {
      const body = { ...d, embed: { ...d.embed, show_avatar: false } };
      const id = initial?.id ?? createdId;
      let menu = id ? await put<Menu>(`/api/guilds/${guildId}/menus/${id}`, body)
        : await post<Menu>(`/api/guilds/${guildId}/menus`, body);
      setCreatedId(menu.id);
      if (imageChanged === "new" && image) menu = await put<Menu>(`/api/guilds/${guildId}/menus/${menu.id}/image`, { data: image });
      if (imageChanged === "removed") menu = await del<Menu>(`/api/guilds/${guildId}/menus/${menu.id}/image`);
      setImage(menu.image_url);
      setImageChanged("none");
      if (publish) {
        await post(`/api/guilds/${guildId}/menus/${menu.id}/publish`);
        toast("ok", menu.published ? "Сообщение в Discord обновлено" : "Меню опубликовано");
      } else {
        toast("ok", "Меню сохранено");
      }
      onSaved();
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.details.length) setErrors(e.details.map((x) => (x.loc ? `${x.loc}: ${x.msg}` : x.msg)));
      else setErrors([errorText(e)]);
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  const previewData = preview.data && preview.data.embed && image && imageChanged !== "removed"
    ? { ...preview.data, embed: { ...preview.data.embed, image_url: image } } : preview.data;

  return (
    <Modal title={initial ? `Меню «${initial.name}»` : "Новое меню"} onClose={onClose} wide>
      <div className="stack">
        <div className="grid grid-2">
          <Field label="Название" hint="Видно только в панели"><input className="input" value={d.name} maxLength={100} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Канал" hint="Куда бот отправит сообщение"><ChannelSelect value={d.channel_id} onChange={(v) => set("channel_id", v)} /></Field>
        </div>
        <div className="grid grid-2">
          <div className="stack">
            <Field label="Текст сообщения"><TemplateInput value={d.content} vars={vars} rows={3} onChange={(v) => set("content", v)} /></Field>
            <EmbedEditor value={d.embed} vars={vars} onChange={(e) => set("embed", e)} />
            <Field label="Изображение" hint={d.embed.enabled ? "Показывается внутри embed (заменяет ссылку на изображение)" : "Прикрепляется к сообщению"}>
              <div className="image-drop">
                {image && imageChanged !== "removed" && <img src={image} alt="" />}
                <label className="btn btn-sm">
                  <Icon name="upload" size={14} />{image && imageChanged !== "removed" ? "Заменить" : "Загрузить"}
                  <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={(e) => { pickImage(e.target.files?.[0]); e.target.value = ""; }} />
                </label>
                {image && imageChanged !== "removed" && (
                  <button className="btn btn-sm" onClick={() => { if (initial?.image_url) { setImageChanged("removed"); } else { setImage(null); setImageChanged("none"); } }}>
                    <Icon name="x" size={14} />Убрать
                  </button>
                )}
              </div>
            </Field>
          </div>
          <div className="field">
            <span className="field-label">Предпросмотр</span>
            <DiscordPreview data={previewData} loading={preview.loading}>
              <ComponentsPreview d={d} image={imageChanged === "removed" ? null : image} />
            </DiscordPreview>
          </div>
        </div>

        <Card title="Выпадающий список ролей"><RoleSelectEditor value={d.role_select} onChange={(v) => set("role_select", v)} /></Card>

        <Card title={`Кнопки (${d.buttons.length}/${maxButtons})`}
          actions={d.buttons.length < maxButtons && <button className="btn btn-sm" onClick={() => set("buttons", [...d.buttons, { ...NEW_BUTTON }])}><Icon name="plus" size={14} />Добавить кнопку</button>}>
          {d.buttons.length === 0 ? <div className="small muted">Кнопок нет. Можно добавить до {maxButtons}: ответ, выдача роли, тикет или ссылка.</div> : (
            d.buttons.map((b, i) => (
              <ButtonEditor key={i} b={b} index={i} total={d.buttons.length} selectOn={d.role_select.enabled}
                onChange={(nb) => set("buttons", d.buttons.map((x, j) => (j === i ? nb : x)))}
                onMove={(dir) => set("buttons", move(d.buttons, i, dir))}
                onRemove={() => set("buttons", d.buttons.filter((_, j) => j !== i))} />
            ))
          )}
        </Card>
        {errors.map((e) => <div key={e} className="alert alert-err">{e}</div>)}
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>Отмена</button>
        <button className="btn" disabled={busy || !d.name.trim()} onClick={() => save(false)}>Сохранить</button>
        <button className="btn btn-primary" disabled={busy || !d.name.trim() || !d.channel_id} onClick={() => save(true)}>
          <Icon name="send" size={14} />{busy ? "Отправка…" : initial?.published ? "Сохранить и обновить в Discord" : "Сохранить и опубликовать"}
        </button>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ страница */

export default function Menus() {
  const { guildId } = useParams();
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, loading, reload } = useApi<Menu[]>(`/api/guilds/${guildId}/menus`, [guildId]);
  const [editing, setEditing] = useState<{ menu: Menu | null; preset: Draft | null } | null>(null);
  const [choosing, setChoosing] = useState(false);

  const publish = async (m: Menu) => {
    try { await post(`/api/guilds/${guildId}/menus/${m.id}/publish`); toast("ok", m.published ? "Сообщение обновлено" : "Меню опубликовано"); reload(); }
    catch (e) { toast("err", errorText(e)); }
  };
  const remove = async (m: Menu) => {
    if (!(await confirm({ title: `Удалить меню «${m.name}»?`, danger: true, confirmText: "Удалить",
      text: m.published ? "Опубликованное сообщение тоже будет удалено из Discord." : undefined }))) return;
    try { await del(`/api/guilds/${guildId}/menus/${m.id}`); toast("ok", "Меню удалено"); reload(); } catch (e) { toast("err", errorText(e)); }
  };

  return (
    <>
      <PageHeader title="Меню с кнопками" subtitle="Сообщения с изображением, кнопками и выпадающим списком ролей. Кнопки работают и после перезапуска бота."
        actions={<button className="btn btn-primary" onClick={() => setChoosing(true)}><Icon name="plus" size={15} />Новое меню</button>} />
      <Card>
        {error && <ErrorBox error={error} onRetry={reload} />}
        {loading && !data ? <Loading /> : data && data.length === 0 ? <Empty icon="🧩">Меню пока нет — создайте первое, например выбор ролей для новичков</Empty> : data && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Меню</th><th>Канал</th><th>Содержимое</th><th>Статус</th><th></th></tr></thead>
              <tbody>
                {data.map((m) => (
                  <tr key={m.id}>
                    <td><strong>{m.name}</strong></td>
                    <td className="small">{m.channel_id ? <ChannelName id={m.channel_id} /> : <span className="muted">не выбран</span>}</td>
                    <td className="small muted">
                      {m.buttons.length > 0 && <div>кнопок: {m.buttons.length}</div>}
                      {m.role_select && <div>ролей в списке: {m.role_select.options.length}</div>}
                      {m.image_url && <div>с изображением</div>}
                    </td>
                    <td className="small">
                      {m.published ? <span className="badge badge-ok">опубликовано</span> : <span className="badge">черновик</span>}
                      {m.published_at && <div className="muted">{fmtDate(m.published_at)}</div>}
                      {m.published && m.published_channel_id !== m.channel_id && <div className="muted">канал изменён — опубликуйте снова</div>}
                    </td>
                    <td className="nowrap">
                      <button className="btn btn-sm" onClick={() => setEditing({ menu: m, preset: null })}>Изменить</button>{" "}
                      <button className="btn btn-sm" disabled={!m.channel_id} onClick={() => publish(m)}>{m.published ? "Обновить" : "Опубликовать"}</button>{" "}
                      {m.message_url && <a className="btn btn-sm" href={m.message_url} target="_blank" rel="noreferrer"><Icon name="external" size={13} /></a>}{" "}
                      <button className="btn btn-sm btn-danger" onClick={() => remove(m)}>Удалить</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {choosing && (
        <Modal title="Новое меню" onClose={() => setChoosing(false)}>
          <div className="stack">
            {PRESETS.map((p) => (
              <button key={p.key} className="menu-item" style={{ textAlign: "left", cursor: "pointer", width: "100%" }}
                onClick={() => { setChoosing(false); setEditing({ menu: null, preset: p.draft() }); }}>
                <strong>{p.title}</strong><div className="small muted">{p.hint}</div>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {editing && <Editor guildId={guildId!} initial={editing.menu} preset={editing.preset} onClose={() => setEditing(null)} onSaved={reload} />}
    </>
  );
}
