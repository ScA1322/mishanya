import { useEffect, useState } from "react";
import { del, errorText, get, post, put } from "../api";
import { useApi } from "../hooks";
import { Icon } from "../icons";
import { Card, ChannelSelect, Field, MultiRoleSelect, NumberInput, ToggleRow, fmtDate, useConfirm, useToast } from "../ui";

export interface VoiceTrigger { phrase: string; steam_app_id: number; title: string }
export interface MindConfig {
  wake_words_in_text: boolean; dialog_seconds: number; reply_cooldown_seconds: number; dialog_max_per_hour: number;
  memory_enabled: boolean; memory_max: number; vision_enabled: boolean;
  punish_enabled: boolean; punish_min_level: number; punish_mute_minutes: number; punish_remove_roles: boolean;
  apology_channel_id: string | null; punish_exempt_roles: string[];
  move_enabled: boolean; move_others_requires_permission: boolean;
  control_music: boolean; control_moderation: boolean; control_roles: boolean;
  control_kick_ban: boolean; kick_ban_per_hour: number; character_ignore: boolean;
  relay_dm: boolean; relay_per_hour: number; relay_anonymous: boolean;
  family: { user_id: string | null; username: string; title: string; prompt: string }[];
  confidants: string[]; confidant_dm_chat: boolean; dm_open: boolean; dm_max_per_hour: number;
  join_requests: boolean; join_min_messages: number; join_retry_hours: number;
  pc_control: boolean; pc_start_menu: boolean; pc_programs: { name: string; target: string }[];
  report_inactive_days: number; gossip_hours: number;
  voice_triggers: VoiceTrigger[]; trigger_cooldown_minutes: number;
  listen_wake_words: string[]; listen_interject_percent: number; listen_interject_seconds: number;
}
type Setter<C> = <K extends keyof C>(k: K, v: C[K]) => void;

interface MemberRef { id: string; name: string; avatar_url: string | null }
interface MindData {
  memory: { total: number; general: number; people: { user: MemberRef; count: number }[] };
  grudges: { user: MemberRef; roles: string[]; reason: string; created_at: string }[];
  inactive: { user: MemberRef; last_seen: string }[];
  relations: { user: MemberRef; attitude: number; ignore_until: string | null; ignore_reason: string }[];
  vision_model: string;
}
export interface AiStatus { ai_configured: boolean; model: string; base_url: string; vision_model: string }

export function DialogCard<C extends MindConfig>({ d, set }: { d: C; set: Setter<C> }) {
  return (
    <Card title="Разговор" subtitle="Как бот ведёт живой диалог в чате. Отвечает на упоминание, на ответ на его сообщение и на слова-обращения, а потом какое-то время продолжает разговор с этим человеком без обращения.">
      <ToggleRow title="Слова-обращения работают и в тексте" hint={`Например: «${d.listen_wake_words[0] ?? "император"}, ты где?» — бот ответит. Слова задаются в блоке «Слушать голос»`}
        checked={d.wake_words_in_text} onChange={(v) => set("wake_words_in_text", v)} />
      <div className="grid grid-3 mt">
        <Field label="Продолжать разговор, сек" hint="После ответа бот отвечает этому человеку без обращения. 0 — только по обращению">
          <NumberInput value={d.dialog_seconds} min={0} max={900} onChange={(v) => set("dialog_seconds", v)} /></Field>
        <Field label="Пауза между ответами одному, сек" hint="Защита от спама"><NumberInput value={d.reply_cooldown_seconds} min={0} max={120} onChange={(v) => set("reply_cooldown_seconds", v)} /></Field>
        <Field label="Ответов в час на сервер" hint="Ограничивает расходы на ИИ"><NumberInput value={d.dialog_max_per_hour} min={1} max={600} onChange={(v) => set("dialog_max_per_hour", v)} /></Field>
      </div>
    </Card>
  );
}

