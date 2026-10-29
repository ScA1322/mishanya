import type { ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useMeta } from "../App";
import { useModuleConfig, useUnsavedWarning } from "../hooks";
import {
  Card, ChannelSelect, ErrorBox, Field, Loading, MultiChannelSelect, MultiRoleSelect, NumberInput, PageHeader, SaveBar, TagsInput,
  TemplateInput, Toggle, ToggleRow,
} from "../ui";

interface RuleBase { enabled: boolean; delete_message: boolean; warn_user: boolean; add_warning: boolean; exempt_roles: string[]; exempt_channels: string[] }
interface AutomodConfig {
  enabled: boolean; ignore_moderators: boolean; exempt_roles: string[]; exempt_channels: string[]; log_channel_id: string | null;
  warn_message: string;
  spam: RuleBase & { max_messages: number; per_seconds: number };
  flood: RuleBase & { max_lines: number; max_length: number; max_repeated_chars: number };
  duplicates: RuleBase & { max_duplicates: number; per_seconds: number };
  mentions: RuleBase & { max_mentions: number; block_everyone: boolean };
  words: RuleBase & { words: string[]; match_mode: "word" | "contains" };
  links: RuleBase & { allowed_domains: string[] };
  invites: RuleBase & { allowed_codes: string[] };
  escalation: { enabled: boolean; violations: number; window_minutes: number; timeout_minutes: number };
}
type RuleKey = "spam" | "flood" | "duplicates" | "mentions" | "words" | "links" | "invites";

const RULE_HINTS: Record<RuleKey, string> = {
  spam: "Слишком много сообщений за короткое время",
  flood: "Стена текста, много строк или повторяющиеся символы",
  duplicates: "Одно и то же сообщение несколько раз подряд",
  mentions: "Много упоминаний в одном сообщении, @everyone/@here",
  words: "Сообщения со словами из списка",
  links: "Любые ссылки, кроме разрешённых доменов",
  invites: "Приглашения на другие Discord-серверы",
};

function Rule({ name, hint, rule, onChange, children }: {
  name: string; hint: string; rule: RuleBase; onChange: (fn: (r: RuleBase) => void) => void; children?: ReactNode;
}) {
  return (
    <Card title={<span className="row">{name} {rule.enabled ? <span className="badge badge-ok">включено</span> : <span className="badge">выключено</span>}</span>}
      subtitle={hint} actions={<Toggle checked={rule.enabled} onChange={(v) => onChange((r) => { r.enabled = v; })} label={name} />}>
      {rule.enabled && (
        <div className="stack">
          {children && <div className="grid grid-3">{children}</div>}
          <div>
            <ToggleRow title="Удалять сообщение" checked={rule.delete_message} onChange={(v) => onChange((r) => { r.delete_message = v; })} />
            <ToggleRow title="Предупреждать в канале" hint="Короткое сообщение, удаляется через 10 секунд" checked={rule.warn_user} onChange={(v) => onChange((r) => { r.warn_user = v; })} />
            <ToggleRow title="Выдавать предупреждение (/warn)" hint="Учитывается в автоматических действиях модерации" checked={rule.add_warning} onChange={(v) => onChange((r) => { r.add_warning = v; })} />
          </div>
          <details>
            <summary className="small muted" style={{ cursor: "pointer" }}>Исключения для этого правила ({rule.exempt_roles.length + rule.exempt_channels.length})</summary>
            <div className="grid grid-2 mt">
              <Field label="Роли"><MultiRoleSelect value={rule.exempt_roles} onChange={(v) => onChange((r) => { r.exempt_roles = v; })} /></Field>
              <Field label="Каналы"><MultiChannelSelect value={rule.exempt_channels} onChange={(v) => onChange((r) => { r.exempt_channels = v; })} /></Field>
            </div>
          </details>
        </div>
      )}
    </Card>
  );
}

