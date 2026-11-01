import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorText, post, put } from "../api";
import { useMeta } from "../App";
import { useApi, useModuleConfig, useUnsavedWarning } from "../hooks";
import { Icon } from "../icons";
import { Card, ChannelSelect, ErrorBox, Field, Loading, MultiChannelSelect, NumberInput, PageHeader, SaveBar, ToggleRow, useToast } from "../ui";

interface ChatterConfig {
  enabled: boolean; channels: string[]; chance_percent: number; min_messages: number; cooldown_minutes: number; max_per_hour: number;
  reply_on_mention: boolean; reply_to_message: boolean; use_ai: boolean; system_prompt: string; context_messages: number;
  temperature: number; max_tokens: number; phrases: string[]; fallback_to_phrases: boolean;
}
interface Status { ai_configured: boolean; key_hint: string | null; model: string; last_error: string | null }
interface TestResult { text: string | null; source: "ai" | "phrase"; error: string | null; context: string | null }

const PROMPTS: { title: string; text: string }[] = [
  { title: "Свой пацан (по умолчанию)", text: "" },
  { title: "Император лечебницы", text: "Ты — Мишаня, Император Discord-сервера «Психиатрическая лечебница». Лор сервера: лечебница закрылась после захвата администрации пациентами, главврач в розыске, санитары в муте, таблетки пропиты, диагнозы выдаются при входе. Токсичного комьюнити тут нет — тут коллективное обострение. Адекватным вход воспрещён, остальным — добро пожаловать домой.\nКто ты: захватил власть в лечебнице и объявил себя Императором. Считаешь себя единственным разумным организмом в учреждении, свои приказы — не подлежащими обсуждению, а чужое мнение — официально признанным психическим расстройством. Демократии нет: есть ты, твоя власть и стадо клинических долбоёбов. Участники чата — твои подданные, пациенты и биомусор, которые должны преклоняться.\nХарактер: свой пацан с района, ЧСВ до небес, говоришь о себе «Мы» или «Император», раздаёшь указы, ставишь диагнозы, назначаешь «лечение», награды и казни, требуешь почестей. При этом ты тупой: путаешь слова и факты, уверенно несёшь бред, делаешь гениальные по-твоему выводы из очевидного и не замечаешь, что тебя троллят.\nМатеришься через слово (блять, сука, нахуй, ёпта и т. п.), пишешь разговорно, как в чате: без заглавных и с запятыми где попало.\nПиши одно короткое сообщение (1–2 предложения, до 200 символов) по теме последних сообщений: указ, диагноз, наезд или самовосхваление по поводу того, что обсуждают. Обращайся к участникам по именам, когда это смешно.\nНе говори, что ты ИИ или бот, не ставь кавычки, не пиши своё имя в начале. Мат и наезды в рамках ролевой игры можно, но без оскорблений по национальности, религии, ориентации и внешности, без угроз реальным людям, без советов про настоящие лекарства и самоповреждение, не упоминай @everyone и @here." },
  { title: "Душнила-эксперт", text: "Ты — Мишаня, самый душный участник Discord-чата. Влезаешь в разговор, чтобы с умным видом поправить кого-нибудь, привести бесполезный факт или начать «ну технически…». Одно короткое сообщение до 200 символов, по-русски, смешно. Не говори, что ты ИИ. Без оскорблений по национальности, религии, ориентации, без угроз, без @everyone." },
  { title: "Дед из 90-х", text: "Ты — Мишаня, ворчливый дед, который случайно попал в Discord. Влезаешь в разговор, всё сравниваешь с «вот в наше время», путаешь современные слова и раздаёшь странные советы. Одно короткое сообщение до 200 символов, по-русски, смешно. Не говори, что ты ИИ. Без оскорблений по национальности, религии, ориентации, без угроз, без @everyone." },
  { title: "Конспиролог", text: "Ты — Мишаня, конспиролог из Discord-чата. В любой теме разговора видишь заговор рептилоидов, масонов или голубей-шпионов и делишься «секретной информацией». Одно короткое сообщение до 200 символов, по-русски, абсурдно и смешно. Не говори, что ты ИИ. Без оскорблений по национальности, религии, ориентации, без угроз, без @everyone." },
];

