import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// When VITE_API_BASE is set the client talks to the real service; the dev proxy
// below means a plain `npm run dev` can point at a locally running backend
// without CORS in the way.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.FKL_API ?? "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
