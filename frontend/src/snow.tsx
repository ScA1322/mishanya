import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { SpaceBackground } from "./space";

type Effect = "snow" | "space";

function readEffect(name: Effect): boolean {
  try {
    return localStorage.getItem(`mishanya-${name}`) !== "off";
  } catch {
    return true;
  }
}

const EffectsCtx = createContext<{ on: Record<Effect, boolean>; toggle: (e: Effect) => void }>({
  on: { snow: true, space: true }, toggle: () => {},
});

/** Украшения панели: снег и космический фон с чёрной дырой. Каждое можно выключить, выбор запоминается. */
export function SnowProvider({ children }: { children: ReactNode }) {
  const [on, setOn] = useState<Record<Effect, boolean>>(() => ({ snow: readEffect("snow"), space: readEffect("space") }));
  const toggle = (e: Effect) => setOn((v) => {
    try { localStorage.setItem(`mishanya-${e}`, v[e] ? "off" : "on"); } catch { /* только до перезагрузки */ }
    return { ...v, [e]: !v[e] };
  });
  useEffect(() => {
    if (on.space) document.documentElement.dataset.space = "on";
    else delete document.documentElement.dataset.space;
  }, [on.space]);
  return (
    <EffectsCtx.Provider value={{ on, toggle }}>
      {on.space && <SpaceBackground />}
      {children}
      {on.snow && <Snowfall />}
    </EffectsCtx.Provider>
  );
}

export function SnowToggle() {
  const { on, toggle } = useContext(EffectsCtx);
  return (
    <div className="effects-toggles">
      <button type="button" className={`snow-toggle${on.snow ? " active" : ""}`} onClick={() => toggle("snow")} title={on.snow ? "Выключить снег" : "Включить снег"}>
        ❄ Снег
      </button>
      <button type="button" className={`snow-toggle${on.space ? " active" : ""}`} onClick={() => toggle("space")} title={on.space ? "Выключить космос" : "Включить космос"}>
        🌌 Космос
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