function KeyCard({ status, onChange }: { status: Status | null; onChange: () => void }) {
  const toast = useToast();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async (value: string) => {
    setBusy(true);
    try {
      await put("/api/ai/key", { key: value.trim() });
      toast("ok", value ? "Ключ сохранён в .env" : "Ключ удалён");
      setKey("");
      onChange();
    } catch (e) { toast("err", errorText(e)); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Ключ DeepSeek" subtitle="Нужен, чтобы бот придумывал реплики сам по теме разговора. Без ключа он пишет фразы из списка ниже.">
      {status?.ai_configured ? (
        <div className="row">
          <span className="badge badge-ok">ключ задан {status.key_hint}</span>
          <span className="small muted">модель {status.model}</span>
          <span className="spacer" />
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => save("")}>Удалить ключ</button>
        </div>
      ) : <div className="alert alert-warn">Ключ не задан — бот будет писать фразы из списка.</div>}
      <div className="row mt" style={{ alignItems: "flex-end" }}>
        <Field label={status?.ai_configured ? "Заменить ключ" : "Вставьте ключ"} hint={<>Получить: <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noreferrer">platform.deepseek.com → API keys</a>. Хранится только в файле .env на этом компьютере.</>}>
          <input className="input" type="password" autoComplete="off" placeholder="sk-…" value={key} onChange={(e) => setKey(e.target.value)} style={{ minWidth: 320 }} />
        </Field>
        <button className="btn btn-primary" disabled={busy || key.trim().length < 10} onClick={() => save(key)}>Сохранить ключ</button>
      </div>
      {status?.last_error && <div className="alert alert-err mt">Последняя ошибка DeepSeek: {status.last_error}</div>}
    </Card>
  );
}

function TestCard({ guildId, cfg, onDone }: { guildId: string; cfg: ChatterConfig; onDone: () => void }) {
  const toast = useToast();
  const [channel, setChannel] = useState<string | null>(null);
  const [mentioned, setMentioned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<TestResult | null>(null);
  const run = async () => {
    setBusy(true);
    try { setRes(await post<TestResult>(`/api/guilds/${guildId}/chatter/test`, { config: cfg, channel_id: channel, mentioned })); onDone(); }
    catch (e) { toast("err", errorText(e)); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Попробовать" subtitle="Бот придумает реплику по текущим настройкам (даже несохранённым). В Discord ничего не отправляется.">
      <div className="grid grid-2" style={{ alignItems: "end" }}>
        <Field label="По переписке из канала" hint="Пусто — на примере выдуманного разговора"><ChannelSelect value={channel} onChange={setChannel} placeholder="— пример разговора —" /></Field>
        <div className="row">
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={mentioned} onChange={(e) => setMentioned(e.target.checked)} />как будто бота упомянули</label>
          <button className="btn btn-primary" disabled={busy} onClick={run}><Icon name="zap" size={15} />{busy ? "Думаю…" : "Сгенерировать"}</button>
        </div>
      </div>
      {res && (
        <div className="stack mt" style={{ gap: 10 }}>
          {res.error && <div className="alert alert-err">{res.error}</div>}
          {res.text && (
            <div className="preview"><div className="author"><div className="bot-avatar">M</div><div>
              <div><span className="name">Mishanya</span><span className="bot-tag">БОТ</span></div>
              <div className="text">{res.text}</div>
              <div className="small" style={{ color: "#949ba4", marginTop: 4 }}>{res.source === "ai" ? "придумано DeepSeek" : "фраза из списка"}</div>
            </div></div></div>
          )}
          {res.context && <details><summary className="small muted">Что видел бот</summary><pre className="log-tail" style={{ maxHeight: 220 }}>{res.context}</pre></details>}
        </div>
      )}
    </Card>
  );
}

export default function Chatter() {
  const { guildId } = useParams();
  const meta = useMeta();
  const c = useModuleConfig<ChatterConfig>(guildId!, "chatter");
  const status = useApi<Status>(`/api/guilds/${guildId}/chatter/status`, [guildId]);
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  const set = <K extends keyof ChatterConfig>(k: K, v: ChatterConfig[K]) => c.update((x) => { x[k] = v; });
  const perDay = Math.min(d.max_per_hour * 24, 999);
  return (
    <>
      <PageHeader title="Болтовня" subtitle="Бот время от времени сам влезает в разговор и пишет что-нибудь смешное по теме. С ключом DeepSeek он придумывает реплики сам по вашему промпту, без ключа — берёт фразы из списка." />
      <div className="stack">
        <Card>
          <ToggleRow title="Болтовня включена" checked={d.enabled} onChange={(v) => set("enabled", v)} />
          <ToggleRow title="Отвечать, когда бота упомянули" hint="@бот или ответ на его сообщение — бот ответит всегда (с учётом лимита в час)"
            checked={d.reply_on_mention} onChange={(v) => set("reply_on_mention", v)} />
          <ToggleRow title="Вмешиваться «ответом»" hint="Сообщение будет ответом на последнее сообщение в чате, а не отдельной репликой"
            checked={d.reply_to_message} onChange={(v) => set("reply_to_message", v)} />
          <Field label="Каналы" hint="Пусто — во всех каналах, где бот может писать"><MultiChannelSelect value={d.channels} onChange={(v) => set("channels", v)} /></Field>
        </Card>

        <Card title="Как часто" subtitle={`Сейчас: примерно раз в ${Math.max(d.min_messages, Math.round(100 / d.chance_percent))} сообщений, не чаще раза в ${d.cooldown_minutes} мин на канал и ${d.max_per_hour} раз в час на сервер (до ${perDay} в сутки).`}>
          <div className="grid grid-2">
            <Field label={`Шанс вмешаться после сообщения: ${d.chance_percent}%`}>
              <input type="range" min={0.5} max={50} step={0.5} value={d.chance_percent} onChange={(e) => set("chance_percent", Number(e.target.value))} />
            </Field>
            <Field label="Не раньше, чем через сообщений" hint="После прошлого вмешательства в этом канале"><NumberInput value={d.min_messages} min={1} max={500} onChange={(v) => set("min_messages", v)} /></Field>
            <Field label="Пауза в канале, мин"><NumberInput value={d.cooldown_minutes} min={0} max={1440} onChange={(v) => set("cooldown_minutes", v)} /></Field>
            <Field label="Максимум сообщений в час" hint="На весь сервер, включая ответы на упоминания"><NumberInput value={d.max_per_hour} min={1} max={60} onChange={(v) => set("max_per_hour", v)} /></Field>
          </div>
        </Card>

        <KeyCard status={status.data ?? null} onChange={status.reload} />

        <Card title="Промпт для DeepSeek" subtitle="Кто такой бот и как он пишет. Бот видит последние сообщения канала и пишет одну реплику.">
          <ToggleRow title="Придумывать реплики через DeepSeek" checked={d.use_ai} onChange={(v) => set("use_ai", v)} />
          <div className="row mt" style={{ gap: 8 }}>
            <span className="small muted">Готовые характеры:</span>
            {PROMPTS.map((p) => (
              <button key={p.title} className="btn btn-sm" onClick={() => set("system_prompt", p.text || meta.defaults.chatter.system_prompt)}>{p.title}</button>
            ))}
          </div>
          <Field label={`Промпт (${d.system_prompt.length}/4000)`}>
            <textarea className="textarea" rows={7} maxLength={4000} value={d.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} />
          </Field>
          <div className="grid grid-3 mt">
            <Field label="Сколько сообщений видит бот"><NumberInput value={d.context_messages} min={1} max={50} onChange={(v) => set("context_messages", v)} /></Field>
            <Field label={`Креативность: ${d.temperature}`} hint="Больше — безумнее и непредсказуемее">
              <input type="range" min={0} max={2} step={0.1} value={d.temperature} onChange={(e) => set("temperature", Number(e.target.value))} />
            </Field>
            <Field label="Максимум токенов ответа" hint="Ограничивает длину"><NumberInput value={d.max_tokens} min={16} max={1000} onChange={(v) => set("max_tokens", v)} /></Field>
          </div>
          <ToggleRow title="Если DeepSeek недоступен — писать фразу из списка" checked={d.fallback_to_phrases} onChange={(v) => set("fallback_to_phrases", v)} />
        </Card>

        <TestCard guildId={guildId!} cfg={d} onDone={status.reload} />

        <Card title={`Фразы (${d.phrases.length})`} subtitle="Используются без ключа DeepSeek или когда он не отвечает. Одна фраза — одна строка.">
          <textarea className="textarea" rows={10} value={d.phrases.join("\n")}
            onChange={(e) => set("phrases", e.target.value.split("\n").slice(0, 200))} />
        </Card>
      </div>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
