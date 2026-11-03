import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { errorText, post, postBlob, put } from "../api";
import { useMeta } from "../App";
import { useApi, useModuleConfig, useUnsavedWarning } from "../hooks";
import { Icon } from "../icons";
import { CharacterCard, ConfidantsCard, FamilyCard, PcCard, DialogCard, GrudgeCard, MemoryCard, type MindConfig, ModelsCard, MoveCard, TriggersCard } from "./ChatterMind";
import { Card, ChannelSelect, ErrorBox, Field, Loading, MultiChannelSelect, NumberInput, PageHeader, Picker, SaveBar, ToggleRow, useResources, useToast } from "../ui";

interface ChatterConfig extends MindConfig {
  enabled: boolean; channels: string[]; chance_percent: number; min_messages: number; cooldown_minutes: number; max_per_hour: number;
  reply_on_mention: boolean; reply_to_message: boolean; use_ai: boolean; system_prompt: string; context_messages: number;
  temperature: number; max_tokens: number; phrases: string[]; fallback_to_phrases: boolean;
  voice_enabled: boolean; voice_channels: string[]; voice_interval_minutes: number; voice_max_per_hour: number;
  voice_min_members: number; voice_lines: number; voice_engine: "auto" | "edge" | "google" | "elevenlabs" | "openai"; voice_eleven_id: string; voice_openai: string; voice_name: "dmitry" | "svetlana";
  voice_rate: number; voice_pitch: number;
  listen_wake_words: string[]; listen_followup_seconds: number; listen_min_seconds: number; listen_max_minutes: number; listen_auto: boolean;
}
interface Status { ai_configured: boolean; key_hint: string | null; model: string; base_url: string; vision_model: string; last_error: string | null }
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

function VoiceChannelPicker({ value, onChange }: { value: string | null; onChange: (v: string | null) => void }) {
  const { channels, connected } = useResources();
  const items = useMemo(() => channels.filter((c) => c.type === "voice")
    .map((c) => ({ id: c.id, label: c.name, group: c.category ?? null, icon: "volume" as const })), [channels]);
  return <Picker items={items} value={value} onChange={onChange} placeholder="— голосовой канал —" searchPlaceholder="Поиск канала…"
    emptyText={connected ? "Голосовых каналов нет" : "Бот не подключён"} />;
}

function VoiceChannelsMulti({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const { channels } = useResources();
  const name = (id: string) => channels.find((c) => c.id === id)?.name ?? `канал ${id}`;
  return (
    <div className="chips">
      {value.map((id) => (
        <span key={id} className="chip"><Icon name="volume" size={12} className="muted" />{name(id)}
          <button type="button" onClick={() => onChange(value.filter((x) => x !== id))} aria-label="Убрать"><Icon name="x" size={12} /></button></span>
      ))}
      <VoiceAdd exclude={value} onAdd={(id) => onChange([...value, id])} />
    </div>
  );
}

function VoiceAdd({ exclude, onAdd }: { exclude: string[]; onAdd: (id: string) => void }) {
  const { channels } = useResources();
  const items = channels.filter((c) => c.type === "voice" && !exclude.includes(c.id))
    .map((c) => ({ id: c.id, label: c.name, group: c.category ?? null, icon: "volume" as const }));
  return <Picker items={items} value={null} trigger={<><Icon name="plus" size={14} />Канал</>} searchPlaceholder="Поиск канала…"
    emptyText="Голосовых каналов нет" onChange={(v) => v && onAdd(v)} />;
}

const ELEVEN_VOICES: [string, string][] = [
  ["pNInz6obpgDQGcFmaJgB", "Adam — низкий, уверенный"],
  ["TxGEqnHWrfWFTfGW9XjX", "Josh — молодой, живой"],
  ["VR6AewLTigWG4xSOukaG", "Arnold — грубый, резкий"],
  ["ErXwobaYiN019PkySvjV", "Antoni — спокойный"],
  ["yoZ06aMxZJJ28mfd3POQ", "Sam — хрипловатый"],
];

function ElevenKey() {
  const toast = useToast();
  const st = useApi<{ eleven_configured: boolean }>("/api/ai/elevenlabs-status", []);
  const [key, setKey] = useState("");
  const save = async (value: string) => {
    try { await put("/api/ai/elevenlabs-key", { key: value.trim() }); toast("ok", value ? "Ключ ElevenLabs сохранён" : "Ключ удалён"); setKey(""); st.reload(); }
    catch (e) { toast("err", errorText(e)); }
  };
  return (
    <div className="mt">
      {st.data?.eleven_configured
        ? <div className="row"><span className="badge badge-ok">ключ ElevenLabs задан</span><span className="spacer" /><button className="btn btn-sm btn-danger" onClick={() => save("")}>Удалить</button></div>
        : <div className="alert alert-warn">Нужен ключ ElevenLabs: elevenlabs.io → Profile → API Keys. Бесплатно ~20 тысяч символов в месяц. Пока ключа нет, бот говорит голосом Microsoft.</div>}
      <div className="row mt" style={{ alignItems: "flex-end" }}>
        <Field label={st.data?.eleven_configured ? "Заменить ключ" : "Ключ ElevenLabs"} hint="Хранится только в .env на этом компьютере">
          <input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} style={{ minWidth: 320 }} />
        </Field>
        <button className="btn btn-primary" disabled={key.trim().length < 10} onClick={() => save(key)}>Сохранить ключ</button>
      </div>
    </div>
  );
}

