import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // 127.0.0.1, not "localhost": uvicorn listens on IPv4 only, while Node may try IPv6 first.
        // Override with API_TARGET=http://127.0.0.1:9000 npm run dev
        target: loadEnv(mode, ".", "API_").API_TARGET || "http://127.0.0.1:8000",
        configure(proxy) {
          // While the backend restarts (uvicorn --reload), answer with a JSON 503 the app
          // recognises and retries, instead of a bare error.
          proxy.on("error", (_err, _req, res) => {
            if ("writeHead" in res && !res.headersSent) {
              res.writeHead(503, { "Content-Type": "application/json", "Retry-After": "1" });
              res.end(JSON.stringify({ detail: "The server is restarting. Retrying shortly…" }));
            }
          });
        },
      },
    },
  },
}));
