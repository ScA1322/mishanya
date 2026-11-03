import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { BACKGROUNDS, Background } from "./backgrounds";

function readStored(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function store(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* только до перезагрузки */ }
}

const EffectsCtx = createContext<{ snow: boolean; bg: string; toggleSnow: () => void; setBg: (b: string) => void }>({
  snow: true, bg: "blackhole", toggleSnow: () => {}, setBg: () => {},
});

/** Украшения панели: снег поверх и анимированный фон под содержимым. Выбор запоминается. */
export function SnowProvider({ children }: { children: ReactNode }) {
  const [snow, setSnow] = useState(() => readStored("mishanya-snow", "on") !== "off");
  const [bg, setBgState] = useState(() => {
    if (readStored("mishanya-space", "on") === "off" && !readStored("mishanya-bg", "")) return "none";
    const b = readStored("mishanya-bg", "blackhole");
    return BACKGROUNDS.some(([id]) => id === b) ? b : "blackhole";
  });
  const toggleSnow = () => setSnow((v) => { store("mishanya-snow", v ? "off" : "on"); return !v; });
  const setBg = (b: string) => { store("mishanya-bg", b); setBgState(b); };
  useEffect(() => {
    if (bg !== "none") document.documentElement.dataset.space = "on";
    else delete document.documentElement.dataset.space;
  }, [bg]);
  return (
    <EffectsCtx.Provider value={{ snow, bg, toggleSnow, setBg }}>
      <Background kind={bg} />
      {children}
      {snow && <Snowfall />}
    </EffectsCtx.Provider>
  );
}

export function SnowToggle() {
  const { snow, bg, toggleSnow, setBg } = useContext(EffectsCtx);
  return (
    <div className="effects-toggles">
      <select className="bg-select" value={bg} onChange={(e) => setBg(e.target.value)} title="Анимированный фон (в тёмной теме)" aria-label="Фон">
        {BACKGROUNDS.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select>
      <button type="button" className={`snow-toggle${snow ? " active" : ""}`} onClick={toggleSnow} title={snow ? "Выключить снег" : "Включить снег"}>
        ❄ Снег
      </button>
    </div>
  );
}

interface Flake { x: number; y: number; r: number; vy: number; drift: number; phase: number; alpha: number }

/** Падающий снег поверх страницы. Клики проходят сквозь него; при «уменьшить движение» в системе не показывается. */
function Snowfall() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    let w = 0, h = 0, raf = 0, last = performance.now();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const flakes: Flake[] = [];
    const make = (top: boolean): Flake => {
      const r = 0.8 + Math.random() ** 2 * 2.6; // больше мелких снежинок, редкие крупные
      return { x: Math.random() * w, y: top ? -10 : Math.random() * h, r, vy: 18 + r * 14 + Math.random() * 10,
        drift: 8 + Math.random() * 18, phase: Math.random() * Math.PI * 2, alpha: 0.35 + Math.random() * 0.5 };
    };
    const resize = () => {
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const want = Math.round(Math.min(160, (w * h) / 11000));
      while (flakes.length < want) flakes.push(make(false));
      flakes.length = want;
    };
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      ctx.clearRect(0, 0, w, h);
      for (let i = 0; i < flakes.length; i++) {
        const f = flakes[i];
        f.y += f.vy * dt;
        f.phase += dt * 0.8;
        f.x += Math.sin(f.phase) * f.drift * dt;
        if (f.y > h + 10 || f.x < -20 || f.x > w + 20) { flakes[i] = make(true); continue; }
        ctx.beginPath();
        ctx.fillStyle = `rgba(255,255,255,${f.alpha})`;
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
        ctx.fill();
      }
      raf = requestAnimationFrame(tick);
    };
    const onVisibility = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden) { last = performance.now(); raf = requestAnimationFrame(tick); }
    };
    resize();
    raf = requestAnimationFrame(tick);
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
  return <canvas ref={ref} className="snowfall" aria-hidden="true" />;
}