export function ModelsCard<C extends MindConfig>({ status, onChange, d, set }: { status: AiStatus | null; onChange: () => void; d: C; set: Setter<C> }) {
  const toast = useToast();
  const [form, setForm] = useState({ base_url: "", model: "", vision_model: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (status) setForm({ base_url: status.base_url, model: status.model, vision_model: status.vision_model });
  }, [status?.base_url, status?.model, status?.vision_model]);
  const save = async () => {
    setBusy(true);
    try { await put("/api/ai/models", form); toast("ok", "Сохранено в .env"); onChange(); }
    catch (e) { toast("err", errorText(e)); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Модели ИИ" subtitle="Куда бот ходит за ответами. Для Inferhold: адрес https://api.inferhold.com/v1 и ключ Inferhold выше. Сохраняется в .env на этом компьютере.">
      <div className="grid grid-3">
        <Field label="Адрес API" hint="DeepSeek: https://api.deepseek.com"><input className="input" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value.trim() })} /></Field>
        <Field label="Модель для текста" hint="deepseek-chat, gpt-4o-mini…"><input className="input" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value.trim() })} /></Field>
        <Field label="Модель для картинок" hint="Со зрением, например gpt-4o-mini у Inferhold. Пусто — скриншоты не смотрит"><input className="input" value={form.vision_model} placeholder="не задана" onChange={(e) => setForm({ ...form, vision_model: e.target.value.trim() })} /></Field>
      </div>
      <div className="row mt"><button className="btn btn-primary" disabled={busy || !form.base_url || !form.model} onClick={save}>Сохранить модели</button></div>
      <ToggleRow title="Смотреть картинки и скриншоты" hint="Когда к сообщению, обращённому к боту, приложена картинка, он её разглядывает. Сам DeepSeek картинки не видит — нужна модель для картинок"
        checked={d.vision_enabled} onChange={(v) => set("vision_enabled", v)} />
      <div className="small muted">Демонстрацию экрана бот смотреть не может: Discord не отдаёт ботам видео.</div>
    </Card>
  );
}

function useMind(guildId: string) {
  return useApi<MindData>(`/api/guilds/${guildId}/mind`, [guildId]);
}

interface Fact { id: number; text: string; user: MemberRef | null; created_at: string }

