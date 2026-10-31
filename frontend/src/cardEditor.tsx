// Редактор картинки-баннера приветствия/прощания с живым предпросмотром, который рисует сам backend.
import { useEffect, useState } from "react";
import { errorText, postBlob, put } from "./api";
import { Icon } from "./icons";
import type { CardConfig } from "./types";
import { Field, Loading, NumberInput, Tabs, TemplateInput, ToggleRow, useToast } from "./ui";

const FONTS: [CardConfig["font"], string][] = [
  ["segoe", "Segoe UI"], ["arial", "Arial"], ["verdana", "Verdana"], ["tahoma", "Tahoma"],
  ["trebuchet", "Trebuchet MS"], ["georgia", "Georgia"], ["impact", "Impact"], ["comic", "Comic Sans MS"],
];
const SHAPES: [CardConfig["avatar_shape"], string][] = [["circle", "Круг"], ["rounded", "Скруглённый квадрат"], ["square", "Квадрат"]];
const MAX_BG = 8 * 1024 * 1024;

/** Готовые сочетания цветов фона. */
const PALETTES: [string, string, string][] = [
  ["Discord", "#5865F2", "#23272A"], ["Закат", "#F97316", "#DB2777"], ["Океан", "#0EA5E9", "#1E3A8A"],
  ["Лес", "#22C55E", "#14532D"], ["Ночь", "#312E81", "#020617"], ["Красный", "#ED4245", "#2B1215"],
];

function ColorInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="row" style={{ flexWrap: "nowrap" }}>
      <input type="color" value={value} onChange={(e) => onChange(e.target.value)} style={{ width: 44, height: 34, border: "none", background: "none" }} />
      <input className="input" value={value} maxLength={7} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function usePreviewImage(guildId: string, kind: string, card: CardConfig) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = JSON.stringify(card);
  useEffect(() => {
    if (!card.enabled) return;
    let alive = true;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const blob = await postBlob(`/api/guilds/${guildId}/welcome/card-preview`, { kind, card });
        if (!alive) return;
        setUrl(URL.createObjectURL(blob));
        setError(null);
      } catch (e) {
        if (alive) setError(errorText(e));
      } finally {
        if (alive) setLoading(false);
      }
    }, 400);
    return () => { alive = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, guildId, kind]);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]); // освобождаем предыдущую картинку
  return { url, loading, error };
}

