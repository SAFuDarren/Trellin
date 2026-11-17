/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built interface ships inside the Python package and is served by the
// local workbench under /app/ while it replaces the old interface (roadmap P1.4).
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: {
    outDir: "../research_multiplier/workbench/app",
    emptyOutDir: true,
    // The server's content security policy allows only same-origin files:
    // no inline scripts, no data: URLs.
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    sourcemap: false,
  },
  server: {
    proxy: {
      "/api": { target: process.env.RM_WORKBENCH_URL ?? "http://127.0.0.1:8765", changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
