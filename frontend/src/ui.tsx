import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Channel, DiscordResources, EmbedConfig, Role } from "./types";

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
          <div key={t.id} className={`toast toast-${t.kind}`}>{t.text}</div>
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

function channelLabel(c: Channel) {
  return `#${c.name}${c.category ? ` · ${c.category}` : ""}${c.can_send ? "" : " (нет прав)"}`;
}

export function ChannelSelect({ value, onChange, placeholder = "— не выбран —", textOnly = true }: {
  value: string | null; onChange: (v: string | null) => void; placeholder?: string; textOnly?: boolean;
}) {
  const { channels, connected } = useResources();
  const list = channels.filter((c) => !textOnly || c.text);
  const known = !value || list.some((c) => c.id === value);
  return (
    <select className="select" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{placeholder}</option>
      {!known && <option value={value!}>{connected ? `⚠ удалённый канал ${value}` : `Канал ${value}`}</option>}
      {list.map((c) => (
        <option key={c.id} value={c.id}>{channelLabel(c)}</option>
      ))}
    </select>
  );
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
      <button type="button" onClick={onRemove} aria-label="Убрать">×</button>
    </span>
  );
}

export function RoleSelect({ value, onChange, assignableOnly = false, placeholder = "— выберите роль —" }: {
  value: string | null; onChange: (v: string | null) => void; assignableOnly?: boolean; placeholder?: string;
}) {
  const { roles } = useResources();
  return (
    <select className="select" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{placeholder}</option>
      {roles.filter((r) => !r.managed).map((r) => (
        <option key={r.id} value={r.id} disabled={assignableOnly && !r.assignable}>
          {r.name}{assignableOnly && !r.assignable ? " (выше роли бота)" : ""}
        </option>
      ))}
    </select>
  );
}

export function MultiRoleSelect({ value, onChange, assignableOnly = false }: {
  value: string[]; onChange: (v: string[]) => void; assignableOnly?: boolean;
}) {
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.length > 0 && (
        <div className="chips">
          {value.map((id) => <RoleChip key={id} id={id} onRemove={() => onChange(value.filter((x) => x !== id))} />)}
        </div>
      )}
      <RoleSelect value={null} assignableOnly={assignableOnly} placeholder="+ добавить роль"
        onChange={(v) => v && !value.includes(v) && onChange([...value, v])} />
    </div>
  );
}

export function MultiChannelSelect({ value, onChange, categories = false }: {
  value: string[]; onChange: (v: string[]) => void; categories?: boolean;
}) {
  const { channels, categories: cats } = useResources();
  const items = categories ? cats.map((c) => ({ id: c.id, label: c.name })) : channels.map((c) => ({ id: c.id, label: `#${c.name}` }));
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.length > 0 && (
        <div className="chips">
          {value.map((id) => {
            const item = items.find((i) => i.id === id);
            return (
              <span key={id} className={`chip ${item ? "" : "problem"}`}>
                {item ? item.label : `${categories ? "Категория" : "Канал"} ${id}`}
                <button type="button" onClick={() => onChange(value.filter((x) => x !== id))} aria-label="Убрать">×</button>
              </span>
            );
          })}
        </div>
      )}
      <select className="select" value="" onChange={(e) => e.target.value && !value.includes(e.target.value) && onChange([...value, e.target.value])}>
        <option value="">{categories ? "+ добавить категорию" : "+ добавить канал"}</option>
        {items.filter((i) => !value.includes(i.id)).map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
      </select>
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

export function DiscordPreview({ data, loading }: { data: PreviewData | null; loading?: boolean }) {
  if (loading && !data) return <Loading text="Формирую предпросмотр…" />;
  if (!data || (!data.text && !data.embed)) return <div className="muted small">Сообщение пустое — ничего не будет отправлено.</div>;
  return (
    <div className="preview">
      <div className="author">
        <div className="bot-avatar">M</div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div><span className="name">Mishanya</span><span className="bot-tag">БОТ</span></div>
          {data.text && <div className="text">{data.text}</div>}
          {data.embed && (
            <div className="embed" style={{ borderLeftColor: data.embed.color }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                {data.embed.title && <div className="embed-title">{data.embed.title}</div>}
                {data.embed.description && <div className="embed-desc">{data.embed.description}</div>}
                {data.embed.image_url && <img className="embed-image" src={data.embed.image_url} alt="" />}
                {data.embed.footer && <div className="embed-footer">{data.embed.footer}</div>}
              </div>
              {data.embed.show_avatar && <div className="embed-thumb" />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ прочее */

export function SaveBar({ dirty, saving, onSave, onReset }: { dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void }) {
  if (!dirty) return null;
  return (
    <div className="savebar">
      <span>Есть несохранённые изменения</span>
      <span className="spacer" />
      <button className="btn btn-sm" onClick={onReset} disabled={saving}>Отменить</button>
      <button className="btn btn-primary btn-sm" onClick={onSave} disabled={saving}>{saving ? "Сохранение…" : "Сохранить"}</button>
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
