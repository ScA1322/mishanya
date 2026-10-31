import { useState } from "react";
import { useParams } from "react-router-dom";
import { del, errorText, put } from "../api";
import { useApi } from "../hooks";
import type { Paged } from "../types";
import { Card, Empty, ErrorBox, Field, Loading, Modal, NumberInput, PageHeader, Pagination, Toggle, fmtDate, fmtNum, useConfirm, useToast } from "../ui";

interface Row {
  user_id: string; username: string; display_name: string; avatar_url: string | null; xp: number; level: number;
  xp_into_level: number; xp_needed: number; percent: number; message_count: number; left: boolean; last_xp_at: string | null;
  rank: number | null;
}

const LIMIT = 25;

function EditXp({ guildId, row, onClose, onDone }: { guildId: string; row: { user_id: string; name: string; xp: number; level: number } | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [userId, setUserId] = useState(row?.user_id ?? "");
  const [mode, setMode] = useState<"set" | "add" | "level">("add");
  const [value, setValue] = useState(mode === "add" ? 100 : row?.xp ?? 0);
  const [busy, setBusy] = useState(false);
  const validId = /^\d{15,22}$/.test(userId);
  const submit = async () => {
    setBusy(true);
    try {
      const res = await put<{ xp: number; level: number; problems: string[] }>(`/api/guilds/${guildId}/members/${userId}/xp`, { mode, value });
      toast("ok", `Теперь ${res.xp} XP, уровень ${res.level}`);
      res.problems.forEach((p) => toast("err", p));
      onDone();
      onClose();
    } catch (e) {
      toast("err", errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={row ? `Изменить XP: ${row.name}` : "Изменить XP участника"} onClose={onClose}>
      <div className="stack">
        {row ? <p className="muted">Сейчас: {fmtNum(row.xp)} XP, уровень {row.level}</p> : (
          <Field label="Discord ID участника" hint="ПКМ по участнику в Discord → «Копировать ID» (нужен режим разработчика)"
            error={userId && !validId ? "ID — это число из 15–22 цифр" : undefined}>
            <input className="input mono" value={userId} onChange={(e) => setUserId(e.target.value.trim())} />
          </Field>
        )}
        <Field label="Действие">
          <select className="select" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
            <option value="add">Добавить XP (отрицательное число — отнять)</option>
            <option value="set">Установить точное количество XP</option>
            <option value="level">Установить уровень</option>
          </select>
        </Field>
        <Field label={mode === "level" ? "Уровень" : "XP"}>
          <NumberInput value={value} min={mode === "add" ? -1_000_000_000 : 0} onChange={setValue} />
        </Field>
        <p className="small muted">Роли-награды будут синхронизированы с новым уровнем, если участник на сервере.</p>
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>Отмена</button>
        <button className="btn btn-primary" disabled={busy || !validId} onClick={submit}>{busy ? "Сохранение…" : "Применить"}</button>
      </div>
    </Modal>
  );
}

export default function Leaderboard() {
  const { guildId } = useParams();
  const toast = useToast();
  const confirm = useConfirm();
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [includeLeft, setIncludeLeft] = useState(false);
  const [editing, setEditing] = useState<null | { user_id: string; name: string; xp: number; level: number } | "new">(null);
  const qs = new URLSearchParams({ offset: String(offset), limit: String(LIMIT), search: query, include_left: String(includeLeft) });
  const { data, error, loading, reload } = useApi<Paged<Row>>(`/api/guilds/${guildId}/leaderboard?${qs}`, [guildId]);

  const reset = async (r: Row) => {
    if (!(await confirm({ title: `Сбросить статистику ${r.display_name}?`, text: "XP, уровень и счётчик сообщений будут удалены. Роли-награды будут сняты.", danger: true, confirmText: "Сбросить" }))) return;
    try {
      const res = await del<{ problems: string[] }>(`/api/guilds/${guildId}/members/${r.user_id}/xp`);
      toast("ok", "Статистика сброшена");
      res.problems.forEach((p) => toast("err", p));
      reload();
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  return (
    <>
      <PageHeader title="Таблица лидеров" subtitle="Рейтинг участников по XP. Здесь же можно вручную изменить XP и уровень."
        actions={<button className="btn btn-primary" onClick={() => setEditing("new")}>Изменить XP по ID</button>} />
      <Card>
        <form className="row" style={{ marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); setOffset(0); setQuery(search.trim()); }}>
          <input className="input" style={{ maxWidth: 320 }} placeholder="Поиск по имени или ID" value={search} onChange={(e) => setSearch(e.target.value)} />
          <button className="btn">Найти</button>
          {query && <button type="button" className="btn btn-ghost" onClick={() => { setSearch(""); setQuery(""); setOffset(0); }}>Сбросить</button>}
          <span className="spacer" />
          <label className="row small"><Toggle checked={includeLeft} onChange={(v) => { setIncludeLeft(v); setOffset(0); }} label="Показывать вышедших" />Показывать вышедших</label>
        </form>
        {error && <ErrorBox error={error} onRetry={reload} />}
        {loading && !data ? <Loading /> : data && data.items.length === 0 ? (
          <Empty icon="🏆">{query ? "Никого не найдено" : "Пока никто не получил XP"}</Empty>
        ) : data && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Участник</th><th>Уровень</th><th>Прогресс</th><th>XP</th><th>Сообщений</th><th>Последний XP</th><th></th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.user_id}>
                    <td><span className={`rank ${r.rank && r.rank <= 3 ? `rank-${r.rank}` : ""}`}>{r.rank ?? "—"}</span></td>
                    <td>
                      <div className="row" style={{ flexWrap: "nowrap" }}>
                        {r.avatar_url ? <img className="avatar" src={r.avatar_url} alt="" /> : <div className="avatar" />}
                        <div style={{ minWidth: 0 }}>
                          <div>{r.display_name || r.user_id} {r.left && <span className="badge">вышел</span>}</div>
                          <div className="small muted mono">{r.user_id}</div>
                        </div>
                      </div>
                    </td>
                    <td><strong>{r.level}</strong></td>
                    <td style={{ minWidth: 140 }}>
                      <div className="progress"><div style={{ width: `${r.percent}%` }} /></div>
                      <div className="small muted">{r.xp_needed ? `${fmtNum(r.xp_into_level)} / ${fmtNum(r.xp_needed)}` : "максимум"}</div>
                    </td>
                    <td className="nowrap">{fmtNum(r.xp)}</td>
                    <td>{fmtNum(r.message_count)}</td>
                    <td className="small muted nowrap">{fmtDate(r.last_xp_at)}</td>
                    <td className="nowrap">
                      <button className="btn btn-sm" onClick={() => setEditing({ user_id: r.user_id, name: r.display_name, xp: r.xp, level: r.level })}>XP</button>{" "}
                      <button className="btn btn-sm btn-danger" onClick={() => reset(r)}>Сброс</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && <Pagination total={data.total} offset={offset} limit={LIMIT} onChange={setOffset} />}
      </Card>
      {editing && <EditXp guildId={guildId!} row={editing === "new" ? null : editing} onClose={() => setEditing(null)} onDone={reload} />}
    </>
  );
}
