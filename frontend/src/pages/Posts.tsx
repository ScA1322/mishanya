import { useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, del, errorText, post, put } from "../api";
import { useApi } from "../hooks";
import { Icon } from "../icons";
import { Markdown, MentionCtx, type MentionResolver } from "../markdown";
import {
  Card, ChannelName, ChannelSelect, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Picker, ToggleRow, fmtDate, useConfirm,
  useResources, useToast,
} from "../ui";

interface PostField { name: string; value: string; inline: boolean }
interface PostEmbed {
  color: string; author_name: string; author_icon: string; author_url: string; title: string; url: string; description: string;
  fields: PostField[]; thumbnail: string; image: string; footer: string; footer_icon: string; timestamp: boolean;
}
interface Draft { name: string; channel_id: string | null; content: string; embeds: PostEmbed[]; attachments: string[]; ping: boolean }
interface Post extends Draft {
  id: number; published: boolean; message_url: string | null; published_channel_id: string | null; published_at: string | null; updated_at: string;
}

const MAX_IMAGE = 8 * 1024 * 1024;
const EMBED_LIMIT = 6000;
const EMPTY_EMBED: PostEmbed = {
  color: "#5865F2", author_name: "", author_icon: "", author_url: "", title: "", url: "", description: "", fields: [],
  thumbnail: "", image: "", footer: "", footer_icon: "", timestamp: false,
};
const COLORS = ["#5865F2", "#57F287", "#FEE75C", "#EB459E", "#ED4245", "#F47B67", "#3BA55C", "#00A8FC", "#9B59B6", "#2B2D31", "#FFFFFF"];

const embed = (e: Partial<PostEmbed>): PostEmbed => ({ ...EMPTY_EMBED, ...e, fields: e.fields ?? [] });
const blank = (name: string, d: Partial<Draft> = {}): Draft => ({ name, channel_id: null, content: "", embeds: [], attachments: [], ping: false, ...d });

/* ------------------------------------------------------------------ шаблоны оформления */

const PRESETS: { key: string; title: string; hint: string; icon: string; draft: () => Draft }[] = [
  { key: "blank", icon: "📝", title: "Пустой пост", hint: "Один блок — заполните как хотите", draft: () => blank("Новый пост", { embeds: [embed({ title: "Заголовок", description: "Текст поста" })] }) },
  { key: "announce", icon: "📢", title: "Объявление", hint: "Заголовок, текст, подпись сервера и время", draft: () => blank("Объявление", {
    embeds: [embed({ color: "#5865F2", title: "📢 Важное объявление", description: "Расскажите участникам, что произошло.\n\n**Что нового:**\n- первый пункт\n- второй пункт", footer: "{server}", timestamp: true })] }) },
  { key: "sponsor", icon: "💎", title: "Донат и спонсорство", hint: "Курсивный текст, роли-уровни, ссылки и баннер внизу", draft: () => blank("Спонсорство", {
    embeds: [embed({ color: "#2B2D31",
      description: "*Привет! Бонусы можно получить, участвуя и **побеждая в ивентах**, предлагая идеи и проявляя активность в нашем Discord. Если хочешь поддержать проект — смотри уровни ниже.*\n\n💎 роль 1 — выделите и нажмите «@ Роль»\n🥇 роль 2\n🥈 роль 3\n\n*Также через партнёров можно приобрести [Discord Nitro](https://discord.com/nitro).*",
      image: "" })] }) },
  { key: "rules", icon: "📜", title: "Правила сервера", hint: "Нумерованный список правил с акцентным цветом", draft: () => blank("Правила", {
    embeds: [embed({ color: "#ED4245", title: "📜 Правила сервера {server}",
      description: "**1.** Уважайте других участников.\n**2.** Без спама и флуда.\n**3.** Без рекламы без согласования.\n**4.** Контент 18+ запрещён.\n\n*Незнание правил не освобождает от ответственности.*",
      footer: "Администрация {server}" })] }) },
  { key: "fields", icon: "🗂️", title: "Карточка с полями", hint: "Ивент или турнир: поля «Дата», «Время», «Приз» в ряд", draft: () => blank("Ивент", {
    embeds: [embed({ color: "#FEE75C", title: "🏆 Турнир выходного дня", description: "Собирайте команду и записывайтесь!",
      fields: [{ name: "📅 Дата", value: "Суббота", inline: true }, { name: "⏰ Время", value: "18:00 МСК", inline: true }, { name: "🎁 Приз", value: "Nitro", inline: true }],
      timestamp: true })] }) },
  { key: "banner", icon: "🖼️", title: "Баннер + текст", hint: "Два блока одного цвета: сверху картинка, ниже текст", draft: () => blank("Баннер", {
    embeds: [embed({ color: "#EB459E", image: "" }), embed({ color: "#EB459E", title: "Заголовок", description: "Текст под баннером" })] }) },
  { key: "images", icon: "📎", title: "Только картинки", hint: "Без embed: изображения вложениями и подпись", draft: () => blank("Картинки", { content: "" }) },
];