export default function Automod() {
  const { guildId } = useParams();
  const meta = useMeta();
  const c = useModuleConfig<AutomodConfig>(guildId!, "automod");
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  const rule = (k: RuleKey) => (fn: (r: RuleBase) => void) => c.update((x) => fn(x[k]));
  const set = <K extends RuleKey>(k: K, fn: (r: AutomodConfig[K]) => void) => c.update((x) => fn(x[k]));
  const vars = { user: meta.template_variables.user, username: meta.template_variables.username, rule: "Название нарушенного правила" };

  return (
    <>
      <PageHeader title="Автомодерация" subtitle="Автоматическая проверка сообщений. По умолчанию все правила выключены — включайте только нужные." />
      <div className="stack">
        <Card>
          <ToggleRow title="Автомодерация включена" checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
          <ToggleRow title="Не проверять модераторов" hint="Участники с правом «Управлять сообщениями», «Управлять сервером» или администраторы"
            checked={d.ignore_moderators} onChange={(v) => c.update((x) => { x.ignore_moderators = v; })} />
          <div className="grid grid-2 mt">
            <Field label="Канал журнала автомодерации"><ChannelSelect value={d.log_channel_id} onChange={(v) => c.update((x) => { x.log_channel_id = v; })} placeholder="— не отправлять —" /></Field>
            <Field label="Текст предупреждения в канале"><TemplateInput rows={2} maxLength={500} value={d.warn_message} vars={vars} onChange={(v) => c.update((x) => { x.warn_message = v; })} /></Field>
          </div>
          <div className="grid grid-2 mt">
            <Field label="Доверенные роли" hint="Не проверяются никакими правилами"><MultiRoleSelect value={d.exempt_roles} onChange={(v) => c.update((x) => { x.exempt_roles = v; })} /></Field>
            <Field label="Исключённые каналы"><MultiChannelSelect value={d.exempt_channels} onChange={(v) => c.update((x) => { x.exempt_channels = v; })} /></Field>
          </div>
        </Card>

        <Card title="Наказание за повторные нарушения" subtitle="Тайм-аут, если участник нарушил правила несколько раз за короткое время"
          actions={<Toggle checked={d.escalation.enabled} onChange={(v) => c.update((x) => { x.escalation.enabled = v; })} label="Наказание" />}>
          {d.escalation.enabled && (
            <div className="grid grid-3">
              <Field label="Нарушений"><NumberInput value={d.escalation.violations} min={1} max={100} onChange={(v) => c.update((x) => { x.escalation.violations = v; })} /></Field>
              <Field label="За период, мин"><NumberInput value={d.escalation.window_minutes} min={1} max={1440} onChange={(v) => c.update((x) => { x.escalation.window_minutes = v; })} /></Field>
              <Field label="Тайм-аут, мин"><NumberInput value={d.escalation.timeout_minutes} min={1} max={40320} onChange={(v) => c.update((x) => { x.escalation.timeout_minutes = v; })} /></Field>
            </div>
          )}
        </Card>

        <Rule name={meta.automod_rules.spam} hint={RULE_HINTS.spam} rule={d.spam} onChange={rule("spam")}>
          <Field label="Больше сообщений, чем"><NumberInput value={d.spam.max_messages} min={2} max={50} onChange={(v) => set("spam", (r) => { r.max_messages = v; })} /></Field>
          <Field label="За секунд"><NumberInput value={d.spam.per_seconds} min={1} max={120} onChange={(v) => set("spam", (r) => { r.per_seconds = v; })} /></Field>
        </Rule>
        <Rule name={meta.automod_rules.flood} hint={RULE_HINTS.flood} rule={d.flood} onChange={rule("flood")}>
          <Field label="Максимум строк"><NumberInput value={d.flood.max_lines} min={2} max={500} onChange={(v) => set("flood", (r) => { r.max_lines = v; })} /></Field>
          <Field label="Максимум символов"><NumberInput value={d.flood.max_length} min={50} max={4000} onChange={(v) => set("flood", (r) => { r.max_length = v; })} /></Field>
          <Field label="Повтор одного символа подряд"><NumberInput value={d.flood.max_repeated_chars} min={5} max={2000} onChange={(v) => set("flood", (r) => { r.max_repeated_chars = v; })} /></Field>
        </Rule>
        <Rule name={meta.automod_rules.duplicates} hint={RULE_HINTS.duplicates} rule={d.duplicates} onChange={rule("duplicates")}>
          <Field label="Одинаковых сообщений"><NumberInput value={d.duplicates.max_duplicates} min={2} max={50} onChange={(v) => set("duplicates", (r) => { r.max_duplicates = v; })} /></Field>
          <Field label="За секунд"><NumberInput value={d.duplicates.per_seconds} min={5} max={3600} onChange={(v) => set("duplicates", (r) => { r.per_seconds = v; })} /></Field>
        </Rule>
        <Rule name={meta.automod_rules.mentions} hint={RULE_HINTS.mentions} rule={d.mentions} onChange={rule("mentions")}>
          <Field label="Максимум упоминаний"><NumberInput value={d.mentions.max_mentions} min={1} max={100} onChange={(v) => set("mentions", (r) => { r.max_mentions = v; })} /></Field>
          <Field label="@everyone / @here">
            <select className="select" value={String(d.mentions.block_everyone)} onChange={(e) => set("mentions", (r) => { r.block_everyone = e.target.value === "true"; })}>
              <option value="true">Считать нарушением</option><option value="false">Разрешать</option>
            </select>
          </Field>
        </Rule>
        <Rule name={meta.automod_rules.words} hint={RULE_HINTS.words} rule={d.words} onChange={rule("words")}>
          <div style={{ gridColumn: "1 / -1" }} className="stack">
            <Field label="Режим поиска">
              <select className="select" style={{ maxWidth: 360 }} value={d.words.match_mode} onChange={(e) => set("words", (r) => { r.match_mode = e.target.value as "word" | "contains"; })}>
                <option value="word">Целое слово (меньше ложных срабатываний)</option>
                <option value="contains">Вхождение в любом месте</option>
              </select>
            </Field>
            <Field label={`Запрещённые слова и фразы (${d.words.words.length})`} hint="Регистр не учитывается. Можно вставить список через запятую.">
              <TagsInput value={d.words.words} onChange={(v) => set("words", (r) => { r.words = v.map((w) => w.toLowerCase()); })} placeholder="слово или фраза" />
            </Field>
          </div>
        </Rule>
        <Rule name={meta.automod_rules.links} hint={RULE_HINTS.links} rule={d.links} onChange={rule("links")}>
          <div style={{ gridColumn: "1 / -1" }}>
            <Field label="Разрешённые домены" hint="Поддомены разрешаются автоматически: youtube.com разрешит и m.youtube.com">
              <TagsInput value={d.links.allowed_domains} onChange={(v) => set("links", (r) => { r.allowed_domains = v; })} placeholder="example.com"
                pattern={/^(https?:\/\/)?[a-z0-9.-]+\.[a-z]{2,}(\/.*)?$/i} />
            </Field>
          </div>
        </Rule>
        <Rule name={meta.automod_rules.invites} hint={RULE_HINTS.invites} rule={d.invites} onChange={rule("invites")}>
          <div style={{ gridColumn: "1 / -1" }}>
            <Field label="Разрешённые коды приглашений" hint="Например, код вашего сервера из ссылки discord.gg/КОД">
              <TagsInput value={d.invites.allowed_codes} onChange={(v) => set("invites", (r) => { r.allowed_codes = v; })} placeholder="код" pattern={/^[A-Za-z0-9-]+$/} />
            </Field>
          </div>
        </Rule>
      </div>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
