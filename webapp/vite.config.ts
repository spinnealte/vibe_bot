import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Разработка: npm run dev (страницы на 5173), API - сервер stubbot.web на 8080 (python -m stubbot.web).
// Сборка: npm run build → dist/, её отдаёт тот же сервер stubbot.web.
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
  build: { outDir: "dist", sourcemap: false },
});
