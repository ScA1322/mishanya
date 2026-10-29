import { useParams } from "react-router-dom";
import { useModuleConfig, useUnsavedWarning } from "../hooks";
import { Card, ErrorBox, Field, Loading, MultiRoleSelect, NumberInput, PageHeader, SaveBar, TagsInput, ToggleRow } from "../ui";

interface AutorolesConfig { enabled: boolean; roles: string[]; bot_roles: string[]; excluded_users: string[]; delay_seconds: number }

export default function Autoroles() {
  const { guildId } = useParams();
  const c = useModuleConfig<AutorolesConfig>(guildId!, "autoroles");
  useUnsavedWarning(c.dirty);
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  return (
    <>
      <PageHeader title="Автоматические роли" subtitle="Роли, которые выдаются при вступлении на сервер. Ручная выдача — командой /role add." />
      <div className="stack">
        <Card>
          <ToggleRow title="Автороли включены" checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
          <div className="mt" style={{ maxWidth: 320 }}>
            <Field label="Задержка перед выдачей, сек" hint="Полезно, если на сервере включена проверка участников (0 — сразу)">
              <NumberInput value={d.delay_seconds} min={0} max={3600} onChange={(v) => c.update((x) => { x.delay_seconds = v; })} />
            </Field>
          </div>
        </Card>
        <div className="grid grid-2">
          <Card title="Роли для участников" subtitle={`До 20 ролей · выбрано ${d.roles.length}`}>
            <MultiRoleSelect assignableOnly value={d.roles} onChange={(v) => c.update((x) => { x.roles = v; })} />
          </Card>
          <Card title="Роли для ботов" subtitle="Выдаются вместо обычных, если вступил бот">
            <MultiRoleSelect assignableOnly value={d.bot_roles} onChange={(v) => c.update((x) => { x.bot_roles = v; })} />
          </Card>
        </div>
        <Card title="Исключённые участники" subtitle="Этим пользователям автороли не выдаются (Discord ID)">
          <TagsInput value={d.excluded_users} onChange={(v) => c.update((x) => { x.excluded_users = v; })} placeholder="123456789012345678" pattern={/^\d{15,22}$/} />
        </Card>
        <div className="alert alert-info">
          Роль Mishanya должна стоять выше выдаваемых ролей, а у бота должно быть право «Управлять ролями». Роли с пометкой
          «бот не может выдать» или удалённые роли пропускаются, а предупреждение появится на странице «Диагностика».
        </div>
      </div>
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
