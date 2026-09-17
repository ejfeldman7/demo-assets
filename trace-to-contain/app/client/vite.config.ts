import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into ../server/static so FastAPI serves the SPA from one process/port.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../server/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
