import { useEffect, useRef } from "react";
import { SpaceBackground } from "./space";

/** Анимированные фоны панели. Рисуются под содержимым и берут цвета из текущей цветовой темы. */
export const BACKGROUNDS: [string, string][] = [
  ["blackhole", "🕳️ Чёрная дыра"],
  ["galaxy", "🌌 Галактика"],
  ["warp", "🚀 Гиперпрыжок"],
  ["aurora", "🌈 Северное сияние"],
  ["matrix", "💻 Матрица"],
  ["network", "🕸️ Созвездия"],
  ["embers", "🔥 Угли"],
  ["none", "⬛ Без фона"],
];

type Colors = { a: string; b: string };
type Frame = (dt: number, t: number) => void;
type Init = (ctx: CanvasRenderingContext2D, w: number, h: number, c: Colors) => Frame;

function readColors(): Colors {
  const css = getComputedStyle(document.documentElement);
  return { a: css.getPropertyValue("--accent-rgb").trim() || "139,124,248", b: css.getPropertyValue("--grad-b-rgb").trim() || "235,69,158" };
}

/** Общий холст: размер под окно, пауза при скрытой вкладке, перерисовка при смене темы. */
function AnimatedCanvas({ init }: { init: Init }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let frame: Frame = () => {};
    let raf = 0, last = performance.now(), t = 0;
    const setup = () => {
      const w = window.innerWidth, h = window.innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      frame = init(ctx, w, h, readColors());
      if (still) frame(0, 0);
    };
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now; t += dt;
      frame(dt, t);
      raf = requestAnimationFrame(tick);
    };
    const onVisibility = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden && !still) { last = performance.now(); raf = requestAnimationFrame(tick); }
    };
    setup();
    if (!still) raf = requestAnimationFrame(tick);
    window.addEventListener("resize", setup);
    document.addEventListener("visibilitychange", onVisibility);
    const watch = new MutationObserver(setup);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-palette", "data-theme"] });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", setup);
      document.removeEventListener("visibilitychange", onVisibility);
      watch.disconnect();
    };
  }, [init]);
  return <canvas ref={ref} className="space-bg" aria-hidden="true" />;
}

const clear = (ctx: CanvasRenderingContext2D, w: number, h: number, alpha = 1) => {
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = `rgba(4,4,6,${alpha})`;
  ctx.fillRect(0, 0, w, h);
};

/** Спиральная галактика: тысячи звёзд вращаются вокруг яркого ядра. */
const galaxy: Init = (ctx, w, h, c) => {
  const cx = w * 0.68, cy = h * 0.42, R = Math.min(w, h) * 0.42;
  const stars = Array.from({ length: w < 700 ? 1200 : 2600 }, () => {
    const arm = Math.floor(Math.random() * 3);
    const r = Math.random() ** 1.6 * R;
    const ang = arm * (Math.PI * 2 / 3) + r / R * 5.5 + (Math.random() - 0.5) * (0.9 - r / R * 0.5);
    return { r, ang, size: Math.random() * 1.4 + 0.3, hot: Math.random() < 0.5, tw: Math.random() * 6 };
  });
  return (dt, t) => {
    clear(ctx, w, h);
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.35);
    core.addColorStop(0, "rgba(255,240,220,0.55)");
    core.addColorStop(0.3, `rgba(${c.a},0.18)`);
    core.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = core;
    ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = "lighter";
    for (const s of stars) {
      s.ang += dt * (0.05 + 0.25 * (1 - s.r / R)) * 0.6;
      const x = cx + Math.cos(s.ang) * s.r, y = cy + Math.sin(s.ang) * s.r * 0.55;
      const a = 0.35 + 0.35 * Math.sin(t * 2 + s.tw);
      ctx.fillStyle = s.hot ? `rgba(${c.a},${a})` : `rgba(${c.b},${a * 0.8})`;
      ctx.fillRect(x, y, s.size, s.size);
    }
  };
};

/** Гиперпрыжок: звёзды летят из центра, оставляя следы. */
const warp: Init = (ctx, w, h, c) => {
  const cx = w / 2, cy = h / 2;
  const mk = () => ({ x: (Math.random() - 0.5) * w, y: (Math.random() - 0.5) * h, z: Math.random() * w });
  const stars = Array.from({ length: 500 }, mk);
  return (dt) => {
    clear(ctx, w, h, 0.35);
    ctx.globalCompositeOperation = "lighter";
    for (const s of stars) {
      const pz = s.z;
      s.z -= dt * w * 0.35;
      if (s.z < 1) { Object.assign(s, mk(), { z: w }); continue; }
      const sx = cx + (s.x / s.z) * w * 0.5, sy = cy + (s.y / s.z) * w * 0.5;
      const px = cx + (s.x / pz) * w * 0.5, py = cy + (s.y / pz) * w * 0.5;
      const k = 1 - s.z / w;
      ctx.strokeStyle = k > 0.6 ? `rgba(255,255,255,${k})` : `rgba(${c.a},${k})`;
      ctx.lineWidth = k * 2.2;
      ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(sx, sy); ctx.stroke();
    }
  };
};

