import { useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { errorText, post } from "../api";
import { useMeta } from "../App";
import { useModuleConfig, usePreview, useUnsavedWarning } from "../hooks";
import { Icon } from "../icons";
import {
  Card, ChannelSelect, DiscordPreview, ErrorBox, Field, Loading, Modal, MultiChannelSelect, MultiRoleSelect, NumberInput, PageHeader,
  SaveBar, TemplateInput, ToggleRow, useConfirm, useToast,
} from "../ui";

export interface LevelingConfig {
  enabled: boolean; xp_min: number; xp_max: number; cooldown_seconds: number; min_message_length: number;
  ignore_duplicate_messages: boolean; xp_multiplier: number; formula_a: number; formula_b: number; formula_c: number;
  max_level: number; announce_mode: "current" | "channel" | "dm" | "off"; announce_channel_id: string | null;
  levelup_message: string; no_xp_channels: string[]; no_xp_categories: string[]; no_xp_roles: string[];
  stack_rewards: boolean; restore_rewards_on_rejoin: boolean; reset_on_leave: boolean; hide_left_members: boolean;
}

const LEVELUP_VARS = ["user", "username", "display_name", "level", "xp", "rank", "server", "channel", "choose"];

export default function Leveling() {
  const { guildId } = useParams();
  const meta = useMeta();
  const toast = useToast();
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const [pendingImport, setPendingImport] = useState<{ entries: unknown[] } | null>(null);
  const c = useModuleConfig<LevelingConfig>(guildId!, "leveling");
  useUnsavedWarning(c.dirty);
  const preview = usePreview(guildId, c.draft?.levelup_message ?? "", null, { level: 5, xp: 1234, rank: 3 });

  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />;
  if (!c.draft) return <Loading />;
  const d = c.draft;
  const vars = Object.fromEntries(Object.entries(meta.template_variables).filter(([k]) => LEVELUP_VARS.includes(k)));

  const levels = Array.from({ length: 8 }, (_, i) => [1, 2, 3, 5, 10, 20, 30, 50][i]).filter((l) => l <= d.max_level);
  const totalFor = (lvl: number) => {
    let s = 0;
    for (let i = 0; i < lvl; i++) s += d.formula_a * i * i + d.formula_b * i + d.formula_c;
    return s;
  };
  const avgXp = ((d.xp_min + d.xp_max) / 2) * d.xp_multiplier;

  const exportData = () => {
    window.location.href = `/api/guilds/${guildId}/leveling/export`;
  };

  const readImport = async (file: File) => {
    try {
      const payload = JSON.parse(await file.text());
      if (!Array.isArray(payload?.entries)) throw new Error();
      setPendingImport(payload);
    } catch {
      toast("err", "Файл не является экспортом Mishanya (ожидается JSON с полем entries)");
    }
  };

  const runImport = async (mode: "merge" | "replace") => {
    const payload = pendingImport;
    setPendingImport(null);
    try {
      const res = await post<{ imported: number }>(`/api/guilds/${guildId}/leveling/import?mode=${mode}`, payload);
      toast("ok", `Импортировано записей: ${res.imported}`);
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  const resetAll = async () => {
    const ok = await confirm({
      title: "Сбросить XP всех участников?", danger: true, confirmText: "Сбросить", typeToConfirm: "СБРОС",
      text: "XP, уровни и счётчики сообщений всех участников сервера будут удалены. Роли-награды не снимаются автоматически.",
    });
    if (!ok) return;
    try {
      const res = await post<{ removed: number }>(`/api/guilds/${guildId}/leveling/reset`, { confirm: "СБРОС" });
      toast("ok", `Сброшено участников: ${res.removed}`);
    } catch (e) {
      toast("err", errorText(e));
    }
  };

  return (
    <>
      <PageHeader title="Уровни и XP" subtitle="Начисление опыта за сообщения, уведомления о новом уровне и исключения" />
      <div className="stack">
        <Card>
          <ToggleRow title="Система уровней включена" hint="Если выключено, XP не начисляется, но статистика сохраняется"
            checked={d.enabled} onChange={(v) => c.update((x) => { x.enabled = v; })} />
        </Card>

        <Card title="Начисление XP" subtitle="XP начисляется не чаще одного раза за интервал — это главная защита от фарма спамом">
          <div className="grid grid-3">
            <Field label="Минимум XP за сообщение"><NumberInput value={d.xp_min} min={0} max={1000} onChange={(v) => c.update((x) => { x.xp_min = v; })} /></Field>
            <Field label="Максимум XP за сообщение"><NumberInput value={d.xp_max} min={0} max={1000} onChange={(v) => c.update((x) => { x.xp_max = v; })} /></Field>
            <Field label="Множитель XP" hint="1 — обычный, 2 — двойной опыт"><NumberInput value={d.xp_multiplier} min={0} max={10} step={0.1} onChange={(v) => c.update((x) => { x.xp_multiplier = v; })} /></Field>
            <Field label="Интервал начисления, сек" hint="По умолчанию 60"><NumberInput value={d.cooldown_seconds} min={0} max={86400} onChange={(v) => c.update((x) => { x.cooldown_seconds = v; })} /></Field>
            <Field label="Минимальная длина сообщения" hint="Короче — XP не начисляется"><NumberInput value={d.min_message_length} min={0} max={500} onChange={(v) => c.update((x) => { x.min_message_length = v; })} /></Field>
          </div>
          {d.xp_min > d.xp_max && <div className="alert alert-err mt">Минимум не может быть больше максимума.</div>}
          <div className="mt">
            <ToggleRow title="Не начислять XP за повтор того же сообщения" hint="Защита от копирования одного и того же текста"
              checked={d.ignore_duplicate_messages} onChange={(v) => c.update((x) => { x.ignore_duplicate_messages = v; })} />
          </div>
        </Card>

        <Card title="Формула уровней" subtitle="XP для перехода с уровня L на L+1 = A·L² + B·L + C (как у MEE6 при A=5, B=50, C=100)">
          <div className="grid grid-4">
            <Field label="A"><NumberInput value={d.formula_a} min={0} max={10000} onChange={(v) => c.update((x) => { x.formula_a = v; })} /></Field>
            <Field label="B"><NumberInput value={d.formula_b} min={0} max={100000} onChange={(v) => c.update((x) => { x.formula_b = v; })} /></Field>
            <Field label="C"><NumberInput value={d.formula_c} min={1} max={1000000} onChange={(v) => c.update((x) => { x.formula_c = v; })} /></Field>
            <Field label="Максимальный уровень"><NumberInput value={d.max_level} min={1} max={5000} onChange={(v) => c.update((x) => { x.max_level = v; })} /></Field>
          </div>
          <div className="table-wrap mt">
            <table>
              <thead><tr><th>Уровень</th><th>Всего XP</th><th>≈ сообщений</th></tr></thead>
              <tbody>
                {levels.map((l) => (
                  <tr key={l}><td>{l}</td><td>{totalFor(l).toLocaleString("ru-RU")}</td>
                    <td className="muted">{avgXp > 0 ? Math.ceil(totalFor(l) / avgXp).toLocaleString("ru-RU") : "∞"}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted mt">При изменении формулы уровни всех участников будут пересчитаны по их текущему XP.</p>
        </Card>

        <Card title="Уведомление о новом уровне">
          <div className="grid grid-2">
            <Field label="Куда отправлять">
              <select className="select" value={d.announce_mode} onChange={(e) => c.update((x) => { x.announce_mode = e.target.value as LevelingConfig["announce_mode"]; })}>
                <option value="current">В канал, где написано сообщение</option>
                <option value="channel">В выбранный канал</option>
                <option value="dm">В личные сообщения</option>
                <option value="off">Не отправлять (без публичных уведомлений)</option>
              </select>
            </Field>
            {d.announce_mode === "channel" && (
              <Field label="Канал уведомлений" error={!d.announce_channel_id ? "Выберите канал" : undefined}>
                <ChannelSelect value={d.announce_channel_id} onChange={(v) => c.update((x) => { x.announce_channel_id = v; })} />
              </Field>
            )}
          </div>
          {d.announce_mode !== "off" && (
            <div className="grid grid-2 mt">
              <Field label="Текст сообщения">
                <TemplateInput value={d.levelup_message} vars={vars} onChange={(v) => c.update((x) => { x.levelup_message = v; })} />
              </Field>
              <div className="field"><span className="field-label">Предпросмотр</span><DiscordPreview data={preview.data} loading={preview.loading} /></div>
            </div>
          )}
        </Card>

        <Card title="Исключения" subtitle="Где и кому XP не начисляется">
          <div className="grid grid-3">
            <Field label="Каналы"><MultiChannelSelect value={d.no_xp_channels} onChange={(v) => c.update((x) => { x.no_xp_channels = v; })} /></Field>
            <Field label="Категории"><MultiChannelSelect categories value={d.no_xp_categories} onChange={(v) => c.update((x) => { x.no_xp_categories = v; })} /></Field>
            <Field label="Роли"><MultiRoleSelect value={d.no_xp_roles} onChange={(v) => c.update((x) => { x.no_xp_roles = v; })} /></Field>
          </div>
        </Card>

        <Card title="Награды и выход участников">
          <ToggleRow title="Накапливать роли-награды" hint="Выключите, чтобы у участника оставалась только роль самого высокого достигнутого уровня"
            checked={d.stack_rewards} onChange={(v) => c.update((x) => { x.stack_rewards = v; })} />
          <ToggleRow title="Возвращать награды при повторном вступлении" checked={d.restore_rewards_on_rejoin}
            onChange={(v) => c.update((x) => { x.restore_rewards_on_rejoin = v; })} />
          <ToggleRow title="Сбрасывать XP при выходе с сервера" hint="По умолчанию XP сохраняется и восстанавливается при возвращении"
            checked={d.reset_on_leave} onChange={(v) => c.update((x) => { x.reset_on_leave = v; })} />
          <ToggleRow title="Скрывать вышедших участников в таблице лидеров" checked={d.hide_left_members}
            onChange={(v) => c.update((x) => { x.hide_left_members = v; })} />
        </Card>

        <Card title="Данные" subtitle="Экспорт и импорт статистики в формате JSON, полный сброс">
          <div className="row">
            <button className="btn" onClick={exportData}><Icon name="download" size={15} />Экспорт JSON</button>
            <button className="btn" onClick={() => fileRef.current?.click()}><Icon name="upload" size={15} />Импорт JSON</button>
            <input ref={fileRef} type="file" accept="application/json,.json" hidden
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) readImport(f); }} />
            <span className="spacer" />
            <button className="btn btn-danger" onClick={resetAll}>Сбросить XP сервера</button>
          </div>
        </Card>
      </div>
      {pendingImport && (
        <Modal title="Импорт статистики" onClose={() => setPendingImport(null)}>
          <p>В файле записей: <strong>{pendingImport.entries.length}</strong>. Выберите режим:</p>
          <ul className="small muted">
            <li><strong>Объединить</strong> — XP участников из файла перезапишет их текущий XP, остальные не изменятся.</li>
            <li><strong>Заменить всё</strong> — текущая статистика сервера будет удалена.</li>
          </ul>
          <div className="modal-actions">
            <button className="btn" onClick={() => setPendingImport(null)}>Отмена</button>
            <button className="btn btn-danger" onClick={() => runImport("replace")}>Заменить всё</button>
            <button className="btn btn-primary" onClick={() => runImport("merge")}>Объединить</button>
          </div>
        </Modal>
      )}
      <SaveBar dirty={c.dirty} saving={c.saving} onSave={() => c.save()} onReset={c.reset} />
    </>
  );
}