/* ------------------------------------------------------------------ загрузка изображений */

function fileUrl(guildId: string, ref: string): string {
  return ref.startsWith("upload:") ? `/api/guilds/${guildId}/posts/files/${ref.slice(7)}` : ref;
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error("Не удалось прочитать файл"));
    r.readAsDataURL(file);
  });
}

function useUpload(guildId: string) {
  const toast = useToast();
  return async (file: File | undefined): Promise<string | null> => {
    if (!file) return null;
    if (file.size > MAX_IMAGE) { toast("err", "Изображение больше 8 МБ"); return null; }
    if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type)) { toast("err", "Поддерживаются PNG, JPEG, GIF и WebP"); return null; }
    try {
      const res = await post<{ ref: string }>(`/api/guilds/${guildId}/posts/files`, { data: await readFile(file) });
      return res.ref;
    } catch (e) { toast("err", errorText(e)); return null; }
  };
}

function ImageInput({ guildId, value, onChange, label, hint }: { guildId: string; value: string; onChange: (v: string) => void; label: string; hint?: string }) {
  const upload = useUpload(guildId);
  const [busy, setBusy] = useState(false);
  const [linkMode, setLinkMode] = useState(!!value && !value.startsWith("upload:"));
  return (
    <Field label={label} hint={hint}>
      <div className="img-input">
        {value && <img src={fileUrl(guildId, value)} alt="" />}
        <div className="stack" style={{ gap: 8, flex: 1, minWidth: 0 }}>
          <div className="row">
            <label className="btn btn-sm">
              <Icon name="upload" size={14} />{busy ? "Загрузка…" : value.startsWith("upload:") ? "Заменить" : "Загрузить файл"}
              <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden disabled={busy} onChange={async (e) => {
                const f = e.target.files?.[0]; e.target.value = "";
                setBusy(true); const ref = await upload(f); setBusy(false);
                if (ref) { onChange(ref); setLinkMode(false); }
              }} />
            </label>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setLinkMode((x) => !x)}>или ссылкой</button>
            {value && <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange("")}><Icon name="x" size={14} />Убрать</button>}
          </div>
          {linkMode && <input className="input" placeholder="https://…/картинка.png" value={value.startsWith("upload:") ? "" : value} maxLength={600}
            onChange={(e) => onChange(e.target.value.trim())} />}
        </div>
      </div>
    </Field>
  );
}

/* ------------------------------------------------------------------ поле с панелью форматирования */

