import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, get, post, put } from "./api";
import type { PreviewData } from "./ui";
import { useToast } from "./ui";

/** Загрузка данных GET-запросом с состояниями загрузки и ошибки. */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const reload = useCallback(async () => {
    if (!path) return;
    const id = ++seq.current;
    setLoading(true);
    try {
      const d = await get<T>(path);
      if (id === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (id === seq.current) setError(errorText(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, setData, error, loading, reload };
}

/** Редактирование настроек модуля: черновик, признак изменений, сохранение на backend. */
export function useModuleConfig<T>(guildId: string, module: string) {
  const path = `/api/guilds/${guildId}/config/${module}`;
  const { data, setData, error, loading, reload } = useApi<T>(path);
  const [draft, setDraft] = useState<T | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (data) setDraft(structuredClone(data));
  }, [data]);

  const dirty = !!data && !!draft && JSON.stringify(data) !== JSON.stringify(draft);

  const save = useCallback(async (override?: T) => {
    const body = override ?? draft;
    if (!body) return false;
    setSaving(true);
    try {
      const res = await put<{ config: T; recalculated?: number }>(path, body);
      setData(res.config);
      setDraft(structuredClone(res.config));
      toast("ok", res.recalculated !== undefined ? `Сохранено. Уровни пересчитаны: ${res.recalculated}` : "Настройки сохранены");
      return true;
    } catch (e) {
      toast("err", errorText(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [draft, path, setData, toast]);

  const reset = useCallback(() => data && setDraft(structuredClone(data)), [data]);
  const update = useCallback((fn: (d: T) => void) => {
    setDraft((d) => {
      if (!d) return d;
      const copy = structuredClone(d);
      fn(copy);
      return copy;
    });
  }, []);

  return { saved: data, draft, update, setDraft, dirty, save, saving, reset, error, loading, reload };
}

/** Предупреждение о несохранённых изменениях при закрытии вкладки. */
export function useUnsavedWarning(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);
}

/** Предпросмотр шаблона: тот же рендер, что использует бот. */
export function usePreview(guildId: string | undefined, text: string, embed: unknown, extra: Record<string, string | number> = {}) {
  const [data, setData] = useState<PreviewData | null>(null);
  const [loading, setLoading] = useState(false);
  const key = JSON.stringify([text, embed, extra]);
  useEffect(() => {
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        setData(await post<PreviewData>("/api/preview", { text, embed, guild_id: guildId, extra }));
      } catch {
        setData(null);
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, guildId]);
  return { data, loading };
}
