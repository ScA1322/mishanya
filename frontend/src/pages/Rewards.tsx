import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { del, errorText, patch, post } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Field, Loading, NumberInput, PageHeader, RoleName, RoleSelect, Toggle, useConfirm, useResources, useToast } from "../ui";

interface Reward { id: number; level: number; role_id: string; enabled: boolean; role_name: string | null; problem: string | null }

export default function Rewards() {
  const { guildId } = useParams();
  const toast = useToast();
  const confirm = useConfirm();
  const { connected } = useResources();
  const { data, error, loading, reload, setData } = useApi<Reward[]>(`/api/guilds/${guildId}/rewards`, [guildId]);
  const [level, setLevel] = useState(5);
  const [roleId, setRoleId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    if (!roleId) return;
    setBusy(true);
    try {
      await post(`/api/guilds/${guildId}/rewards`, { level, role_id: roleId, enabled: true });
      toast("ok", "Награда добавлена");
      setRoleId(null);
      reload();
    } catch (e) {
      toast("err", errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (r: Reward, enabled: boolean) => {
    try {
      const upd = await patch<Reward>(`/api/guilds/${guildId}/rewards/${r.id}`, { enabled });
      setData((list) => list?.map((x) => (x.id === r.id ? upd : x)) ?? null);
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  const remove = async (r: Reward) => {
    if (!(await confirm({ title: "Удалить награду?", text: `Уровень ${r.level} → ${r.role_name ?? r.role_id}. Уже выданные роли не снимаются.`, danger: true, confirmText: "Удалить" }))) return;
    try {
      await del(`/api/guilds/${guildId}/rewards/${r.id}`);
      toast("ok", "Награда удалена");
      reload();
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  return (
    <>
      <PageHeader title="Роли за уровни" subtitle={<>Роль выдаётся автоматически при достижении уровня. Режим накопления наград — в разделе <Link to={`/servers/${guildId}/leveling`}>«Уровни и XP»</Link>.</>} />
      <Card title="Добавить награду">
        {!connected && <p className="muted small">Бот не подключён — список ролей недоступен.</p>}
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ width: 140 }}><Field label="Уровень"><NumberInput value={level} min={1} max={5000} onChange={setLevel} /></Field></div>
          <div style={{ flex: 1, minWidth: 220 }}><Field label="Роль"><RoleSelect value={roleId} onChange={setRoleId} assignableOnly /></Field></div>
          <button className="btn btn-primary" disabled={!roleId || busy} onClick={add}>Добавить</button>
        </div>
        <p className="small muted mt">Роль бота Mishanya должна быть выше выдаваемых ролей (Настройки сервера → Роли), а у бота должно быть право «Управлять ролями».</p>
      </Card>
      <Card title="Настроенные награды">
        {error && <ErrorBox error={error} onRetry={reload} />}
        {loading && !data ? <Loading /> : data && data.length === 0 ? <Empty icon="🎁">Наград пока нет</Empty> : data && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Уровень</th><th>Роль</th><th>Состояние</th><th>Включена</th><th></th></tr></thead>
              <tbody>
                {data.map((r) => (
                  <tr key={r.id}>
                    <td><strong>{r.level}</strong></td>
                    <td><RoleName id={r.role_id} /></td>
                    <td>{r.problem ? <span className="badge badge-err">{r.problem}</span> : <span className="badge badge-ok">OK</span>}</td>
                    <td><Toggle checked={r.enabled} onChange={(v) => toggle(r, v)} label="Включена" /></td>
                    <td><button className="btn btn-sm btn-danger" onClick={() => remove(r)}>Удалить</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
