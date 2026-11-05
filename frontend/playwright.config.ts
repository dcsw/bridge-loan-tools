import { defineConfig, devices } from "@playwright/test";

// Everything runs offline: the app is built in "mock" mode (MSW service worker answers /api/*) and served
// by `vite preview` on loopback. The specs additionally fail on any request leaving 127.0.0.1.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npx vite build --mode mock --outDir dist-mock && npx vite preview --mode mock --outDir dist-mock --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