function RichText({ value, onChange, rows = 5, maxLength, placeholder }: {
  value: string; onChange: (v: string) => void; rows?: number; maxLength: number; placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const { roles, channels } = useResources();
  const roleItems = useMemo(() => roles.filter((r) => !r.managed).map((r) => ({ id: r.id, label: r.name, color: r.color })), [roles]);
  const chanItems = useMemo(() => channels.map((c) => ({ id: c.id, label: c.name, group: c.category ?? null, icon: "hash" as const })), [channels]);

  const apply = (fn: (sel: string) => [string, number?]) => {
    const ta = ref.current;
    const s = ta?.selectionStart ?? value.length, e = ta?.selectionEnd ?? value.length;
    const [text, cursor] = fn(value.slice(s, e));
    const next = (value.slice(0, s) + text + value.slice(e)).slice(0, maxLength);
    onChange(next);
    requestAnimationFrame(() => {
      if (!ta) return;
      ta.focus();
      const pos = s + (cursor ?? text.length);
      ta.setSelectionRange(pos, pos);
    });
  };
  const wrap = (m: string, ph: string) => apply((sel) => [`${m}${sel || ph}${m}`, sel ? undefined : m.length + ph.length]);
  const insert = (t: string) => apply(() => [t]);

  return (
    <div className="rt">
      <div className="rt-toolbar">
        <button type="button" title="Жирный" onClick={() => wrap("**", "жирный")}><b>Ж</b></button>
        <button type="button" title="Курсив" onClick={() => wrap("*", "курсив")}><i>К</i></button>
        <button type="button" title="Подчёркнутый" onClick={() => wrap("__", "текст")}><u>П</u></button>
        <button type="button" title="Зачёркнутый" onClick={() => wrap("~~", "текст")}><s>З</s></button>
        <button type="button" title="Спойлер" onClick={() => wrap("||", "спойлер")}>▒</button>
        <button type="button" title="Код" onClick={() => wrap("`", "код")}>{"</>"}</button>
        <button type="button" title="Заголовок" onClick={() => apply((sel) => [`## ${sel || "Заголовок"}`])}>H</button>
        <button type="button" title="Цитата" onClick={() => apply((sel) => [`> ${sel || "цитата"}`])}>❝</button>
        <button type="button" title="Список" onClick={() => apply((sel) => [`- ${sel || "пункт"}`])}>•</button>
        <button type="button" title="Ссылка с текстом" onClick={() => apply((sel) => [`[${sel || "текст ссылки"}](https://)`])}><Icon name="external" size={13} /></button>
        <span className="rt-sep" />
        <Picker items={roleItems} value={null} trigger={<span className="rt-pick">@ Роль</span>} searchPlaceholder="Поиск роли…"
          emptyText="Ролей нет" onChange={(v) => v && insert(`<@&${v}>`)} />
        <Picker items={chanItems} value={null} trigger={<span className="rt-pick"># Канал</span>} searchPlaceholder="Поиск канала…"
          emptyText="Каналов нет" onChange={(v) => v && insert(`<#${v}>`)} />
        <span className="rt-count">{value.length}/{maxLength}</span>
      </div>
      <textarea ref={ref} className="textarea" rows={rows} value={value} maxLength={maxLength} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

/* ------------------------------------------------------------------ редактор блока embed */

function textLen(e: PostEmbed) {
  return e.author_name.length + e.title.length + e.description.length + e.footer.length + e.fields.reduce((n, f) => n + f.name.length + f.value.length, 0);
}

function EmbedEditor({ guildId, e, index, total, onChange, onMove, onRemove, onDuplicate }: {
  guildId: string; e: PostEmbed; index: number; total: number; onChange: (e: PostEmbed) => void;
  onMove: (d: number) => void; onRemove: () => void; onDuplicate: () => void;
}) {
  const [open, setOpen] = useState(true);
  const [more, setMore] = useState(!!(e.author_name || e.url || e.footer_icon || e.author_icon));
  const set = <K extends keyof PostEmbed>(k: K, v: PostEmbed[K]) => onChange({ ...e, [k]: v });
  const setField = (i: number, f: PostField) => set("fields", e.fields.map((x, j) => (j === i ? f : x)));
  return (
    <div className="embed-editor" style={{ borderLeftColor: e.color }}>
      <div className="menu-item-head">
        <button type="button" className="embed-editor-toggle" onClick={() => setOpen((x) => !x)}>
          <Icon name="chevronDown" size={16} className={open ? "" : "rot"} />
          <strong>Блок {index + 1}</strong>
          <span className="muted small">{e.title || e.description.slice(0, 40) || (e.image ? "изображение" : "пустой")}</span>
        </button>
        <span className="spacer" />
        <button className="icon-btn" title="Выше" disabled={index === 0} onClick={() => onMove(-1)}><Icon name="arrowUp" size={15} /></button>
        <button className="icon-btn" title="Ниже" disabled={index === total - 1} onClick={() => onMove(1)}><Icon name="chevronDown" size={15} /></button>
        <button className="icon-btn" title="Дублировать" disabled={total >= 10} onClick={onDuplicate}><Icon name="plus" size={15} /></button>
        <button className="icon-btn" title="Удалить блок" onClick={onRemove}><Icon name="trash" size={15} /></button>
      </div>
      {open && (
        <div className="stack">
          <Field label="Цвет полосы слева">
            <div className="color-row">
              {COLORS.map((c) => <button key={c} type="button" className={`swatch ${e.color.toUpperCase() === c ? "on" : ""}`} style={{ background: c }}
                title={c} onClick={() => set("color", c)} />)}
              <input type="color" value={e.color} onChange={(ev) => set("color", ev.target.value.toUpperCase())} title="Свой цвет" />
            </div>
          </Field>
          <Field label="Заголовок"><input className="input" value={e.title} maxLength={256} onChange={(ev) => set("title", ev.target.value)} /></Field>
          <Field label="Текст" hint="Поддерживается разметка Discord. Упоминания ролей в embed подсвечиваются, но никого не уведомляют.">
            <RichText value={e.description} maxLength={4096} rows={6} onChange={(v) => set("description", v)} />
          </Field>

          <div>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="field-label">Поля ({e.fields.length}/25)</span>
              {e.fields.length < 25 && <button className="btn btn-sm" onClick={() => set("fields", [...e.fields, { name: "Название", value: "Значение", inline: true }])}>
                <Icon name="plus" size={14} />Поле</button>}
            </div>
            {e.fields.map((f, i) => (
              <div className="field-row" key={i}>
                <input className="input" placeholder="Название" value={f.name} maxLength={256} onChange={(ev) => setField(i, { ...f, name: ev.target.value })} />
                <textarea className="textarea" rows={1} placeholder="Значение" value={f.value} maxLength={1024} onChange={(ev) => setField(i, { ...f, value: ev.target.value })} />
                <label className="small nowrap row" style={{ gap: 6 }}><input type="checkbox" checked={f.inline} onChange={(ev) => setField(i, { ...f, inline: ev.target.checked })} />в ряд</label>
                <button className="icon-btn" title="Удалить поле" onClick={() => set("fields", e.fields.filter((_, j) => j !== i))}><Icon name="x" size={15} /></button>
              </div>
            ))}
          </div>

          <div className="grid grid-2">
            <ImageInput guildId={guildId} label="Большая картинка" hint="Внизу блока, во всю ширину" value={e.image} onChange={(v) => set("image", v)} />
            <ImageInput guildId={guildId} label="Миниатюра" hint="Маленькая картинка справа сверху" value={e.thumbnail} onChange={(v) => set("thumbnail", v)} />
          </div>
          <div className="grid grid-2">
            <Field label="Подпись внизу (footer)"><input className="input" value={e.footer} maxLength={2048} onChange={(ev) => set("footer", ev.target.value)} /></Field>
            <div style={{ alignSelf: "end" }}><ToggleRow title="Показывать дату и время" checked={e.timestamp} onChange={(v) => set("timestamp", v)} /></div>
          </div>

          <button type="button" className="btn btn-sm btn-ghost" style={{ alignSelf: "flex-start" }} onClick={() => setMore((x) => !x)}>
            <Icon name="chevronDown" size={14} className={more ? "" : "rot"} />Автор, ссылка заголовка, иконки
          </button>
          {more && (
            <div className="stack">
              <div className="grid grid-2">
                <Field label="Автор (строка над заголовком)"><input className="input" value={e.author_name} maxLength={256} onChange={(ev) => set("author_name", ev.target.value)} /></Field>
                <Field label="Ссылка автора"><input className="input" placeholder="https://…" value={e.author_url} maxLength={512} onChange={(ev) => set("author_url", ev.target.value)} /></Field>
              </div>
              <Field label="Ссылка заголовка" hint="Заголовок станет кликабельным"><input className="input" placeholder="https://…" value={e.url} maxLength={512} onChange={(ev) => set("url", ev.target.value)} /></Field>
              <div className="grid grid-2">
                <ImageInput guildId={guildId} label="Иконка автора" value={e.author_icon} onChange={(v) => set("author_icon", v)} />
                <ImageInput guildId={guildId} label="Иконка подписи" value={e.footer_icon} onChange={(v) => set("footer_icon", v)} />
              </div>
            </div>
          )}
          <div className="small muted">Символов в блоке: {textLen(e)}</div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ предпросмотр как в Discord */

function useMentionResolver(): MentionResolver {
  const { roles, channels } = useResources();
  return (raw) => {
    let m = /^<@&(\d+)>$/.exec(raw);
    if (m) {
      const r = roles.find((x) => x.id === m![1]);
      return r ? { label: `@${r.name}`, color: r.color && r.color !== "#000000" ? r.color : null } : { label: "@удалённая роль" };
    }
    m = /^<#(\d+)>$/.exec(raw);
    if (m) {
      const c = channels.find((x) => x.id === m![1]);
      return { label: c ? `#${c.name}` : "#удалённый канал" };
    }
    return null;
  };
}

function PostPreview({ guildId, d }: { guildId: string; d: Draft }) {
  const resolver = useMentionResolver();
  const time = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const src = (ref: string) => fileUrl(guildId, ref);
  const empty = !d.content.trim() && d.embeds.length === 0 && d.attachments.length === 0;
  return (
    <MentionCtx.Provider value={resolver}>
      <div className="preview">
        {empty ? <div className="muted small">Пост пустой — добавьте текст, блок или картинку.</div> : (
          <div className="author">
            <div className="bot-avatar">M</div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div><span className="name">Mishanya</span><span className="bot-tag">БОТ</span><span className="pv-time">Сегодня, в {time}</span></div>
              {d.content && <div className="text"><Markdown text={d.content} /></div>}
              {d.embeds.map((e, i) => (
                <div key={i} className="pv-embed" style={{ borderLeftColor: e.color }}>
                  <div className="pv-embed-body">
                    {e.author_name && <div className="pv-author">{e.author_icon && <img src={src(e.author_icon)} alt="" />}{e.author_name}</div>}
                    {e.title && <div className={`pv-title ${e.url ? "link" : ""}`}><Markdown text={e.title} /></div>}
                    {e.description && <div className="pv-desc"><Markdown text={e.description} /></div>}
                    {e.fields.length > 0 && (
                      <div className="pv-fields">
                        {e.fields.map((f, j) => (
                          <div key={j} className={`pv-field ${f.inline ? "inline" : ""}`}>
                            <div className="pv-field-name"><Markdown text={f.name} /></div>
                            <div className="pv-field-value"><Markdown text={f.value} /></div>
                          </div>
                        ))}
                      </div>
                    )}
                    {e.image && <img className="pv-image" src={src(e.image)} alt="" />}
                    {(e.footer || e.timestamp) && (
                      <div className="pv-footer">{e.footer_icon && <img src={src(e.footer_icon)} alt="" />}
                        {e.footer}{e.footer && e.timestamp ? " • " : ""}{e.timestamp ? `Сегодня, в ${time}` : ""}</div>
                    )}
                  </div>
                  {e.thumbnail && <img className="pv-thumb" src={src(e.thumbnail)} alt="" />}
                </div>
              ))}
              {d.attachments.length > 0 && (
                <div className={`pv-attachments n${Math.min(d.attachments.length, 3)}`}>
                  {d.attachments.map((a) => <img key={a} src={src(a)} alt="" />)}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </MentionCtx.Provider>
  );
}

/* ------------------------------------------------------------------ окно редактирования */

function move<T>(list: T[], i: number, d: number): T[] {
  const j = i + d;
  if (j < 0 || j >= list.length) return list;
  const copy = [...list];
  [copy[i], copy[j]] = [copy[j], copy[i]];
  return copy;
}

function Editor({ guildId, initial, draft, onClose, onSaved }: {
  guildId: string; initial: Post | null; draft: Draft; onClose: () => void; onSaved: () => void;
}) {
  const toast = useToast();
  const upload = useUpload(guildId);
  const [d, setD] = useState<Draft>(draft);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [savedId, setSavedId] = useState<number | null>(initial?.id ?? null);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const setEmbed = (i: number, e: PostEmbed) => set("embeds", d.embeds.map((x, j) => (j === i ? e : x)));
  const total = d.embeds.reduce((n, e) => n + textLen(e), 0);
  const fileCount = new Set([...d.embeds.flatMap((e) => [e.image, e.thumbnail, e.author_icon, e.footer_icon]).filter((x) => x.startsWith("upload:")), ...d.attachments]).size;

  const addAttachments = async (files: FileList | null) => {
    if (!files) return;
    setUploading(true);
    const refs: string[] = [];
    for (const f of Array.from(files).slice(0, 10 - d.attachments.length)) {
      const ref = await upload(f);
      if (ref) refs.push(ref);
    }
    setUploading(false);
    setD((x) => ({ ...x, attachments: [...x.attachments, ...refs].slice(0, 10) }));
  };

  const save = async (publish: boolean) => {
    setBusy(true);
    setErrors([]);
    try {
      const p = savedId ? await put<Post>(`/api/guilds/${guildId}/posts/${savedId}`, d) : await post<Post>(`/api/guilds/${guildId}/posts`, d);
      setSavedId(p.id);
      if (publish) {
        await post(`/api/guilds/${guildId}/posts/${p.id}/publish`);
        toast("ok", p.published ? "Сообщение в Discord обновлено" : "Пост опубликован");
      } else toast("ok", "Пост сохранён");
      onSaved();
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.details.length) setErrors(e.details.map((x) => (x.loc ? `${x.loc}: ${x.msg}` : x.msg)));
      else setErrors([errorText(e)]);
      onSaved();
    } finally { setBusy(false); }
  };

  return (
    <Modal title={initial ? `Пост «${initial.name}»` : "Новый пост"} onClose={onClose} wide>
      <div className="post-editor">
        <div className="stack">
          <div className="grid grid-2">
            <Field label="Название" hint="Видно только в панели"><input className="input" value={d.name} maxLength={100} onChange={(e) => set("name", e.target.value)} /></Field>
            <Field label="Канал" hint="Куда опубликовать"><ChannelSelect value={d.channel_id} onChange={(v) => set("channel_id", v)} /></Field>
          </div>
          <Field label="Текст над блоками" hint="Обычный текст сообщения (необязательно)">
            <RichText value={d.content} maxLength={2000} rows={3} onChange={(v) => set("content", v)} />
          </Field>
          <ToggleRow title="Уведомлять упомянутых" hint="Если выключено, упоминания ролей и людей в тексте подсвечиваются, но никому не приходит уведомление"
            checked={d.ping} onChange={(v) => set("ping", v)} />

          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3>Блоки embed ({d.embeds.length}/10)</h3>
            <span className={`small ${total > EMBED_LIMIT ? "err-text" : "muted"}`}>текст всех блоков: {total}/{EMBED_LIMIT}</span>
          </div>
          {d.embeds.map((e, i) => (
            <EmbedEditor key={i} guildId={guildId} e={e} index={i} total={d.embeds.length} onChange={(ne) => setEmbed(i, ne)}
              onMove={(dir) => set("embeds", move(d.embeds, i, dir))} onRemove={() => set("embeds", d.embeds.filter((_, j) => j !== i))}
              onDuplicate={() => set("embeds", [...d.embeds.slice(0, i + 1), { ...e, fields: e.fields.map((f) => ({ ...f })) }, ...d.embeds.slice(i + 1)])} />
          ))}
          {d.embeds.length < 10 && (
            <button className="btn add-block" onClick={() => set("embeds", [...d.embeds, embed({ color: d.embeds.at(-1)?.color ?? "#5865F2" })])}>
              <Icon name="plus" size={16} />Добавить блок
            </button>
          )}

          <Field label={`Картинки-вложения (${d.attachments.length}/10)`} hint="Прикрепляются к сообщению отдельно от блоков, как обычные файлы">
            <div className="attach-grid">
              {d.attachments.map((a, i) => (
                <div key={a} className="attach-item">
                  <img src={fileUrl(guildId, a)} alt="" />
                  <div className="attach-actions">
                    <button title="Левее" disabled={i === 0} onClick={() => set("attachments", move(d.attachments, i, -1))}>‹</button>
                    <button title="Убрать" onClick={() => set("attachments", d.attachments.filter((x) => x !== a))}><Icon name="x" size={13} /></button>
                    <button title="Правее" disabled={i === d.attachments.length - 1} onClick={() => set("attachments", move(d.attachments, i, 1))}>›</button>
                  </div>
                </div>
              ))}
              {d.attachments.length < 10 && (
                <label className="attach-add">
                  <Icon name="upload" size={20} />{uploading ? "Загрузка…" : "Добавить"}
                  <input type="file" multiple accept="image/png,image/jpeg,image/gif,image/webp" hidden disabled={uploading}
                    onChange={(e) => { addAttachments(e.target.files); e.target.value = ""; }} />
                </label>
              )}
            </div>
          </Field>
          {fileCount > 10 && <div className="alert alert-err">Всего файлов {fileCount}: Discord принимает не больше 10 в одном сообщении.</div>}
        </div>

        <div className="post-preview-col">
          <span className="field-label">Предпросмотр</span>
          <PostPreview guildId={guildId} d={d} />
          <div className="small muted">Переменные: <code>{"{server}"}</code> — название сервера, <code>{"{member_count}"}</code> — участников.</div>
        </div>
      </div>
      {errors.map((e) => <div key={e} className="alert alert-err mt">{e}</div>)}
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>Отмена</button>
        <button className="btn" disabled={busy || uploading || !d.name.trim()} onClick={() => save(false)}>Сохранить черновик</button>
        <button className="btn btn-primary" disabled={busy || uploading || !d.name.trim() || !d.channel_id} title={!d.channel_id ? "Выберите канал" : undefined}
          onClick={() => save(true)}>
          <Icon name="send" size={15} />{busy ? "Отправка…" : initial?.published ? "Сохранить и обновить в Discord" : "Опубликовать"}
        </button>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ страница */

function toDraft(p: Post): Draft {
  return { name: p.name, channel_id: p.channel_id, content: p.content, embeds: p.embeds.map((e) => embed(e)), attachments: p.attachments, ping: p.ping };
}

export default function Posts() {
  const { guildId } = useParams();
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, loading, reload } = useApi<Post[]>(`/api/guilds/${guildId}/posts`, [guildId]);
  const [editing, setEditing] = useState<{ post: Post | null; draft: Draft } | null>(null);
  const [choosing, setChoosing] = useState(false);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); toast("ok", ok); reload(); } catch (e) { toast("err", errorText(e)); }
  };
  const remove = async (p: Post) => {
    if (!(await confirm({ title: `Удалить пост «${p.name}»?`, danger: true, confirmText: "Удалить",
      text: p.published ? "Сообщение в Discord тоже будет удалено." : undefined }))) return;
    act(() => del(`/api/guilds/${guildId}/posts/${p.id}`), "Пост удалён");
  };
  const unpublish = async (p: Post) => {
    if (!(await confirm({ title: "Убрать сообщение из Discord?", confirmText: "Убрать", text: "Пост останется в панели как черновик — его можно опубликовать снова." }))) return;
    act(() => post(`/api/guilds/${guildId}/posts/${p.id}/unpublish`), "Сообщение удалено из Discord");
  };

  return (
    <>
      <PageHeader title="Посты" subtitle="Красиво оформленные сообщения: текст, до 10 цветных блоков с картинками, полями и упоминаниями ролей, изображения-вложения. Публикуйте в любой канал и редактируйте уже отправленное."
        actions={<button className="btn btn-primary" onClick={() => setChoosing(true)}><Icon name="plus" size={16} />Новый пост</button>} />
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : data && data.length === 0 ? (
        <Card><Empty icon="🖼️">Постов пока нет. Нажмите «Новый пост» и выберите оформление: объявление, правила, спонсорство и другие.</Empty></Card>
      ) : data && (
        <div className="post-list">
          {data.map((p) => {
            const first = p.embeds[0];
            const img = first?.image || first?.thumbnail || p.attachments[0];
            return (
              <div key={p.id} className="card post-card" style={{ borderTopColor: first?.color ?? "var(--border)" }}>
                {img && <div className="post-card-img" style={{ backgroundImage: `url("${fileUrl(guildId!, img)}")` }} />}
                <div className="post-card-body">
                  <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
                    <strong className="post-card-title">{p.name}</strong>
                    {p.published ? <span className="badge badge-ok">в Discord</span> : <span className="badge">черновик</span>}
                  </div>
                  <div className="small muted">
                    {p.channel_id ? <ChannelName id={p.channel_id} /> : "канал не выбран"} · блоков: {p.embeds.length}{p.attachments.length ? ` · вложений: ${p.attachments.length}` : ""}
                  </div>
                  <div className="small muted">{p.published_at ? `опубликован ${fmtDate(p.published_at)}` : `изменён ${fmtDate(p.updated_at)}`}</div>
                  {p.published && p.published_channel_id !== p.channel_id && <div className="small warn-text">канал изменён — опубликуйте снова</div>}
                  <div className="row mt" style={{ gap: 8 }}>
                    <button className="btn btn-sm btn-primary" onClick={() => setEditing({ post: p, draft: toDraft(p) })}><Icon name="pencil" size={14} />Изменить</button>
                    <button className="btn btn-sm" disabled={!p.channel_id} onClick={() => act(() => post(`/api/guilds/${guildId}/posts/${p.id}/publish`), p.published ? "Сообщение обновлено" : "Пост опубликован")}>
                      <Icon name="send" size={14} />{p.published ? "Обновить" : "Опубликовать"}</button>
                    <button className="btn btn-sm" title="Создать копию" onClick={() => setEditing({ post: null, draft: { ...toDraft(p), name: `${p.name} (копия)` } })}>Копия</button>
                    {p.message_url && <a className="btn btn-sm" href={p.message_url} target="_blank" rel="noreferrer" title="Открыть в Discord"><Icon name="external" size={14} /></a>}
                    {p.published && <button className="btn btn-sm" onClick={() => unpublish(p)}>Снять</button>}
                    <button className="btn btn-sm btn-danger" onClick={() => remove(p)} title="Удалить"><Icon name="trash" size={14} /></button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {choosing && (
        <Modal title="Выберите оформление" onClose={() => setChoosing(false)} wide>
          <div className="preset-grid">
            {PRESETS.map((p) => (
              <button key={p.key} className="preset" onClick={() => { setChoosing(false); setEditing({ post: null, draft: p.draft() }); }}>
                <span className="preset-icon">{p.icon}</span>
                <strong>{p.title}</strong>
                <span className="small muted">{p.hint}</span>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {editing && <Editor guildId={guildId!} initial={editing.post} draft={editing.draft} onClose={() => setEditing(null)} onSaved={reload} />}
    </>
  );
}
