import { useState } from "react";
import { useParams } from "react-router-dom";
import { useMeta } from "../App";
import { useModuleConfig, usePreview, useUnsavedWarning } from "../hooks";
import { CardEditor } from "../cardEditor";
import type { CardConfig, EmbedConfig } from "../types";
import { Card, ChannelSelect, DiscordPreview, EmbedEditor, ErrorBox, Field, Loading, PageHeader, SaveBar, Tabs, TemplateInput, ToggleRow } from "../ui";

interface Greeting { enabled: boolean; channel_id: string | null; message: string; embed: EmbedConfig; card: CardConfig }
interface WelcomeConfig { join: Greeting; leave: Greeting; dm_enabled: boolean; dm_message: string }

const VARS = ["user", "username", "display_name", "user_id", "server", "member_count", "choose"];

function GreetingEditor({ guildId, value, onChange, kind }: { guildId: string; value: Greeting; onChange: (fn: (g: Greeting) => void) => void; kind: "join" | "leave" }) {
  const meta = useMeta();
  const vars = Object.fromEntries(Object.entries(meta.template_variables).filter(([k]) => VARS.includes(k)));
  const preview = usePreview(guildId, value.message, value.embed);
  const errors: string[] = [];
  if (value.enabled && !value.channel_id) errors.push("Выберите канал");
  if (value.enabled && !value.message.trim() && !value.embed.enabled && !value.card.enabled) errors.push("Заполните текст, включите embed или картинку-баннер");
  return (
    <div className="stack">
      <Card>
        <ToggleRow title={kind === "join" ? "Приветствовать новых участников" : "Сообщать о выходе участников"}
          checked={value.enabled} onChange={(v) => onChange((g) => { g.enabled = v; })} />
        <div className="mt" style={{ maxWidth: 420 }}>
          <Field label="Канал"><ChannelSelect value={value.channel_id} onChange={(v) => onChange((g) => { g.channel_id = v; })} /></Field>
        </div>
        {errors.map((e) => <div key={e} className="alert alert-err mt">{e}</div>)}
      </Card>
      <div className="grid grid-2">
        <Card title="Сообщение">
          <div className="stack">
            <Field label="Текст"><TemplateInput value={value.message} vars={vars} onChange={(v) => onChange((g) => { g.message = v; })} /></Field>
            <EmbedEditor value={value.embed} vars={vars} onChange={(e) => onChange((g) => { g.embed = e; })} />
          </div>
        </Card>
        <Card title="Предпросмотр" subtitle="Переменные подставляются так же, как это сделает бот">
          <DiscordPreview data={preview.data} loading={preview.loading}>
            {value.card.enabled && !value.embed.enabled && <div className="small muted mt">+ картинка-баннер (предпросмотр ниже)</div>}
          </DiscordPreview>
        </Card>
      </div>
      <Card title="Картинка с аватаркой">
        <CardEditor guildId={guildId} kind={kind} value={value.card} vars={vars} onChange={(card) => onChange((g) => { g.card = card; })} />
      </Card>
    </div>
  );
}

export default function Welcome() {
  const { guildId } = useParams();
  const meta = useMeta();
  const [tab, setTab] = useState<"join" | "leave" | "dm">("join");
  const c = useModuleConfig<WelcomeConfig>(guildId!, "welcome");
  useUnsavedWarning(c.dirty);
  const dmPreview = usePreview(guildId, c.draft?.dm_message ?? "", null);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  const vars = Object.fromEntries(Object.entries(meta.template_variables).filter(([k]) => VARS.includes(k)));
  return (
    <>
      <PageHeader title="Приветствия и прощания" subtitle="Сообщения при вступлении и выходе участников. Требуется intent «Server Members»." />
      <Tabs tabs={[["join", "Приветствие"], ["leave", "Выход"], ["dm", "Личное сообщение"]]} value={tab} onChange={setTab} />
      {tab === "join" && <GreetingEditor guildId={guildId!} kind="join" value={d.join} onChange={(fn) => c.update((x) => fn(x.join))} />}
      {tab === "leave" && <GreetingEditor guildId={guildId!} kind="leave" value={d.leave} onChange={(fn) => c.update((x) => fn(x.leave))} />}
      {tab === "dm" && (
        <div className="grid grid-2">
          <Card title="Приветствие в личных сообщениях" subtitle="Не доставляется, если у участника закрыты личные сообщения">
            <ToggleRow title="Отправлять личное сообщение новичкам" checked={d.dm_enabled} onChange={(v) => c.update((x) => { x.dm_enabled = v; })} />
            <div className="mt"><Field label="Текст"><TemplateInput value={d.dm_message} vars={vars} onChange={(v) => c.update((x) => { x.dm_message = v; })} /></Field></div>
          </Card>
          <Card title="Предпросмотр"><DiscordPreview data={dmPreview.data} loading={dmPreview.loading} /></Card>
        </div>
      )}
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