/** Северное сияние: переливающиеся ленты над звёздным небом. */
const aurora: Init = (ctx, w, h, c) => {
  const stars = Array.from({ length: Math.round(w * h / 4000) }, () => ({ x: Math.random() * w, y: Math.random() * h, r: Math.random() * 1.2 }));
  const bands = [c.a, c.b, "56,239,170"];
  return (_dt, t) => {
    clear(ctx, w, h);
    ctx.fillStyle = "rgba(255,255,255,0.6)";
    for (const s of stars) ctx.fillRect(s.x, s.y, s.r, s.r);
    ctx.globalCompositeOperation = "lighter";
    bands.forEach((col, i) => {
      const base = h * (0.25 + i * 0.12);
      for (let x = 0; x <= w; x += 6) {
        const y = base + Math.sin(x * 0.004 + t * (0.25 + i * 0.1) + i) * 60 + Math.sin(x * 0.011 - t * 0.4) * 25;
        const height = 120 + Math.sin(x * 0.008 + t * 0.6 + i * 2) * 60;
        const g = ctx.createLinearGradient(0, y - height, 0, y + 20);
        g.addColorStop(0, `rgba(${col},0)`);
        g.addColorStop(0.7, `rgba(${col},0.10)`);
        g.addColorStop(1, `rgba(${col},0)`);
        ctx.fillStyle = g;
        ctx.fillRect(x, y - height, 7, height + 20);
      }
    });
  };
};

/** Цифровой дождь. */
const matrix: Init = (ctx, w, h, c) => {
  const size = 16, cols = Math.ceil(w / size);
  const drops = Array.from({ length: cols }, () => Math.random() * -h / size);
  const speed = Array.from({ length: cols }, () => 8 + Math.random() * 14);
  const chars = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉ0123456789ИМПЕРАТОР";
  clear(ctx, w, h);
  return (dt) => {
    clear(ctx, w, h, 0.12);
    ctx.font = `${size}px monospace`;
    for (let i = 0; i < cols; i++) {
      const y = drops[i] * size;
      ctx.fillStyle = "rgba(230,255,240,0.9)";
      ctx.fillText(chars[Math.floor(Math.random() * chars.length)], i * size, y);
      ctx.fillStyle = `rgba(${c.a},0.55)`;
      ctx.fillText(chars[Math.floor(Math.random() * chars.length)], i * size, y - size);
      drops[i] += speed[i] * dt;
      if (y > h + 50 && Math.random() > 0.97) drops[i] = Math.random() * -20;
    }
  };
};

/** Созвездия: точки дрейфуют и соединяются линиями, когда оказываются рядом. */
const network: Init = (ctx, w, h, c) => {
  const pts = Array.from({ length: Math.min(140, Math.round(w * h / 12000)) }, () => ({
    x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - 0.5) * 18, vy: (Math.random() - 0.5) * 18,
  }));
  const link = 140;
  return (dt) => {
    clear(ctx, w, h);
    for (const p of pts) {
      p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;
    }
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, d = Math.hypot(dx, dy);
        if (d < link) {
          ctx.strokeStyle = `rgba(${c.a},${(1 - d / link) * 0.35})`;
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke();
        }
      }
    }
    for (const p of pts) {
      ctx.fillStyle = `rgba(${c.b},0.9)`;
      ctx.beginPath(); ctx.arc(p.x, p.y, 1.8, 0, Math.PI * 2); ctx.fill();
    }
  };
};

/** Тлеющие угли: искры поднимаются снизу и гаснут. */
const embers: Init = (ctx, w, h, c) => {
  const mk = (anywhere: boolean) => ({
    x: Math.random() * w, y: anywhere ? Math.random() * h : h + 10, vy: 20 + Math.random() * 50, size: 0.8 + Math.random() * 2.2,
    life: 0.5 + Math.random() * 0.5, sway: Math.random() * 6, hot: Math.random() < 0.6,
  });
  const sparks = Array.from({ length: w < 700 ? 90 : 180 }, () => mk(true));
  return (dt, t) => {
    clear(ctx, w, h);
    const glow = ctx.createLinearGradient(0, h, 0, h * 0.55);
    glow.addColorStop(0, `rgba(${c.a},0.16)`);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, h * 0.55, w, h * 0.45);
    ctx.globalCompositeOperation = "lighter";
    sparks.forEach((s, i) => {
      s.y -= s.vy * dt;
      s.x += Math.sin(t * 1.5 + s.sway) * 12 * dt;
      const k = Math.max(0, s.y / h) * s.life;
      if (s.y < -10 || k <= 0.02) { sparks[i] = mk(false); return; }
      ctx.fillStyle = s.hot ? `rgba(255,${120 + Math.round(k * 100)},40,${k})` : `rgba(${c.b},${k})`;
      ctx.beginPath(); ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2); ctx.fill();
    });
  };
};

const INITS: Record<string, Init> = { galaxy, warp, aurora, matrix, network, embers };

export function Background({ kind }: { kind: string }) {
  if (kind === "none") return null;
  if (kind === "blackhole" || !INITS[kind]) return <SpaceBackground />;
  return <AnimatedCanvas key={kind} init={INITS[kind]} />;
}
