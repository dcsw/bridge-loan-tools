import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import type { Plugin } from "vite";

/**
 * Production CSP, injected as a <meta> at build time only (Vite's dev server needs inline scripts for HMR).
 * The nginx image sends the same policy as a header plus frame-ancestors, which a meta tag cannot carry.
 * style-src stays strict: React's style prop uses CSSOM, which CSP allows.
 */
export const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

function cspPlugin(): Plugin {
  return {
    name: "inject-csp",
    apply: "build",
    transformIndexHtml: () => [
      { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: CSP }, injectTo: "head-prepend" },
    ],
  };
}

export default defineConfig(({ mode }) => {
  // No VITE_ prefix on purpose: these are read here, in Node, and never reach the client bundle.
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.BRIDGE_API_URL || "http://127.0.0.1:8080";
  const jwt = env.BRIDGE_API_JWT;
  return {
    plugins: [react(), cspPlugin()],
    publicDir: mode === "mock" ? "public-mock" : false,
    build: { sourcemap: false },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        // v1 limitation: single local user. The dev proxy holds the credential and adds it server-side.
        "/api": {
          target,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ""),
          configure: (proxy) => {
            proxy.on("proxyReq", (req) => {
              if (jwt) req.setHeader("Authorization", `Bearer ${jwt}`);
            });
          },
        },
      },
    },
  };
});
