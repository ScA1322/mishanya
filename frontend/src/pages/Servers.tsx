import { Link } from "react-router-dom";
import { del, errorText } from "../api";
import { useGuilds } from "../App";
import type { ModuleName } from "../types";
import { Card, Empty, GuildIcon, PageHeader, fmtDate, fmtNum, useConfirm, useToast } from "../ui";

export const MODULE_LABELS: Record<ModuleName, string> = {
  leveling: "Уровни",
  moderation: "Модерация",
  automod: "Автомодерация",
  welcome: "Приветствия",
  autoroles: "Автороли",
  custom_commands: "Команды",
  logging: "Журналы",
  utility: "Утилиты",
};

export default function Servers() {
  const { guilds, reload } = useGuilds();
  const confirm = useConfirm();
  const toast = useToast();
  const active = guilds.filter((g) => g.present);
  const left = guilds.filter((g) => !g.present);

  const remove = async (id: string, name: string) => {
    const ok = await confirm({
      title: `Удалить данные сервера «${name}»?`,
      text: "Будут безвозвратно удалены настройки, XP, предупреждения, команды и журналы этого сервера. Рекомендуется сначала создать резервную копию.",
      confirmText: "Удалить навсегда", danger: true, typeToConfirm: "УДАЛИТЬ",
    });
    if (!ok) return;
    try {
      await del(`/api/guilds/${id}`);
      toast("ok", "Данные сервера удалены");
      reload();
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  return (
    <>
      <PageHeader title="Серверы" subtitle="Серверы, на которых находится Mishanya. У каждого — собственные настройки."
        actions={<button className="btn" onClick={reload}>Обновить</button>} />
      {active.length === 0 ? (
        <Card><Empty icon="🗂️">Бот пока не добавлен ни на один сервер или ещё не подключился к Discord. Ссылку-приглашение можно найти на главной странице.</Empty></Card>
      ) : (
        <div className="grid grid-3">
          {active.map((g) => (
            <Link key={g.id} to={`/servers/${g.id}`} className="card guild-card">
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <GuildIcon name={g.name} url={g.icon_url} />
                <div style={{ minWidth: 0 }}>
                  <h2 style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{g.name}</h2>
                  <div className="small muted mono">{g.id}</div>
                </div>
              </div>
              <div className="row small">
                {g.connected ? <span className="badge badge-ok"><span className="dot dot-ok" />Подключён</span>
                  : <span className="badge badge-warn">Нет связи</span>}
                <span className="muted">Участников: {fmtNum(g.member_count)}</span>
              </div>
              <div className="chips">
                {(Object.keys(MODULE_LABELS) as ModuleName[]).filter((m) => m !== "utility").map((m) => (
                  <span key={m} className={`badge ${g.modules[m] ? "badge-accent" : ""}`}>{MODULE_LABELS[m]}</span>
                ))}
              </div>
            </Link>
          ))}
        </div>
      )}

      {left.length > 0 && (
        <Card className="mt" title="Серверы, которые бот покинул" subtitle="Данные сохранены. Их можно просмотреть или удалить вручную.">
          {left.map((g) => (
            <div key={g.id} className="list-item">
              <GuildIcon name={g.name} url={g.icon_url} small />
              <div style={{ minWidth: 0 }} className="spacer">
                <div><Link to={`/servers/${g.id}`}>{g.name || g.id}</Link></div>
                <div className="small muted">Бот покинул сервер: {fmtDate(g.left_at)}</div>
              </div>
              <button className="btn btn-danger btn-sm" onClick={() => remove(g.id, g.name)}>Удалить данные</button>
            </div>
          ))}
        </Card>
      )}
    </>
  );
}