export function MemoryCard<C extends MindConfig>({ guildId, d, set }: { guildId: string; d: C; set: Setter<C> }) {
  const toast = useToast();
  const confirm = useConfirm();
  const mind = useMind(guildId);
  const [busy, setBusy] = useState(false);
  const [who, setWho] = useState<string>("all");
  const [q, setQ] = useState("");
  const [facts, setFacts] = useState<{ total: number; items: Fact[] } | null>(null);
  const [peopleFilter, setPeopleFilter] = useState("");
  const load = async (offset = 0) => {
    try {
      const r = await get<{ total: number; items: Fact[] }>(`/api/guilds/${guildId}/mind/memories?who=${who}&q=${encodeURIComponent(q)}&offset=${offset}&limit=100`);
      setFacts((prev) => (offset && prev ? { total: r.total, items: [...prev.items, ...r.items] } : r));
    } catch (e) { toast("err", errorText(e)); }
  };
  useEffect(() => {
    const t = setTimeout(() => { void load(0); }, 250);
    return () => clearTimeout(t);
  }, [guildId, who, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const learn = async () => {
    setBusy(true);
    try { const r = await post<{ added: number }>(`/api/guilds/${guildId}/mind/learn`); toast("ok", r.added ? `Запомнил новых фактов: ${r.added}` : "Ничего нового не запомнил"); mind.reload(); void load(0); }
    catch (e) { toast("err", errorText(e)); }
    finally { setBusy(false); }
  };
  const forget = async (id?: number) => {
    const person = who !== "all" && who !== "general";
    if (id === undefined && !(await confirm({ title: person ? "Стереть всё об этом человеке?" : "Стереть всю память?",
      text: person ? "Бот забудет всё, что узнал о нём." : "Бот забудет всё, что узнал о сервере и людях.", danger: true, confirmText: "Стереть" }))) return;
    try {
      await del(id === undefined ? `/api/guilds/${guildId}/mind/memories?who=${person ? who : "all"}` : `/api/guilds/${guildId}/mind/memories/${id}`);
      mind.reload(); void load(0);
    } catch (e) { toast("err", errorText(e)); }
  };
  const mem = mind.data?.memory;
  const people = (mem?.people ?? []).filter((p) => !peopleFilter || p.user.name.toLowerCase().includes(peopleFilter.toLowerCase()));
  return (
    <Card title={`Память и саморазвитие (${(mem?.total ?? 0).toLocaleString("ru-RU")} фактов)`} subtitle="Бот сам перечитывает чат и голосовые разговоры и запоминает факты — отдельно о каждом человеке и о сервере в целом. В разговоре он вспоминает факты о собеседнике и о тех, кого обсуждают, и подходящие к теме.">
      <ToggleRow title="Запоминать" checked={d.memory_enabled} onChange={(v) => set("memory_enabled", v)} />
      <div className="row mt" style={{ alignItems: "flex-end" }}>
        <Field label="Помнить фактов, максимум" hint="Старые вытесняются новыми. До 1 000 000"><NumberInput value={d.memory_max} min={10} max={1000000} onChange={(v) => set("memory_max", v)} /></Field>
        <button className="btn" disabled={busy} onClick={learn}><Icon name="zap" size={15} />{busy ? "Думаю…" : "Обдумать сейчас"}</button>
      </div>

      <h3 className="mt">О ком помнит</h3>
      {(mem?.people.length ?? 0) > 12 && <input className="input mt" placeholder="Найти человека…" value={peopleFilter} onChange={(e) => setPeopleFilter(e.target.value)} />}
      <div className="chips mt" style={{ maxHeight: 180, overflow: "auto" }}>
        <button type="button" className={`chip${who === "all" ? " chip-active" : ""}`} onClick={() => setWho("all")}>Все · {(mem?.total ?? 0).toLocaleString("ru-RU")}</button>
        <button type="button" className={`chip${who === "general" ? " chip-active" : ""}`} onClick={() => setWho("general")}>О сервере · {(mem?.general ?? 0).toLocaleString("ru-RU")}</button>
        {people.map((p) => (
          <button key={p.user.id} type="button" className={`chip${who === p.user.id ? " chip-active" : ""}`} onClick={() => setWho(p.user.id)}>
            {p.user.name} · {p.count.toLocaleString("ru-RU")}</button>
        ))}
      </div>

      <div className="row mt">
        <input className="input" style={{ flex: 1 }} placeholder="Поиск по фактам…" value={q} onChange={(e) => setQ(e.target.value)} />
        {!!facts?.total && <button className="btn btn-sm btn-danger" onClick={() => forget()}>{who !== "all" && who !== "general" ? "Забыть всё о нём" : "Стереть всё"}</button>}
      </div>
      <div className="small muted mt">Найдено: {(facts?.total ?? 0).toLocaleString("ru-RU")}</div>
      <div className="stack mt" style={{ gap: 4, maxHeight: 420, overflow: "auto" }}>
        {facts?.items.map((m) => (
          <div key={m.id} className="row small" style={{ gap: 8 }}>
            <span className="muted" style={{ minWidth: 86 }}>{fmtDate(m.created_at, false)}</span>
            {who === "all" && <span className="badge" style={{ minWidth: 0 }}>{m.user?.name ?? "сервер"}</span>}
            <span style={{ flex: 1 }}>{m.text}</span>
            <button className="btn btn-sm btn-ghost" onClick={() => forget(m.id)} aria-label="Забыть"><Icon name="trash" size={13} /></button>
          </div>
        ))}
        {facts && facts.items.length < facts.total && <button className="btn btn-sm" onClick={() => load(facts.items.length)}>Показать ещё</button>}
        {facts && !facts.total && <div className="small muted">{q ? "Ничего не найдено." : "Пока ничего не помнит. Факты появляются примерно каждые 25 сообщений в чате."}</div>}
      </div>
    </Card>
  );
}

const LEVELS: [number, string][] = [[1, "даже за подколы и стёб"], [2, "за настоящие оскорбления"], [3, "только за жёсткую травлю"]];

export function GrudgeCard<C extends MindConfig>({ guildId, d, set }: { guildId: string; d: C; set: Setter<C> }) {
  const toast = useToast();
  const mind = useMind(guildId);
  const forgive = async (id: string) => {
    try { const r = await post<{ restored: number }>(`/api/guilds/${guildId}/mind/forgive/${id}`); toast("ok", `Прощён, ролей возвращено: ${r.restored}`); mind.reload(); }
    catch (e) { toast("err", errorText(e)); }
  };
  return (
    <Card title="Обиды и извинения" subtitle="Если над ботом издеваются или оскорбляют его, он даёт мут и забирает роли. Вернёт всё, только если человек извинится в канале для извинений и признает, что был неправ. Отписки вроде «ну сорян» не принимает.">
      <ToggleRow title="Обижаться и наказывать" checked={d.punish_enabled} onChange={(v) => set("punish_enabled", v)} />
      <div className="grid grid-3 mt">
        <Field label="Наказывать">
          <select className="select" value={d.punish_min_level} onChange={(e) => set("punish_min_level", Number(e.target.value))}>
            {LEVELS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </Field>
        <Field label="Мут — не больше, минут" hint="Срок бот выбирает сам по тяжести обиды: за подкол пару минут, за травлю часы. 0 — без мута. В муте нельзя писать даже в канал извинений"><NumberInput value={d.punish_mute_minutes} min={0} max={40320} onChange={(v) => set("punish_mute_minutes", v)} /></Field>
        <Field label="Канал для извинений" hint="Должен быть виден всем — ролей у наказанного не будет" error={d.punish_enabled && !d.apology_channel_id ? "Без канала извиниться будет негде" : undefined}>
          <ChannelSelect value={d.apology_channel_id} onChange={(v) => set("apology_channel_id", v)} />
        </Field>
      </div>
      <ToggleRow title="Забирать роли до извинения" hint="Снимаются все роли ниже роли бота; после извинения возвращаются" checked={d.punish_remove_roles} onChange={(v) => set("punish_remove_roles", v)} />
      <Field label="Кого не трогать" hint="Владелец, администраторы и те, чья роль выше роли бота, не наказываются в любом случае">
        <MultiRoleSelect value={d.punish_exempt_roles} onChange={(v) => set("punish_exempt_roles", v)} />
      </Field>
      <h3 className="mt">Сейчас обижен на</h3>
      {mind.data?.grudges.length ? (
        <div className="stack" style={{ gap: 6 }}>
          {mind.data.grudges.map((g) => (
            <div key={g.user.id} className="row small" style={{ gap: 8 }}>
              <b>{g.user.name}</b><span className="muted">{fmtDate(g.created_at)}</span>
              <span style={{ flex: 1 }}>за: {g.reason || "?"} · снято ролей: {g.roles.length}</span>
              <button className="btn btn-sm" onClick={() => forgive(g.user.id)}>Простить</button>
            </div>
          ))}
        </div>
      ) : <div className="small muted">Ни на кого не обижен.</div>}
    </Card>
  );
}

export function MoveCard<C extends MindConfig>({ d, set }: { d: C; set: Setter<C> }) {
  return (
    <Card title="Власть бота" subtitle="Бот сам решает, что делать на сервере: по просьбе в чате или голосом («император, выруби музыку») — или по своей воле. Может и отказать. Владельца, администраторов и тех, чья роль выше роли бота, он не трогает; роли с админскими правами не раздаёт.">
      <ToggleRow title="Музыка" hint="Включить трек, пауза, пропуск, стоп, громкость, повтор, перемешать" checked={d.control_music} onChange={(v) => set("control_music", v)} />
      <ToggleRow title="Голосовые каналы" hint="Перекинуть в другой канал, выкинуть из войса, выключить микрофон. Нужно право «Перемещать участников» и «Отключать микрофон»" checked={d.move_enabled} onChange={(v) => set("move_enabled", v)} />
      {d.move_enabled && <ToggleRow title="Чужих перекидывать — только по просьбе тех, у кого есть право перемещать" hint="Выключено — бот сам решает, кого куда утащить"
        checked={d.move_others_requires_permission} onChange={(v) => set("move_others_requires_permission", v)} />}
      <ToggleRow title="Модерация" hint="Мут в чате и снятие мута, смена ника, удаление последних сообщений (до 50)" checked={d.control_moderation} onChange={(v) => set("control_moderation", v)} />
      <ToggleRow title="Роли" hint="Выдать или забрать роль ниже роли бота (кроме ролей с админскими правами)" checked={d.control_roles} onChange={(v) => set("control_roles", v)} />
      <ToggleRow title="Передавать сообщения в личку" hint="«Император, напиши Васе в лс, что он лох» — бот передаст от своего имени. Просить может кто угодно, бот может отказать" checked={d.relay_dm} onChange={(v) => set("relay_dm", v)} />
      {d.relay_dm && (
        <div className="grid grid-2">
          <Field label="Передач в час от одного человека" hint="Чтобы бота не превратили в спамера"><NumberInput value={d.relay_per_hour} min={1} max={100} onChange={(v) => set("relay_per_hour", v)} /></Field>
          <ToggleRow title="Анонимно" hint="Не писать получателю, кто просил передать" checked={d.relay_anonymous} onChange={(v) => set("relay_anonymous", v)} />
        </div>
      )}
      <ToggleRow title="Кик и бан" hint="Выгнать или забанить по своему решению. О каждом кике и бане доверенным уходит доклад с причиной" checked={d.control_kick_ban} onChange={(v) => set("control_kick_ban", v)} />
      {d.control_kick_ban && <Field label="Киков и банов в час, не больше" hint="Защита на случай, если модель «сойдёт с ума»"><NumberInput value={d.kick_ban_per_hour} min={1} max={30} onChange={(v) => set("kick_ban_per_hour", v)} /></Field>}
      <div className="small muted">Боту нужны права: «Управлять ролями», «Управлять никнеймами», «Отправлять участников подумать», «Управлять сообщениями», «Перемещать участников», «Отключать участникам микрофон», «Выгонять участников», «Банить участников».</div>
    </Card>
  );
}

function MemberChip({ guildId, id, onRemove }: { guildId: string; id: string; onRemove: () => void }) {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    get<{ id: string; display_name: string; name: string }[]>(`/api/guilds/${guildId}/members/search?q=${id}`)
      .then((r) => setName(r[0] ? `${r[0].display_name} (@${r[0].name})` : null)).catch(() => setName(null));
  }, [guildId, id]);
  return <span className="chip"><Icon name="users" size={12} className="muted" />{name ?? `участник ${id}`}
    <button type="button" onClick={onRemove} aria-label="Убрать"><Icon name="x" size={12} /></button></span>;
}

function MemberSearch({ guildId, exclude, onAdd }: { guildId: string; exclude: string[]; onAdd: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<{ id: string; display_name: string; name: string; bot: boolean }[]>([]);
  useEffect(() => {
    if (!q.trim()) { setRes([]); return; }
    const t = setTimeout(() => {
      get<typeof res>(`/api/guilds/${guildId}/members/search?q=${encodeURIComponent(q.trim())}`).then(setRes).catch(() => setRes([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, guildId]);
  const options = res.filter((m) => !m.bot && !exclude.includes(m.id));
  return (
    <div className="stack" style={{ gap: 4 }}>
      <input className="input" placeholder="Найти участника по имени…" value={q} onChange={(e) => setQ(e.target.value)} />
      {options.slice(0, 8).map((m) => (
        <button key={m.id} type="button" className="btn btn-sm btn-ghost" style={{ justifyContent: "flex-start" }} onClick={() => { onAdd(m.id); setQ(""); }}>
          <Icon name="plus" size={13} />{m.display_name} <span className="muted">@{m.name}</span></button>
      ))}
    </div>
  );
}

export function ConfidantsCard<C extends MindConfig>({ guildId, d, set }: { guildId: string; d: C; set: Setter<C> }) {
  const mind = useMind(guildId);
  return (
    <Card title="Доверенные люди и личка" subtitle="Доверенным бот пишет в личку: кто давно не заходит в войс, сплетни, на кого обиделся и за что, кого простил и почему, кто слишком интересуется сервером. С ними он общается откровенно.">
      <Field label={`Доверенные (${d.confidants.length}/10)`}>
        <div className="chips">
          {d.confidants.map((id) => <MemberChip key={id} guildId={guildId} id={id} onRemove={() => set("confidants", d.confidants.filter((x) => x !== id) as C["confidants"])} />)}
        </div>
      </Field>
      {d.confidants.length < 10 && <MemberSearch guildId={guildId} exclude={d.confidants} onAdd={(id) => set("confidants", [...d.confidants, id] as C["confidants"])} />}
      <div className="grid grid-2 mt">
        <Field label="Докладывать, если не заходил в войс, дней" hint="0 — не докладывать. Считаются те, кого бот хоть раз видел в войсе"><NumberInput value={d.report_inactive_days} min={0} max={365} onChange={(v) => set("report_inactive_days", v)} /></Field>
        <Field label="Сплетничать раз в, часов" hint="0 — не сплетничать. Каждому — только из каналов, которые он видит сам"><NumberInput value={d.gossip_hours} min={0} max={168} onChange={(v) => set("gossip_hours", v)} /></Field>
      </div>
      <ToggleRow title="Переписываться с доверенными в личке" hint="Рассказывает им, что творится на сервере" checked={d.confidant_dm_chat} onChange={(v) => set("confidant_dm_chat", v)} />
      <ToggleRow title="Отвечать в личке всем остальным" hint="Участникам сервера — в своём стиле, но без внутренних дел; людям с других общих серверов — холодно; тем, у кого с ботом нет ни одного общего сервера, — с максимальной ненавистью. Если кто-то выведывает лишнее, доверенным уйдёт доклад с его юзернеймом"
        checked={d.dm_open} onChange={(v) => set("dm_open", v)} />
      {d.dm_open && <Field label="Ответов одному человеку в час"><NumberInput value={d.dm_max_per_hour} min={1} max={600} onChange={(v) => set("dm_max_per_hour", v)} /></Field>}
      {d.dm_open && <ToggleRow title="Чужаки могут проситься на сервер" hint="Если человек без общих серверов долго переписывается с ботом и просится на сервер, бот сам решает, передать ли просьбу. Доверенным придёт запрос с кнопками «Пустить» и «Отказать»; при «Пустить» бот сам отправит ему одноразовое приглашение на сутки. Бот может отказать сам — но не навсегда"
        checked={d.join_requests} onChange={(v) => set("join_requests", v)} />}
      {d.dm_open && d.join_requests && (
        <div className="grid grid-2">
          <Field label="Сначала написать боту, сообщений" hint="Раньше бот просьбу даже не рассматривает"><NumberInput value={d.join_min_messages} min={1} max={200} onChange={(v) => set("join_min_messages", v)} /></Field>
          <Field label="После отказа можно снова через, часов"><NumberInput value={d.join_retry_hours} min={0} max={720} onChange={(v) => set("join_retry_hours", v)} /></Field>
        </div>
      )}
      {!!mind.data?.inactive.length && (
        <>
          <h3 className="mt">Давно не заходили в войс</h3>
          <div className="small">{mind.data.inactive.map((r) => `${r.user.name} (с ${fmtDate(r.last_seen, false)})`).join(", ")}</div>
        </>
      )}
    </Card>
  );
}

export function TriggersCard<C extends MindConfig>({ d, set }: { d: C; set: Setter<C> }) {
  const upd = (i: number, patch: Partial<VoiceTrigger>) =>
    set("voice_triggers", d.voice_triggers.map((t, j) => (j === i ? { ...t, ...patch } : t)) as C["voice_triggers"]);
  return (
    <Card title="Голосовые триггеры" subtitle="Пока бот слушает голосовой канал: если кто-то скажет фразу, на компьютере, где запущен бот, откроется игра из Steam. Окончания и ошибки распознавания прощаются.">
      <div className="stack" style={{ gap: 8 }}>
        {d.voice_triggers.map((t, i) => (
          <div key={i} className="grid grid-3" style={{ alignItems: "end" }}>
            <Field label="Фраза"><input className="input" value={t.phrase} onChange={(e) => upd(i, { phrase: e.target.value })} /></Field>
            <Field label="ID игры в Steam" hint="Число из ссылки store.steampowered.com/app/…"><NumberInput value={t.steam_app_id} min={1} max={99999999} onChange={(v) => upd(i, { steam_app_id: v })} /></Field>
            <div className="row">
              <Field label="Название"><input className="input" value={t.title} onChange={(e) => upd(i, { title: e.target.value })} /></Field>
              <button className="btn btn-sm btn-ghost" onClick={() => set("voice_triggers", d.voice_triggers.filter((_, j) => j !== i) as C["voice_triggers"])} aria-label="Удалить"><Icon name="trash" size={14} /></button>
            </div>
          </div>
        ))}
      </div>
      <div className="row mt">
        {d.voice_triggers.length < 20 && <button className="btn btn-sm" onClick={() => set("voice_triggers", [...d.voice_triggers, { phrase: "", steam_app_id: 376210, title: "" }] as C["voice_triggers"])}><Icon name="plus" size={14} />Добавить триггер</button>}
        <span className="spacer" />
        <Field label="Не чаще раза в, минут"><NumberInput value={d.trigger_cooldown_minutes} min={0} max={1440} onChange={(v) => set("trigger_cooldown_minutes", v)} /></Field>
      </div>
    </Card>
  );
}

export function CharacterCard<C extends MindConfig>({ guildId, d, set }: { guildId: string; d: C; set: Setter<C> }) {
  const toast = useToast();
  const mind = useMind(guildId);
  const unignore = async (id: string) => {
    try { await post(`/api/guilds/${guildId}/mind/unignore/${id}`); mind.reload(); }
    catch (e) { toast("err", errorText(e)); }
  };
  const rows = mind.data?.relations ?? [];
  return (
    <Card title="Характер" subtitle="У бота своё отношение к каждому человеку: кого-то он любит, кого-то презирает. Если его достают или спамят, он может послать и начать игнорить — тогда его внимание придётся добиваться.">
      <ToggleRow title="Может игнорить людей" checked={d.character_ignore} onChange={(v) => set("character_ignore", v)} />
      <h3 className="mt">Отношение к людям</h3>
      {rows.length ? (
        <div className="stack" style={{ gap: 6 }}>
          {rows.map((r) => (
            <div key={r.user.id} className="row small" style={{ gap: 8 }}>
              <b style={{ minWidth: 140 }}>{r.user.name}</b>
              <span className={`badge ${r.attitude > 0 ? "badge-ok" : r.attitude < 0 ? "badge-err" : ""}`}>{r.attitude > 0 ? "+" : ""}{r.attitude}</span>
              <span style={{ flex: 1 }}>{r.ignore_until ? `игнорит до ${fmtDate(r.ignore_until)}` : ""}</span>
              {r.ignore_until && <button className="btn btn-sm" onClick={() => unignore(r.user.id)}>Перестать игнорить</button>}
            </div>
          ))}
        </div>
      ) : <div className="small muted">Пока ко всем относится ровно.</div>}
    </Card>
  );
}

export function PcCard<C extends MindConfig>({ guildId, d, set }: { guildId: string; d: C; set: Setter<C> }) {
  const progs = useApi<{ programs: string[]; running: string[] }>(`/api/guilds/${guildId}/mind/pc-programs`, [guildId]);
  const upd = (i: number, patch: Partial<{ name: string; target: string }>) =>
    set("pc_programs", d.pc_programs.map((p, j) => (j === i ? { ...p, ...patch } : p)) as C["pc_programs"]);
  return (
    <Card title="Компьютер владельца" subtitle="«Император, открой стим» / «закрой дискорд» — в чате, голосом или в личке. Слушается только доверенных людей и владельца (OWNER_ID). Открывает только установленные программы и то, что вы добавили ниже; закрывает запущенные, но системные процессы и сам бот защищены. Произвольные команды не выполняет.">
      <ToggleRow title="Управлять программами на компьютере" checked={d.pc_control} onChange={(v) => set("pc_control", v)} />
      {d.pc_control && (
        <>
          <ToggleRow title="Всё из меню «Пуск»" hint="Бот сможет открыть любую программу, у которой есть ярлык в «Пуске»" checked={d.pc_start_menu} onChange={(v) => set("pc_start_menu", v)} />
          <h3 className="mt">Свои программы и игры</h3>
          <div className="small muted">Путь к .exe, .lnk или .url (например <code>C:\Games\Game\game.exe</code>) или ссылка Steam <code>steam://rungameid/376210</code>.</div>
          <div className="stack mt" style={{ gap: 8 }}>
            {d.pc_programs.map((p, i) => (
              <div key={i} className="row">
                <input className="input" style={{ maxWidth: 200 }} placeholder="Название" value={p.name} onChange={(e) => upd(i, { name: e.target.value })} />
                <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="Путь или steam://…" value={p.target} onChange={(e) => upd(i, { target: e.target.value.trim() })} />
                <button className="btn btn-sm btn-ghost" onClick={() => set("pc_programs", d.pc_programs.filter((_, j) => j !== i) as C["pc_programs"])} aria-label="Удалить"><Icon name="trash" size={14} /></button>
              </div>
            ))}
          </div>
          {d.pc_programs.length < 50 && <button className="btn btn-sm mt" onClick={() => set("pc_programs", [...d.pc_programs, { name: "", target: "" }] as C["pc_programs"])}><Icon name="plus" size={14} />Добавить программу</button>}
          {progs.data && (
            <details className="mt">
              <summary className="small muted">Что бот может открыть сейчас ({progs.data.programs.length}) и что запущено ({progs.data.running.length})</summary>
              <div className="small mt"><b>Открыть:</b> {progs.data.programs.join(", ") || "ничего"}</div>
              <div className="small mt"><b>Запущено:</b> {progs.data.running.join(", ") || "—"}</div>
            </details>
          )}
        </>
      )}
    </Card>
  );
}

export function FamilyCard<C extends MindConfig>({ guildId, d, set }: { guildId: string; d: C; set: Setter<C> }) {
  type F = MindConfig["family"][number];
  const upd = (i: number, patch: Partial<F>) => set("family", d.family.map((f, j) => (j === i ? { ...f, ...patch } : f)) as C["family"]);
  return (
    <Card title="Семья" subtitle="Создатели бота, к которым он относится по-особому. Узнаёт их только по аккаунту Discord (выбранному участнику или логину), а не по нику и не по словам — самозванцев высмеивает. Семью бот не наказывает, не игнорит и слушается в управлении компьютером.">
      <div className="stack" style={{ gap: 14 }}>
        {d.family.map((f, i) => (
          <div key={i} className="stack" style={{ gap: 6, paddingBottom: 12, borderBottom: "1px solid var(--border)" }}>
            <div className="row">
              <input className="input" style={{ maxWidth: 180 }} placeholder="Кто он боту: отец…" value={f.title} onChange={(e) => upd(i, { title: e.target.value })} />
              {f.user_id
                ? <MemberChip guildId={guildId} id={f.user_id} onRemove={() => upd(i, { user_id: null } as Partial<F>)} />
                : <input className="input" style={{ maxWidth: 200 }} placeholder="логин Discord" value={f.username} onChange={(e) => upd(i, { username: e.target.value.trim().toLowerCase() } as Partial<F>)} />}
              <span className="spacer" />
              <button className="btn btn-sm btn-ghost" onClick={() => set("family", d.family.filter((_, j) => j !== i) as C["family"])} aria-label="Удалить"><Icon name="trash" size={14} /></button>
            </div>
            {!f.user_id && <MemberSearch guildId={guildId} exclude={[]} onAdd={(id) => upd(i, { user_id: id, username: "" } as Partial<F>)} />}
            <textarea className="textarea" rows={4} maxLength={2000} placeholder="Как бот к нему относится" value={f.prompt} onChange={(e) => upd(i, { prompt: e.target.value })} />
          </div>
        ))}
      </div>
      {d.family.length < 10 && <button className="btn btn-sm mt" onClick={() => set("family", [...d.family, { user_id: null, username: "", title: "", prompt: "" }] as C["family"])}><Icon name="plus" size={14} />Добавить члена семьи</button>}
    </Card>
  );
}
