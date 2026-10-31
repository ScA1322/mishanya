import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { errorText, post } from "../api";
import { useApi, useModuleConfig, useUnsavedWarning } from "../hooks";
import { Icon, type IconName } from "../icons";
import { Card, Empty, ErrorBox, Field, Loading, MultiRoleSelect, NumberInput, PageHeader, SaveBar, ToggleRow, useToast } from "../ui";

interface MusicConfig { enabled: boolean; dj_roles: string[]; default_volume: number; max_queue: number; max_track_minutes: number; idle_minutes: number }
interface Track { title: string; url: string; artist: string; duration: number | null; thumbnail: string | null; requester_id: string | null }
interface MusicState {
  bot: boolean; connected: boolean; channel: string | null; playing: boolean; paused: boolean; current: Track | null; elapsed: number;
  queue: Track[]; loop: "off" | "track" | "queue"; volume: number | null;
}

const LOOP: Record<MusicState["loop"], string> = { off: "выключен", track: "текущий трек", queue: "вся очередь" };

const dur = (s: number | null) => {
  if (!s) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
};

function Player({ guildId }: { guildId: string }) {
  const toast = useToast();
  const { data, error, reload, setData } = useApi<MusicState>(`/api/guilds/${guildId}/music`, [guildId]);
  useEffect(() => {
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [reload]);

  const act = async (action: string) => {
    try { setData(await post<MusicState>(`/api/guilds/${guildId}/music/${action}`)); } catch (e) { toast("err", errorText(e)); }
  };
  const btn = (action: string, icon: IconName, label: string, disabled = false) => (
    <button className="btn btn-sm" disabled={disabled} onClick={() => act(action)}><Icon name={icon} size={14} />{label}</button>
  );

  if (error) return <Card title="Сейчас играет"><ErrorBox error={error} onRetry={reload} /></Card>;
  if (!data) return <Card title="Сейчас играет"><Loading /></Card>;
  return (
    <Card title="Сейчас играет" subtitle={data.connected ? `Голосовой канал: ${data.channel}` : undefined}>
      {!data.bot ? <Empty icon="🔌">Бот не подключён к Discord</Empty> : !data.current ? (
        <Empty icon="🎵">Ничего не играет. Зайдите в голосовой канал и напишите <code>/play название трека</code></Empty>
      ) : (
        <div className="stack">
          <div className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
            {data.current.thumbnail && <img src={data.current.thumbnail} alt="" width={72} height={72} style={{ borderRadius: 8, objectFit: "cover" }} />}
            <div style={{ minWidth: 0 }}>
              <a href={data.current.url} target="_blank" rel="noreferrer"><strong>{data.current.title}</strong></a>
              <div className="small muted">{data.current.artist} · {dur(data.elapsed)} / {dur(data.current.duration)}{data.paused ? " · пауза" : ""}</div>
              <div className="small muted">Повтор: {LOOP[data.loop]} · громкость {data.volume ?? "—"}%</div>
            </div>
          </div>
          <div className="row">
            {data.paused ? btn("resume", "play", "Продолжить") : btn("pause", "pause", "Пауза")}
            {btn("skip", "skip", "Пропустить")}
            {btn("shuffle", "shuffle", "Перемешать", data.queue.length < 2)}
            {btn("stop", "square", "Остановить")}
            {btn("leave", "logout", "Отключить")}
          </div>
        </div>
      )}
      {data.connected && !data.current && data.bot && <div className="row mt">{btn("leave", "logout", "Отключить бота от канала")}</div>}
      {data.queue.length > 0 && (
        <div className="table-wrap mt">
          <table>
            <thead><tr><th>#</th><th>Трек</th><th>Длительность</th></tr></thead>
            <tbody>
              {data.queue.slice(0, 50).map((t, i) => (
                <tr key={i}><td>{i + 1}</td><td><a href={t.url} target="_blank" rel="noreferrer">{t.title}</a><div className="small muted">{t.artist}</div></td><td>{dur(t.duration)}</td></tr>
              ))}
            </tbody>
          </table>
          {data.queue.length > 50 && <div className="small muted mt">…и ещё {data.queue.length - 50}</div>}
        </div>
      )}
    </Card>
  );
}

export default function Music() {
  const { guildId } = useParams();
  const c = useModuleConfig<MusicConfig>(guildId!, "music");
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  return (
    <>
      <PageHeader title="Музыка" subtitle="Треки и плейлисты с SoundCloud в голосовых каналах: /play, /skip, /queue, /loop, /volume и другие." />
      <div className="stack">
        <Player guildId={guildId!} />
        <Card title="Настройки">
          <ToggleRow title="Музыка включена" checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
          <div className="grid grid-2 mt">
            <Field label="Роли диджея" hint="Если заданы, пропускать чужие треки, останавливать и менять громкость могут только они. Пусто — все в канале.">
              <MultiRoleSelect value={d.dj_roles} onChange={(v) => c.update((x) => { x.dj_roles = v; })} />
            </Field>
            <Field label="Громкость по умолчанию, %"><NumberInput value={d.default_volume} min={1} max={200} onChange={(v) => c.update((x) => { x.default_volume = v; })} /></Field>
            <Field label="Максимум треков в очереди"><NumberInput value={d.max_queue} min={1} max={500} onChange={(v) => c.update((x) => { x.max_queue = v; })} /></Field>
            <Field label="Максимальная длина трека, мин" hint="0 — без ограничения"><NumberInput value={d.max_track_minutes} min={0} max={600} onChange={(v) => c.update((x) => { x.max_track_minutes = v; })} /></Field>
            <Field label="Выходить из канала после простоя, мин"><NumberInput value={d.idle_minutes} min={1} max={120} onChange={(v) => c.update((x) => { x.idle_minutes = v; })} /></Field>
          </div>
        </Card>
      </div>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
