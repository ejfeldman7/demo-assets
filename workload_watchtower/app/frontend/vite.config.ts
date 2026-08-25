import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds to app/frontend/dist, which app.py serves as the SPA in production.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
