import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

async function boot(): Promise<void> {
  // Mock mode only (e2e / offline demo). The branch is a build-time constant, so production bundles
  // contain neither MSW nor the mock data.
  if (import.meta.env.MODE === "mock") {
    const { startMocks } = await import("./mocks/browser");
    await startMocks();
  }
  const root = document.getElementById("root");
  if (!root) throw new Error("missing #root");
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void boot();
