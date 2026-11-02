import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === "production" ? "/static/" : "/",
  server: {
    port: 5174,
    proxy: {
      "/api": { target: "http://127.0.0.1:8100", ws: true },
      "/health": "http://127.0.0.1:8100",
    },
  },
  // Keep unrelated files in the backend's shared static directory.
  build: { outDir: "../static", emptyOutDir: false },
}));