export function CardEditor({ guildId, kind, value, onChange, vars }: {
  guildId: string; kind: "join" | "leave"; value: CardConfig; onChange: (v: CardConfig) => void; vars: Record<string, string>;
}) {
  const toast = useToast();
  const set = <K extends keyof CardConfig>(k: K, v: CardConfig[K]) => onChange({ ...value, [k]: v });
  const preview = usePreviewImage(guildId, kind, value);
  const [bgTab, setBgTab] = useState<"color" | "image">(value.background_image ? "image" : "color");
  const [uploading, setUploading] = useState(false);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_BG) { toast("err", "Картинка больше 8 МБ"); return; }
    setUploading(true);
    try {
      const data = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result));
        r.onerror = () => reject(new Error("Не удалось прочитать файл"));
        r.readAsDataURL(file);
      });
      const res = await put<{ background_image: string }>(`/api/guilds/${guildId}/welcome/${kind}/background`, { data });
      set("background_image", res.background_image);
      toast("ok", "Фон загружен — не забудьте сохранить настройки");
    } catch (e) {
      toast("err", errorText(e));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="stack">
      <ToggleRow title="Картинка-баннер" hint="Изображение с аватаркой и именем участника, как в MEE6. Если включён embed — картинка будет внутри него."
        checked={value.enabled} onChange={(v) => set("enabled", v)} />
      {value.enabled && (
        <>
          <div className="card-preview">
            {preview.url ? <img src={preview.url} alt="Предпросмотр картинки" style={{ opacity: preview.loading ? 0.6 : 1 }} />
              : preview.error ? <div className="alert alert-err">{preview.error}</div> : <Loading text="Рисую картинку…" />}
            <div className="small muted">Предпросмотр на примере аватара бота. Участник увидит свою аватарку и имя.</div>
          </div>

          <div className="grid grid-3">
            <Field label="Заголовок"><TemplateInput value={value.title} vars={vars} rows={1} maxLength={100} onChange={(v) => set("title", v)} /></Field>
            <Field label="Имя / подзаголовок"><TemplateInput value={value.subtitle} vars={vars} rows={1} maxLength={100} onChange={(v) => set("subtitle", v)} /></Field>
            <Field label="Нижняя строка"><TemplateInput value={value.footer} vars={vars} rows={1} maxLength={150} onChange={(v) => set("footer", v)} /></Field>
          </div>

          <div className="grid grid-3">
            <Field label="Расположение">
              <select className="input" value={value.layout} onChange={(e) => set("layout", e.target.value as CardConfig["layout"])}>
                <option value="center">Аватар сверху, текст по центру</option>
                <option value="left">Аватар слева, текст справа</option>
              </select>
            </Field>
            <Field label="Шрифт">
              <select className="input" value={value.font} onChange={(e) => set("font", e.target.value as CardConfig["font"])}>
                {FONTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            <Field label="Цвет текста"><ColorInput value={value.text_color} onChange={(v) => set("text_color", v)} /></Field>
            <Field label="Форма аватара">
              <select className="input" value={value.avatar_shape} onChange={(e) => set("avatar_shape", e.target.value as CardConfig["avatar_shape"])}>
                {SHAPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            <Field label="Обводка аватара, px" hint="0 — без обводки"><NumberInput value={value.ring_width} min={0} max={20} onChange={(v) => set("ring_width", v)} /></Field>
            <Field label="Цвет обводки"><ColorInput value={value.accent_color} onChange={(v) => set("accent_color", v)} /></Field>
          </div>

          <div>
            <span className="field-label">Фон</span>
            <Tabs tabs={[["color", "Цвет"], ["image", "Своя картинка"]]} value={bgTab} onChange={setBgTab} />
            {bgTab === "color" ? (
              <div className="stack">
                {value.background_image && (
                  <div className="alert alert-info row" style={{ flexWrap: "nowrap" }}>
                    <Icon name="image" size={16} /><span>Сейчас используется картинка — цвет применится, если её убрать.</span>
                  </div>
                )}
                <div className="row">
                  {PALETTES.map(([name, a, b]) => (
                    <button key={name} type="button" className="btn btn-sm" onClick={() => onChange({ ...value, background_color: a, background_color2: b, gradient: true })}>
                      <span className="palette-dot" style={{ background: `linear-gradient(135deg, ${a}, ${b})` }} />{name}
                    </button>
                  ))}
                </div>
                <div className="grid grid-3">
                  <Field label={value.gradient ? "Цвет 1" : "Цвет фона"}><ColorInput value={value.background_color} onChange={(v) => set("background_color", v)} /></Field>
                  {value.gradient && <Field label="Цвет 2"><ColorInput value={value.background_color2} onChange={(v) => set("background_color2", v)} /></Field>}
                </div>
                <ToggleRow title="Градиент" checked={value.gradient} onChange={(v) => set("gradient", v)} />
              </div>
            ) : (
              <div className="stack">
                <div className="image-drop">
                  {value.background_image && <img src={`/api/guilds/${guildId}/welcome/background/${value.background_image}`} alt="" />}
                  <label className="btn btn-sm">
                    <Icon name="upload" size={14} />{uploading ? "Загрузка…" : value.background_image ? "Заменить картинку" : "Загрузить картинку"}
                    <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden disabled={uploading}
                      onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
                  </label>
                  {value.background_image && <button className="btn btn-sm" onClick={() => set("background_image", null)}><Icon name="x" size={14} />Убрать</button>}
                </div>
                <div className="small muted">Лучше всего подходит картинка 1024×450 или больше — она обрезается по размеру баннера. PNG, JPEG, GIF, WebP до 8 МБ.</div>
                <Field label={`Затемнение картинки: ${value.overlay_opacity}%`} hint="Чтобы текст читался на светлом фоне">
                  <input type="range" min={0} max={90} value={value.overlay_opacity} onChange={(e) => set("overlay_opacity", Number(e.target.value))} />
                </Field>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
