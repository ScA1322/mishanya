import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./icons";
import { Markdown } from "./markdown";
import type { DiscordResources, EmbedConfig, Role } from "./types";

/* ------------------------------------------------------------------ уведомления */

type Toast = { id: number; kind: "ok" | "err" | "info"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "err" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <Icon name={t.kind === "ok" ? "checkCircle" : t.kind === "err" ? "alert" : "info"} size={18} className="toast-icon" />
            <span className="toast-text">{t.text}</span>
            <button className="toast-close" aria-label="Закрыть" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/* ------------------------------------------------------------------ подтверждение */

type ConfirmOpts = { title: string; text?: ReactNode; confirmText?: string; danger?: boolean; typeToConfirm?: string };
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const [typed, setTyped] = useState("");
  const confirm = useCallback(
    (o: ConfirmOpts) =>
      new Promise<boolean>((resolve) => {
        setTyped("");
        setState({ ...o, resolve });
      }),
    [],
  );
  const close = (v: boolean) => {
    state?.resolve(v);
    setState(null);
  };
  const blocked = !!state?.typeToConfirm && typed !== state.typeToConfirm;
  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      {state && (
        <div className="modal-backdrop" onClick={() => close(false)}>
          <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h2>{state.title}</h2>
            {state.text && <div className="muted">{state.text}</div>}
            {state.typeToConfirm && (
              <div className="field mt">
                <span className="field-label">Для подтверждения введите «{state.typeToConfirm}»</span>
                <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
              </div>
            )}
            <div className="modal-actions">
              <button className="btn" onClick={() => close(false)}>Отмена</button>
              <button
                className={`btn ${state.danger ? "btn-danger-solid" : "btn-primary"}`}
                disabled={blocked}
                onClick={() => close(true)}
              >
                {state.confirmText ?? "Подтвердить"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}

export const useConfirm = () => useContext(ConfirmCtx);

/* ------------------------------------------------------------------ базовые элементы */

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} — Mishanya`;
  }, [title]);
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Card({ title, subtitle, actions, children, className = "" }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Loading({ text = "Загрузка…" }: { text?: string }) {
  return (
    <div className="loading"><div className="spinner" />{text}</div>
  );
}

export function Empty({ icon = "📭", children }: { icon?: string; children: ReactNode }) {
  return (
    <div className="empty"><div className="big">{icon}</div>{children}</div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="alert alert-err row">
      <Icon name="alert" size={16} />
      <span>{error}</span>
      <span className="spacer" />
      {onRetry && <button className="btn btn-sm" onClick={onRetry}>Повторить</button>}
    </div>
  );
}

export function Toggle({ checked, onChange, disabled, label }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string;
}) {
  return (
    <label className="toggle" title={label}>
      <input type="checkbox" checked={checked} disabled={disabled} aria-label={label}
        onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
    </label>
  );
}

export function ToggleRow({ title, hint, checked, onChange, disabled }: {
  title: ReactNode; hint?: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <div className="toggle-row">
      <div className="text">
        <div style={{ fontWeight: 560 }}>{title}</div>
        {hint && <div className="field-hint">{hint}</div>}
      </div>
      <Toggle checked={checked} onChange={onChange} disabled={disabled} label={typeof title === "string" ? title : undefined} />
    </div>
  );
}

export function Field({ label, hint, error, children }: {
  label: ReactNode; hint?: ReactNode; error?: string; children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </label>
  );
}

export function NumberInput({ value, onChange, min, max, step = 1, className = "input" }: {
  value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; className?: string;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <input
      className={className}
      type="number"
      inputMode="decimal"
      value={text}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value !== "" && Number.isFinite(n)) onChange(n);
      }}
      onBlur={() => {
        let n = Number(text);
        if (!Number.isFinite(n) || text === "") n = value;
        if (min !== undefined) n = Math.max(min, n);
        if (max !== undefined) n = Math.min(max, n);
        setText(String(n));
        onChange(n);
      }}
    />
  );
}

/* ------------------------------------------------------------------ каналы и роли */

export const ResourcesCtx = createContext<DiscordResources>({
  connected: false, channels: [], categories: [], roles: [], bot_top_position: null,
});
export const useResources = () => useContext(ResourcesCtx);

/* ------------------------------------------------------------------ выбор из списка с поиском */

export type PickerItem = {
  id: string; label: string; group?: string | null; color?: string | null; disabled?: boolean; hint?: string; icon?: IconName;
};

function ItemLabel({ item }: { item: PickerItem }) {
  return (
    <span className="picker-label">
      {item.color !== undefined && <span className="role-dot" style={{ background: item.color ?? "var(--muted)" }} />}
      {item.icon && <Icon name={item.icon} size={14} className="muted" />}
      <span className="picker-text">{item.label}</span>
      {item.hint && <span className="picker-hint">{item.hint}</span>}
    </span>
  );
}

/**
 * Выпадающий список с поиском и клавиатурой (↑ ↓ Enter Esc). Позиционируется fixed,
 * поэтому не обрезается контейнерами с прокруткой (например, таблицами).
 */
export function Picker({ items, value, onChange, placeholder = "— не выбрано —", clearLabel, trigger, searchPlaceholder = "Поиск…", emptyText = "Ничего не найдено", missingLabel = "Объект" }: {
  items: PickerItem[]; value: string | null; onChange: (v: string | null) => void; placeholder?: string;
  clearLabel?: string; trigger?: ReactNode; searchPlaceholder?: string; emptyText?: string; missingLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number; width: number; maxHeight: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? items.filter((i) => i.label.toLowerCase().includes(q) || i.id === q || (i.group ?? "").toLowerCase().includes(q)) : items;
    return clearLabel && !q ? [{ id: "", label: clearLabel } as PickerItem, ...list] : list;
  }, [items, query, clearLabel]);

  const place = useCallback(() => {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(Math.max(r.width, 280), window.innerWidth - 16);
    const left = Math.min(Math.max(8, r.left), window.innerWidth - width - 8);
    const below = window.innerHeight - r.bottom - 12;
    const above = r.top - 12;
    if (below >= 260 || below >= above) setPos({ left, top: r.bottom + 4, width, maxHeight: Math.min(360, below) });
    else setPos({ left, bottom: window.innerHeight - r.top + 4, width, maxHeight: Math.min(360, above) });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!popRef.current?.contains(t) && !btnRef.current?.contains(t)) setOpen(false);
    };
    const onScroll = (e: Event) => {
      if (!popRef.current?.contains(e.target as Node)) place();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const openMenu = () => {
    setQuery("");
    const idx = filtered.findIndex((i) => i.id === (value ?? ""));
    setActive(Math.max(0, idx));
    setOpen(true);
  };
  const choose = (item: PickerItem | undefined) => {
    if (!item || item.disabled) return;
    onChange(item.id || null);
    setOpen(false);
    btnRef.current?.focus();
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(filtered.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(filtered[active]); }
    else if (e.key === "Escape") { e.preventDefault(); setOpen(false); btnRef.current?.focus(); }
    else if (e.key === "Tab") setOpen(false);
  };

  const selected = items.find((i) => i.id === value);
  let lastGroup: string | null | undefined;
  return (
    <>
      <button ref={btnRef} type="button" className={trigger ? "picker-add" : "select picker-trigger"} aria-haspopup="listbox" aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={(e) => { if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) { e.preventDefault(); openMenu(); } }}>
        {trigger ?? (
          <>
            {selected ? <ItemLabel item={selected} />
              : value ? <span className="picker-text warn-text"><Icon name="alert" size={14} /> {missingLabel} {value} не найден</span>
                : <span className="picker-text muted">{placeholder}</span>}
            <Icon name="chevronsUpDown" size={14} className="muted picker-chevron" />
          </>
        )}
      </button>
      {open && pos && (
        <div ref={popRef} className="picker-pop" style={{ left: pos.left, top: pos.top, bottom: pos.bottom, width: pos.width }} onKeyDown={onKey}>
          <div className="picker-search">
            <Icon name="search" size={14} className="muted" />
            <input autoFocus value={query} placeholder={searchPlaceholder} aria-label={searchPlaceholder}
              onChange={(e) => { setQuery(e.target.value); setActive(0); }} />
          </div>
          <div ref={listRef} className="picker-list" role="listbox" style={{ maxHeight: Math.max(120, pos.maxHeight - 48) }}>
            {filtered.length === 0 && <div className="picker-empty">{emptyText}</div>}
            {filtered.map((item, i) => {
              const grouped = item.id !== "" && item.group !== undefined;
              const header = grouped && item.group !== lastGroup;
              if (grouped) lastGroup = item.group;
              return (
                <div key={item.id || "__clear"}>
                  {header && <div className="picker-group">{item.group ?? "Без категории"}</div>}
                  <div role="option" aria-selected={item.id === (value ?? "")} aria-disabled={item.disabled} data-index={i}
                    className={`picker-option ${i === active ? "active" : ""} ${item.disabled ? "disabled" : ""}`}
                    onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(item)}>
                    {item.id === "" ? <span className="muted">{item.label}</span> : <ItemLabel item={item} />}
                    {item.id !== "" && item.id === value && <Icon name="check" size={14} className="picker-check" />}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ каналы и роли */

function useChannelItems(textOnly = true): PickerItem[] {
  const { channels } = useResources();
  return useMemo(() => channels.filter((c) => !textOnly || c.text).map((c) => ({
    id: c.id, label: c.name, group: c.category ?? null, icon: "hash" as IconName, hint: c.can_send ? undefined : "нет прав",
  })), [channels, textOnly]);
}

function useRoleItems(assignableOnly: boolean): PickerItem[] {
  const { roles } = useResources();
  return useMemo(() => roles.filter((r) => !r.managed).map((r) => ({
    id: r.id, label: r.name, color: r.color, disabled: assignableOnly && !r.assignable,
    hint: assignableOnly && !r.assignable ? "выше роли бота" : undefined,
  })), [roles, assignableOnly]);
}

export function ChannelSelect({ value, onChange, placeholder = "— канал не выбран —", textOnly = true }: {
  value: string | null; onChange: (v: string | null) => void; placeholder?: string; textOnly?: boolean;
}) {
  const items = useChannelItems(textOnly);
  const { connected } = useResources();
  return <Picker items={items} value={value} onChange={onChange} placeholder={placeholder} clearLabel={placeholder} missingLabel="Канал"
    searchPlaceholder="Поиск канала…" emptyText={connected ? "Канал не найден" : "Бот не подключён — каналы недоступны"} />;
}

function RoleChip({ id, onRemove }: { id: string; onRemove: () => void }) {
  const { roles, connected } = useResources();
  const role = roles.find((r) => r.id === id);
  const problem = connected && (!role ? "роль удалена" : !role.assignable ? "бот не может выдать" : "");
  return (
    <span className={`chip ${problem ? "problem" : ""}`} title={problem || undefined}>
      <span className="role-dot" style={{ background: role?.color ?? "var(--muted)" }} />
      {role ? role.name : `Роль ${id}`}
      {problem && <span className="small">({problem})</span>}
      <button type="button" onClick={onRemove} aria-label="Убрать"><Icon name="x" size={12} /></button>
    </span>
  );
}

export function RoleSelect({ value, onChange, assignableOnly = false, placeholder = "— выберите роль —" }: {
  value: string | null; onChange: (v: string | null) => void; assignableOnly?: boolean; placeholder?: string;
}) {
  const items = useRoleItems(assignableOnly);
  const { connected } = useResources();
  return <Picker items={items} value={value} onChange={onChange} placeholder={placeholder} searchPlaceholder="Поиск роли…" missingLabel="Роль"
    emptyText={connected ? "Роль не найдена" : "Бот не подключён — роли недоступны"} />;
}

function AddTrigger({ label }: { label: string }) {
  return <><Icon name="plus" size={14} />{label}</>;
}

export function MultiRoleSelect({ value, onChange, assignableOnly = false }: {
  value: string[]; onChange: (v: string[]) => void; assignableOnly?: boolean;
}) {
  const items = useRoleItems(assignableOnly).filter((i) => !value.includes(i.id));
  return (
    <div className="chips">
      {value.map((id) => <RoleChip key={id} id={id} onRemove={() => onChange(value.filter((x) => x !== id))} />)}
      <Picker items={items} value={null} trigger={<AddTrigger label="Роль" />} searchPlaceholder="Поиск роли…"
        onChange={(v) => v && !value.includes(v) && onChange([...value, v])} />
    </div>
  );
}

export function MultiChannelSelect({ value, onChange, categories = false }: {
  value: string[]; onChange: (v: string[]) => void; categories?: boolean;
}) {
  const { categories: cats } = useResources();
  const channelItems = useChannelItems(false);
  const all: PickerItem[] = categories ? cats.map((c) => ({ id: c.id, label: c.name, icon: "folder" as IconName })) : channelItems;
  return (
    <div className="chips">
      {value.map((id) => {
        const item = all.find((i) => i.id === id);
        return (
          <span key={id} className={`chip ${item ? "" : "problem"}`}>
            <Icon name={categories ? "folder" : "hash"} size={12} className="muted" />
            {item ? item.label : `${categories ? "Категория" : "Канал"} ${id}`}
            <button type="button" onClick={() => onChange(value.filter((x) => x !== id))} aria-label="Убрать"><Icon name="x" size={12} /></button>
          </span>
        );
      })}
      <Picker items={all.filter((i) => !value.includes(i.id))} value={null} trigger={<AddTrigger label={categories ? "Категория" : "Канал"} />}
        searchPlaceholder={categories ? "Поиск категории…" : "Поиск канала…"}
        onChange={(v) => v && !value.includes(v) && onChange([...value, v])} />
    </div>
  );
}

export function TagsInput({ value, onChange, placeholder, pattern }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string; pattern?: RegExp;
}) {
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const add = () => {
    const parts = text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
    const bad = pattern ? parts.filter((p) => !pattern.test(p)) : [];
    if (bad.length) {
      setErr(`Неверное значение: ${bad.join(", ")}`);
      return;
    }
    setErr("");
    onChange([...new Set([...value, ...parts])]);
    setText("");
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.length > 0 && (
        <div className="chips">
          {value.map((v) => (
            <span key={v} className="chip">{v}
              <button type="button" onClick={() => onChange(value.filter((x) => x !== v))} aria-label="Убрать">×</button>
            </span>
          ))}
        </div>
      )}
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input className="input" value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
        <button type="button" className="btn" onClick={add} disabled={!text.trim()}>Добавить</button>
      </div>
      {err && <span className="field-error">{err}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ шаблоны и предпросмотр */

export function VariableHints({ vars, onInsert }: { vars: Record<string, string>; onInsert: (v: string) => void }) {
  return (
    <div className="vars">
      {Object.entries(vars).map(([k, desc]) => (
        <button key={k} type="button" className="var-btn" title={desc} onClick={() => onInsert(k === "choose" ? "{choose:да|нет}" : `{${k}}`)}>
          {k === "choose" ? "{choose:…}" : `{${k}}`}
        </button>
      ))}
    </div>
  );
}

export function TemplateInput({ value, onChange, vars, rows = 3, maxLength = 2000, placeholder }: {
  value: string; onChange: (v: string) => void; vars: Record<string, string>; rows?: number; maxLength?: number; placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const insert = (token: string) => {
    const el = ref.current;
    if (!el) return onChange(value + token);
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    const next = value.slice(0, start) + token + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = start + token.length;
    });
  };
  return (
    <div className="stack" style={{ gap: 6 }}>
      <textarea ref={ref} className="textarea" rows={rows} value={value} maxLength={maxLength} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} />
      <div className="row small muted" style={{ justifyContent: "space-between" }}>
        <VariableHints vars={vars} onInsert={insert} />
        <span>{value.length}/{maxLength}</span>
      </div>
    </div>
  );
}

export function EmbedEditor({ value, onChange, vars }: {
  value: EmbedConfig; onChange: (v: EmbedConfig) => void; vars: Record<string, string>;
}) {
  const set = <K extends keyof EmbedConfig>(k: K, v: EmbedConfig[K]) => onChange({ ...value, [k]: v });
  return (
    <div className="stack">
      <ToggleRow title="Embed-сообщение" hint="Оформленный блок с заголовком и цветной полосой" checked={value.enabled}
        onChange={(v) => set("enabled", v)} />
      {value.enabled && (
        <>
          <div className="grid grid-2">
            <Field label="Заголовок"><input className="input" value={value.title} maxLength={256} onChange={(e) => set("title", e.target.value)} /></Field>
            <Field label="Цвет полосы">
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <input type="color" value={value.color} onChange={(e) => set("color", e.target.value)} style={{ width: 44, height: 34, border: "none", background: "none" }} />
                <input className="input" value={value.color} onChange={(e) => set("color", e.target.value)} maxLength={7} />
              </div>
            </Field>
          </div>
          <Field label="Описание">
            <TemplateInput value={value.description} onChange={(v) => set("description", v)} vars={vars} rows={4} maxLength={4000} />
          </Field>
          <div className="grid grid-2">
            <Field label="Подпись (footer)"><input className="input" value={value.footer} maxLength={2048} onChange={(e) => set("footer", e.target.value)} /></Field>
            <Field label="Ссылка на изображение" hint="https://…, необязательно"><input className="input" value={value.image_url} maxLength={512} onChange={(e) => set("image_url", e.target.value)} /></Field>
          </div>
          <ToggleRow title="Показывать аватар участника" checked={value.show_avatar} onChange={(v) => set("show_avatar", v)} />
        </>
      )}
    </div>
  );
}

export type PreviewData = { text: string; embed: null | { title: string; description: string; footer: string; color: string; image_url: string; show_avatar: boolean } };

/** Предпросмотр сообщения бота; children — вложения и компоненты (кнопки, списки) под сообщением. */
export function DiscordPreview({ data, loading, children }: { data: PreviewData | null; loading?: boolean; children?: ReactNode }) {
  if (loading && !data) return <Loading text="Формирую предпросмотр…" />;
  if (!children && (!data || (!data.text && !data.embed))) return <div className="muted small">Сообщение пустое — ничего не будет отправлено.</div>;
  return (
    <div className="preview">
      <div className="author">
        <div className="bot-avatar">M</div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div><span className="name">Mishanya</span><span className="bot-tag">БОТ</span></div>
          {data?.text && <div className="text"><Markdown text={data.text} /></div>}
          {data?.embed && (
            <div className="embed" style={{ borderLeftColor: data.embed.color }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                {data.embed.title && <div className="embed-title"><Markdown text={data.embed.title} /></div>}
                {data.embed.description && <div className="embed-desc"><Markdown text={data.embed.description} /></div>}
                {data.embed.image_url && <img className="embed-image" src={data.embed.image_url} alt="" />}
                {data.embed.footer && <div className="embed-footer">{data.embed.footer}</div>}
              </div>
              {data.embed.show_avatar && <div className="embed-thumb" />}
            </div>
          )}
          {children}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ прочее */

export function SaveBar({ dirty, saving, onSave, onReset }: { dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void }) {
  useEffect(() => {
    if (!dirty) return;
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (!saving) onSave();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [dirty, saving, onSave]);
  if (!dirty) return null;
  return (
    <div className="savebar" role="region" aria-label="Несохранённые изменения">
      <Icon name="info" size={16} />
      <span>Есть несохранённые изменения</span>
      <span className="spacer" />
      <kbd className="savebar-kbd">Ctrl+S</kbd>
      <button className="btn btn-sm" onClick={onReset} disabled={saving}>Отменить</button>
      <button className="btn btn-primary btn-sm" onClick={onSave} disabled={saving}>{saving ? "Сохранение…" : "Сохранить"}</button>
    </div>
  );
}

/* ------------------------------------------------------------------ тема */

export type Theme = "system" | "light" | "dark";
const THEME_KEY = "mishanya-theme";

export function readTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(t: Theme) {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const set = (t: Theme) => {
    setTheme(t);
    applyTheme(t);
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch {
      /* хранилище недоступно — тема действует до перезагрузки */
    }
  };
  const opts: [Theme, IconName, string][] = [["system", "monitor", "Как в системе"], ["light", "sun", "Светлая"], ["dark", "moon", "Тёмная"]];
  return (
    <div className="segmented" role="radiogroup" aria-label="Тема оформления">
      {opts.map(([t, icon, label]) => (
        <button key={t} type="button" role="radio" aria-checked={theme === t} title={label} className={theme === t ? "active" : ""} onClick={() => set(t)}>
          <Icon name={icon} size={15} />
        </button>
      ))}
    </div>
  );
}

export function Pagination({ total, offset, limit, onChange }: { total: number; offset: number; limit: number; onChange: (o: number) => void }) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <div className="row mt" style={{ justifyContent: "flex-end" }}>
      <span className="muted small">Страница {page} из {pages} · всего {total}</span>
      <button className="btn btn-sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Назад</button>
      <button className="btn btn-sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Вперёд</button>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: [T, string][]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={`tab ${value === k ? "active" : ""}`} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <div className="row" style={{ marginBottom: 10 }}>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <span className="spacer" />
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">×</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function GuildIcon({ name, url, small }: { name: string; url: string | null; small?: boolean }) {
  const initials = useMemo(() => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "?", [name]);
  const cls = `guild-icon ${small ? "guild-icon-sm" : ""}`;
  return url ? <img className={cls} src={url} alt="" /> : <div className={cls}>{initials}</div>;
}

export function RoleName({ id }: { id: string }) {
  const role = useResources().roles.find((r) => r.id === id);
  return (
    <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
      <span className="role-dot" style={{ background: role?.color ?? "var(--muted)" }} />
      {role ? role.name : <span className="muted">Роль {id}</span>}
    </span>
  );
}

export function ChannelName({ id }: { id: string | null }) {
  const ch = useResources().channels.find((c) => c.id === id);
  if (!id) return <span className="muted">—</span>;
  return <span>{ch ? `#${ch.name}` : `#${id}`}</span>;
}

export const fmtDate = (iso: string | null | undefined, withTime = true) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return withTime ? d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : d.toLocaleDateString("ru-RU");
};

export const fmtNum = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString("ru-RU"));

export const fmtBytes = (n: number) => (n < 1024 ? `${n} Б` : n < 1048576 ? `${(n / 1024).toFixed(1)} КБ` : `${(n / 1048576).toFixed(1)} МБ`);

export function roleOf(roles: Role[], id: string) {
  return roles.find((r) => r.id === id);
}
