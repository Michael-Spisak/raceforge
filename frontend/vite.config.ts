/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const engine = process.env.RACEFORGE_ENGINE_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { chunkSizeWarningLimit: 2500 }, // three.js + drei are large; code-splitting later
  server: {
    proxy: {
      "/api": { target: engine, ws: true },
      "/ldraw": engine,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
