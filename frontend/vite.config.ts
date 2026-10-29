import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В режиме разработки (npm run dev) запросы /api проксируются на работающий backend Mishanya.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8777", changeOrigin: false } },
  },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
});
