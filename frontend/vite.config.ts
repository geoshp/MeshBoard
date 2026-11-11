import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В dev-режиме API и WebSocket проксируются на backend (по умолчанию 127.0.0.1:8080).
declare const process: { env: Record<string, string | undefined> };
const backend = process.env.MESHBOARD_DEV_BACKEND ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/ws": { target: backend.replace(/^http/, "ws"), ws: true },
      "/api": { target: backend, changeOrigin: false },
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
    sourcemap: false,
  },
});