function VoiceCard({ guildId, d, set }: {
  guildId: string; d: ChatterConfig; set: <K extends keyof ChatterConfig>(k: K, v: ChatterConfig[K]) => void;
}) {
  const toast = useToast();
  const [text, setText] = useState("так, холопы, Император зашёл в канал. всем встать");
  const [audio, setAudio] = useState<string | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const [busy, setBusy] = useState<"listen" | "say" | null>(null);
  useEffect(() => () => { if (audio) URL.revokeObjectURL(audio); }, [audio]);

  const listen = async () => {
    setBusy("listen");
    try {
      const blob = await postBlob(`/api/guilds/${guildId}/chatter/tts-preview`, { text, config: d });
      const url = URL.createObjectURL(blob);
      setAudio(url);
      new Audio(url).play().catch(() => undefined);
    } catch (e) { toast("err", errorText(e)); }
    finally { setBusy(null); }
  };
  const say = async (own: boolean) => {
    if (!channel) { toast("err", "Выберите голосовой канал"); return; }
    setBusy("say");
    try {
      const r = await post<{ said: string[] }>(`/api/guilds/${guildId}/chatter/voice-say`, { config: d, channel_id: channel, text: own ? text : "" });
      toast("ok", r.said.length ? `Сказал: ${r.said.join(" / ")}` : "Нечего сказать — список фраз пуст");
    } catch (e) { toast("err", errorText(e)); }
    finally { setBusy(null); }
  };

  return (
    <Card title="Болтовня в голосовых каналах" subtitle="Бот иногда сам заходит в голосовой канал, где сидят люди, говорит голосом пару фраз в своём характере и выходит. Слышать собеседников он не может — говорит по тому, кто сидит в канале и что пишут в его чате.">
      <ToggleRow title="Заходить в голосовые каналы" hint="Работает, только если «Болтовня» включена. Во время музыки бот не заходит."
        checked={d.voice_enabled} onChange={(v) => set("voice_enabled", v)} />
      <div className="grid grid-2 mt">
        <Field label="В какие каналы" hint="Пусто — в любой, где есть люди (кроме AFK)"><VoiceChannelsMulti value={d.voice_channels} onChange={(v) => set("voice_channels", v)} /></Field>
        <Field label="В среднем раз в, минут" hint="Если в каналах кто-то сидит"><NumberInput value={d.voice_interval_minutes} min={5} max={1440} onChange={(v) => set("voice_interval_minutes", v)} /></Field>
        <Field label="Не больше заходов в час"><NumberInput value={d.voice_max_per_hour} min={1} max={12} onChange={(v) => set("voice_max_per_hour", v)} /></Field>
        <Field label="Минимум людей в канале"><NumberInput value={d.voice_min_members} min={1} max={50} onChange={(v) => set("voice_min_members", v)} /></Field>
        <Field label="Фраз за один заход"><NumberInput value={d.voice_lines} min={1} max={5} onChange={(v) => set("voice_lines", v)} /></Field>
      </div>

      <h3 className="mt">Голос</h3>
      <div className="grid grid-2 mt">
        <Field label="Озвучка" hint="ElevenLabs — самая быстрая (0,1–0,3 с) и стабильная. Microsoft бесплатный, но часто не отвечает. При сбое платного движка бот переключится на Microsoft">
          <select className="input" value={d.voice_engine} onChange={(e) => set("voice_engine", e.target.value as ChatterConfig["voice_engine"])}>
            <option value="elevenlabs">ElevenLabs — быстро, мужские голоса (нужен ключ)</option>
            <option value="openai">Через ключ ИИ (OpenAI-совместимый, голос onyx и др.)</option>
            <option value="auto">Microsoft, при сбое — Google (бесплатно)</option>
            <option value="edge">Только Microsoft</option>
            <option value="google">Только Google (стабильно, женский голос)</option>
          </select>
        </Field>
        {d.voice_engine === "elevenlabs" ? (
          <Field label="Голос ElevenLabs" hint="Готовые мужские голоса или ID своего голоса из elevenlabs.io → Voices">
            <select className="input" value={ELEVEN_VOICES.some(([id]) => id === d.voice_eleven_id) ? d.voice_eleven_id : "custom"}
              onChange={(e) => e.target.value !== "custom" && set("voice_eleven_id", e.target.value)}>
              {ELEVEN_VOICES.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              <option value="custom">Свой голос (ID ниже)</option>
            </select>
            <input className="input mt" value={d.voice_eleven_id} placeholder="ID голоса" onChange={(e) => set("voice_eleven_id", e.target.value.trim())} />
          </Field>
        ) : d.voice_engine === "openai" ? (
          <Field label="Голос" hint="Мужские: onyx (низкий), echo, ash. Работает, если сервис по ключу ИИ умеет /audio/speech">
            <select className="input" value={d.voice_openai} onChange={(e) => set("voice_openai", e.target.value)}>
              {["onyx", "echo", "ash", "ballad", "verse", "alloy", "fable", "nova", "shimmer", "coral", "sage"].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </Field>
        ) : (
          <Field label="Голос Microsoft">
            <select className="input" value={d.voice_name} disabled={d.voice_engine === "google"} onChange={(e) => set("voice_name", e.target.value as ChatterConfig["voice_name"])}>
              <option value="dmitry">Дмитрий (мужской)</option>
              <option value="svetlana">Светлана (женский)</option>
            </select>
          </Field>
        )}
        <Field label={`Скорость: ${d.voice_rate > 0 ? "+" : ""}${d.voice_rate}%`}>
          <input type="range" min={-50} max={100} step={5} value={d.voice_rate} onChange={(e) => set("voice_rate", Number(e.target.value))} />
        </Field>
        <Field label={`Высота голоса: ${d.voice_pitch > 0 ? "+" : ""}${d.voice_pitch} Гц`} hint="Только для Microsoft. Ниже — грубее">
          <input type="range" min={-50} max={50} step={5} value={d.voice_pitch} disabled={d.voice_engine === "google"} onChange={(e) => set("voice_pitch", Number(e.target.value))} />
        </Field>
      </div>

      {d.voice_engine === "elevenlabs" && <ElevenKey />}

      <h3 className="mt">Попробовать</h3>
      <Field label="Текст">
        <input className="input" value={text} maxLength={400} onChange={(e) => setText(e.target.value)} />
      </Field>
      <div className="row mt">
        <button className="btn" disabled={!!busy || !text.trim()} onClick={listen}><Icon name="play" size={15} />{busy === "listen" ? "Озвучиваю…" : "Прослушать здесь"}</button>
        {audio && <audio controls src={audio} style={{ height: 36 }} />}
      </div>
      <div className="grid grid-2 mt" style={{ alignItems: "end" }}>
        <Field label="Зайти в канал и сказать"><VoiceChannelPicker value={channel} onChange={setChannel} /></Field>
        <div className="row">
          <button className="btn btn-primary" disabled={!!busy || !channel || !text.trim()} onClick={() => say(true)}><Icon name="volume" size={15} />{busy === "say" ? "Говорю…" : "Сказать этот текст"}</button>
          <button className="btn" disabled={!!busy || !channel} onClick={() => say(false)}>Пусть придумает сам</button>
        </div>
      </div>
      <div className="small muted mt">Ещё можно командой <code>/say текст</code> в Discord (для админов): бот зайдёт в ваш голосовой канал и скажет это.</div>
    </Card>
  );
}

interface ListenStatus {
  available: boolean; stt_configured: boolean; stt_model: string; stt_url: string; listening: boolean;
  channel?: string; minutes?: number; error?: string | null;
  recent?: { time: number; name: string; text: string; addressed: boolean }[];
}

function ListenCard({ guildId, d, set }: {
  guildId: string; d: ChatterConfig; set: <K extends keyof ChatterConfig>(k: K, v: ChatterConfig[K]) => void;
}) {
  const toast = useToast();
  const { data, reload, setData } = useApi<ListenStatus>(`/api/guilds/${guildId}/listen`, [guildId]);
  const [channel, setChannel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [words, setWords] = useState(d.listen_wake_words.join(", "));
  useEffect(() => {
    if (!data?.listening) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [data?.listening, reload]);

  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    try { setData(await post<ListenStatus>(`/api/guilds/${guildId}/listen/${path}`, body)); }
    catch (e) { toast("err", errorText(e)); }
    finally { setBusy(false); }
  };

  return (
    <Card title="Слушать голос (эксперимент)" subtitle="Бот сидит в голосовом канале, распознаёт речь и отвечает голосом, когда слышит слово-обращение. Ещё какое-то время после ответа он продолжает разговор без обращения.">
      {data && !data.available && <div className="alert alert-warn">Приём звука недоступен: бот не подключён или не установлены пакеты. Перезапустите бота через start.bat.</div>}
      {data && !data.stt_configured && <div className="alert alert-warn">Не задан ключ для распознавания речи — вставьте ключ выше (Inferhold подходит) или задайте STT_API_KEY в .env.</div>}
      <div className="alert alert-info">
        Пока бот слушает, <b>каждая фраза в канале</b> отправляется на распознавание ({data?.stt_model ?? "gpt-4o-transcribe"} через {data?.stt_url ?? "…"}) — это платно,
        и сказанное уходит этому сервису. Через {d.listen_max_minutes} мин бот выходит сам, а если в канале никого нет — через минуту.
      </div>

      <div className="grid grid-2 mt">
        <Field label="Слова-обращения" hint="Через запятую. Достаточно части слова: «император» сработает и на «императору»">
          <input className="input" value={words} onChange={(e) => setWords(e.target.value)}
            onBlur={() => set("listen_wake_words", words.split(",").map((w) => w.trim().toLowerCase()).filter(Boolean))} />
        </Field>
        <Field label="Разговор без обращения после ответа, сек" hint="0 — только по слову-обращению"><NumberInput value={d.listen_followup_seconds} min={0} max={300} onChange={(v) => set("listen_followup_seconds", v)} /></Field>
        <Field label="Не распознавать фразы короче, сек" hint="Отсекает кашель и «ага» — экономит деньги"><NumberInput value={d.listen_min_seconds} min={0.2} max={5} step={0.1} onChange={(v) => set("listen_min_seconds", v)} /></Field>
        <Field label="Выйти через, минут"><NumberInput value={d.listen_max_minutes} min={5} max={600} onChange={(v) => set("listen_max_minutes", v)} /></Field>
      </div>
      <div className="grid grid-2 mt">
        <Field label="Влезать в разговор без обращения" hint="«Сам решает» — бот слушает весь разговор и говорит, когда ему интересно, есть что сказать или реплика по смыслу ему (даже без слова «император»)">
          <select className="select" value={d.listen_interject_mode} onChange={(e) => set("listen_interject_mode", e.target.value as ChatterConfig["listen_interject_mode"])}>
            <option value="ai">Сам решает, когда влезть (рекомендуется)</option>
            <option value="chance">Случайно, с заданным шансом</option>
            <option value="off">Только когда к нему обращаются</option>
          </select>
        </Field>
        <Field label="Обдумывать не чаще, чем раз в, сек" hint="Меньше — живее, но дороже"><NumberInput value={d.listen_interject_seconds} min={3} max={3600} onChange={(v) => set("listen_interject_seconds", v)} /></Field>
        {d.listen_interject_mode === "chance" && (
          <Field label={`Шанс влезть: ${d.listen_interject_percent}%`}>
            <input type="range" min={0} max={100} step={5} value={d.listen_interject_percent} onChange={(e) => set("listen_interject_percent", Number(e.target.value))} />
          </Field>
        )}
        <Field label="Короткие имена — только для своих" hint="Через запятую. Доверенным и семье бот откликается и на них; чужих, кто так его назовёт, жёстко ставит на место. Работает в чате и в голосе">
          <input className="input" defaultValue={d.trusted_aliases.join(", ")} onBlur={(e) => set("trusted_aliases", e.target.value.split(",").map((w) => w.trim().toLowerCase()).filter(Boolean))} />
        </Field>
      </div>
      <ToggleRow title="Самому садиться слушать" hint="Если бот не в голосе, он сам зайдёт в канал, где больше всего людей (с учётом списка каналов и минимума людей выше)"
        checked={d.listen_auto} onChange={(v) => set("listen_auto", v)} />

      <h3 className="mt">Сейчас</h3>
      {data?.listening ? (
        <div className="stack" style={{ gap: 10 }}>
          <div className="row">
            <span className="badge badge-ok">слушает «{data.channel}»</span>
            <span className="small muted">{data.minutes} мин</span>
            <span className="spacer" />
            <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => act("stop")}>Перестать слушать</button>
          </div>
          {data.error && <div className="alert alert-err">Распознавание: {data.error}</div>}
          {data.recent && data.recent.length > 0 ? (
            <div className="stack" style={{ gap: 4 }}>
              {data.recent.map((r, i) => (
                <div key={i} className="small" style={{ opacity: r.addressed || r.name === "Mishanya" ? 1 : 0.75 }}>
                  <span className="muted">{new Date(r.time * 1000).toLocaleTimeString("ru-RU")}</span>{" "}
                  <b>{r.name}:</b> {r.text} {r.addressed && <span className="badge badge-accent">обращение</span>}
                </div>
              ))}
            </div>
          ) : <div className="small muted">Пока ничего не услышал. Скажите что-нибудь в канале.</div>}
        </div>
      ) : (
        <div className="grid grid-2" style={{ alignItems: "end" }}>
          <Field label="Зайти и слушать"><VoiceChannelPicker value={channel} onChange={setChannel} /></Field>
          <div><button className="btn btn-primary" disabled={busy || !channel} onClick={() => act("start", { channel_id: channel })}>
            <Icon name="mic" size={15} />{busy ? "Захожу…" : "Начать слушать"}</button></div>
        </div>
      )}
      <div className="small muted mt">В Discord: <code>/listen</code> — бот зайдёт в ваш канал и начнёт слушать, ещё раз <code>/listen</code> — выйдет (для админов).</div>
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
          <ToggleRow title="Отвечать, когда бота упомянули" hint="@бот, ответ на его сообщение или слово-обращение — бот ответит и продолжит разговор (настройки в блоке «Разговор»)"
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
            <Field label="Максимум вмешательств в час" hint="На весь сервер. Ответы в разговоре считаются отдельно"><NumberInput value={d.max_per_hour} min={1} max={60} onChange={(v) => set("max_per_hour", v)} /></Field>
          </div>
        </Card>

        <DialogCard d={d} set={set} />

        <KeyCard status={status.data ?? null} onChange={status.reload} />

        <ModelsCard status={status.data ?? null} onChange={status.reload} d={d} set={set} />

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

        <VoiceCard guildId={guildId!} d={d} set={set} />

        <ListenCard guildId={guildId!} d={d} set={set} />

        <TriggersCard d={d} set={set} />

        <MoveCard d={d} set={set} />

        <CharacterCard guildId={guildId!} d={d} set={set} />

        <FamilyCard guildId={guildId!} d={d} set={set} />

        <PcCard guildId={guildId!} d={d} set={set} />

        <MemoryCard guildId={guildId!} d={d} set={set} />

        <GrudgeCard guildId={guildId!} d={d} set={set} />

        <ConfidantsCard guildId={guildId!} d={d} set={set} />

        <Card title={`Фразы (${d.phrases.length})`} subtitle="Используются без ключа DeepSeek или когда он не отвечает. Одна фраза — одна строка.">
          <textarea className="textarea" rows={10} value={d.phrases.join("\n")}
            onChange={(e) => set("phrases", e.target.value.split("\n").slice(0, 200))} />
        </Card>
      </div>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
