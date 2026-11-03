import { useEffect, useRef } from "react";

interface Star { x: number; y: number; r: number; a: number; tw: number }
interface Dust { ang: number; rad: number; speed: number; size: number; hue: number; light: number; alpha: number; fall: number }

/** Анимированный фон: звёзды и чёрная дыра с вращающимся аккреционным диском. Рисуется под содержимым страницы. */
export function SpaceBackground() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let w = 0, h = 0, raf = 0, last = performance.now(), t = 0;
    let cx = 0, cy = 0, R = 0; // центр и радиус горизонта событий
    let stars: Star[] = [];
    let dust: Dust[] = [];
    const starLayer = document.createElement("canvas");
    // Цвета свечения и туманностей берём из текущей цветовой темы (--accent-rgb, --grad-b-rgb).
    let accent = "139,124,248", second = "235,69,158";
    const readColors = () => {
      const css = getComputedStyle(document.documentElement);
      accent = css.getPropertyValue("--accent-rgb").trim() || accent;
      second = css.getPropertyValue("--grad-b-rgb").trim() || second;
    };
    readColors();

    const spawn = (outer: boolean): Dust => {
      const rad = outer ? 2.4 + Math.random() * 3.2 : 1.35 + Math.random() ** 1.6 * 4.3; // в радиусах горизонта
      const hot = Math.max(0, 1 - (rad - 1.35) / 3.5);
      return {
        ang: Math.random() * Math.PI * 2, rad, speed: 0.55 + Math.random() * 0.25,
        size: 0.6 + Math.random() * 1.6, hue: 18 + hot * 25 + Math.random() * 12 - (Math.random() < 0.25 ? 300 : 0),
        light: 55 + hot * 35, alpha: 0.25 + Math.random() * 0.55, fall: 0.008 + Math.random() * 0.02,
      };
    };

    const resize = () => {
      w = window.innerWidth; h = window.innerHeight;
      for (const c of [canvas, starLayer]) { c.width = w * dpr; c.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cx = w * (w > 900 ? 0.72 : 0.6); cy = h * 0.36; R = Math.max(38, Math.min(w, h) * 0.085);
      stars = Array.from({ length: Math.round((w * h) / 2600) }, () => ({
        x: Math.random() * w, y: Math.random() * h, r: Math.random() ** 3 * 1.4 + 0.25,
        a: 0.2 + Math.random() * 0.7, tw: Math.random() * Math.PI * 2,
      }));
      const s = starLayer.getContext("2d")!;
      s.setTransform(dpr, 0, 0, dpr, 0, 0);
      s.clearRect(0, 0, w, h);
      // туманности
      for (const [x, y, rr, col] of [[w * 0.15, h * 0.8, w * 0.35, `rgba(${accent},0.08)`], [w * 0.85, h * 0.9, w * 0.3, `rgba(${second},0.05)`],
        [w * 0.4, h * 0.1, w * 0.3, `rgba(${accent},0.04)`]] as [number, number, number, string][]) {
        const g = s.createRadialGradient(x, y, 0, x, y, rr);
        g.addColorStop(0, col); g.addColorStop(1, "rgba(0,0,0,0)");
        s.fillStyle = g; s.fillRect(0, 0, w, h);
      }
      for (const st of stars) {
        s.fillStyle = `rgba(255,255,255,${st.a * 0.6})`;
        s.beginPath(); s.arc(st.x, st.y, st.r, 0, Math.PI * 2); s.fill();
      }
      dust = Array.from({ length: w < 700 ? 700 : 1400 }, () => spawn(false));
    };

    const TILT = 0.28; // наклон диска: насколько он «сплющен»
    const drawDisk = (front: boolean) => {
      // мягкое свечение самого диска: широкое кольцо, дальняя половина за дырой, ближняя — перед ней
      for (const [rad, width, alpha] of [[2.0, 1.5, 0.16], [1.55, 0.55, 0.32], [3.3, 2.2, 0.07]] as const) {
        ctx.lineWidth = R * width * TILT * 2.2;
        ctx.strokeStyle = `rgba(255,${front ? 170 : 130},${front ? 90 : 70},${alpha})`;
        ctx.beginPath();
        ctx.ellipse(cx, cy, R * rad, R * rad * TILT, 0, front ? 0 : Math.PI, front ? Math.PI : Math.PI * 2);
        ctx.stroke();
      }
    };

    const drawDust = (front: boolean) => {
      for (const d of dust) {
        const sin = Math.sin(d.ang);
        if ((sin > 0) !== front) continue;
        const r = d.rad * R;
        const x = cx + Math.cos(d.ang) * r;
        const y = cy + sin * r * TILT;
        // ближняя к нам сторона диска ярче (доплеровское усиление), дальняя тусклее
        const boost = 0.55 + 0.45 * Math.cos(d.ang - 0.6);
        ctx.fillStyle = `hsla(${d.hue},95%,${d.light}%,${d.alpha * boost})`;
        ctx.fillRect(x, y, d.size * 2.6, d.size * 0.9); // вытянуто вдоль орбиты — как след движения
      }
    };

    const frame = (dt: number) => {
      t += dt;
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = "#040406";
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(starLayer, 0, 0, w, h);
      // мерцающие звёзды
      for (let i = 0; i < stars.length; i += 7) {
        const st = stars[i];
        const a = st.a * (0.5 + 0.5 * Math.sin(t * 1.3 + st.tw));
        ctx.fillStyle = `rgba(255,255,255,${a})`;
        ctx.fillRect(st.x, st.y, st.r * 1.6, st.r * 1.6);
      }
      // свечение вокруг дыры
      const glow = ctx.createRadialGradient(cx, cy, R * 0.9, cx, cy, R * 6);
      glow.addColorStop(0, "rgba(255,150,60,0.22)");
      glow.addColorStop(0.25, `rgba(${accent},0.10)`);
      glow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = glow;
      ctx.fillRect(cx - R * 6, cy - R * 6, R * 12, R * 12);

      // движение пыли: ближе к центру — быстрее (как у Кеплера), медленно затягивается внутрь
      for (let i = 0; i < dust.length; i++) {
        const d = dust[i];
        d.ang += (d.speed / d.rad ** 1.5) * dt * 1.6;
        d.rad -= d.fall * dt * (1 / d.rad);
        if (d.rad < 1.08) dust[i] = spawn(true);
      }
      ctx.globalCompositeOperation = "lighter";
      drawDisk(false);
      drawDust(false); // дальняя половина диска — за дырой
      // линзированный «верх» диска: свет обратной стороны, изогнутый гравитацией в кольцо над горизонтом
      ctx.lineWidth = R * 0.09;
      const halo = ctx.createLinearGradient(cx - R * 1.4, cy, cx + R * 1.4, cy);
      halo.addColorStop(0, "rgba(255,120,50,0.15)");
      halo.addColorStop(0.5, "rgba(255,200,120,0.55)");
      halo.addColorStop(1, "rgba(255,120,50,0.15)");
      ctx.strokeStyle = halo;
      ctx.beginPath(); ctx.ellipse(cx, cy, R * 1.32, R * 1.22, 0, Math.PI, 0); ctx.stroke();
      ctx.globalCompositeOperation = "source-over";
      // горизонт событий
      const hole = ctx.createRadialGradient(cx, cy, R * 0.7, cx, cy, R * 1.12);
      hole.addColorStop(0, "#000");
      hole.addColorStop(0.85, "#000");
      hole.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = hole;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.12, 0, Math.PI * 2); ctx.fill();
      // фотонное кольцо
      ctx.globalCompositeOperation = "lighter";
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = `rgba(255,220,170,${0.55 + 0.1 * Math.sin(t * 2)})`;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.04, Math.PI * 0.98, Math.PI * 2.02); ctx.stroke(); // нижнюю часть закрывает диск
      drawDisk(true);
      drawDust(true); // ближняя половина диска — перед дырой
      ctx.globalCompositeOperation = "source-over";
    };

    const tick = (now: number) => {
      frame(Math.min(0.05, (now - last) / 1000));
      last = now;
      raf = requestAnimationFrame(tick);
    };
    const onVisibility = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden && !still) { last = performance.now(); raf = requestAnimationFrame(tick); }
    };
    const onResize = () => { resize(); if (still) frame(0); };
    resize();
    if (still) frame(0);
    else raf = requestAnimationFrame(tick);
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onVisibility);
    // Сменили цветовую тему — перекрашиваем туманности и свечение.
    const themeWatch = new MutationObserver(() => { readColors(); onResize(); });
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-palette", "data-theme"] });
    return () => {
      themeWatch.disconnect();
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
  return <canvas ref={ref} className="space-bg" aria-hidden="true" />;
}
