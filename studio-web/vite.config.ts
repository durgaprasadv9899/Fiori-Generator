import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built output (dist/) is served by the approuter on BTP and by studio_api.py locally.
// Dev: `npm run dev` proxies /api to the Python API on :8620.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5173, proxy: { "/api": "http://localhost:8620" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 4000 },
});
