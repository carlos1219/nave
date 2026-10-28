import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

// Misma origen en producción (Nginx proxifica /api/). En desarrollo, el proxy de Vite hace lo
// mismo para que el navegador nunca necesite CORS.
export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { target: "es2022", sourcemap: false, cssCodeSplit: false, modulePreload: false },
  test: { environment: "jsdom", include: ["tests/**/*.test.ts", "src/**/*.test.ts"] },
});
