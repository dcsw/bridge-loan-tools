import { setupWorker } from "msw/browser";
import { handlers, releaseHold, setScenario } from "./handlers";
import type { Scenario } from "./handlers";

declare global {
  interface Window {
    __mockScenario?: (s: Scenario) => void;
    __mockRelease?: () => void;
  }
}

export async function startMocks(): Promise<void> {
  window.__mockScenario = setScenario;
  window.__mockRelease = releaseHold;
  await setupWorker(...handlers).start({ onUnhandledFrame: "bypass", quiet: true });
}
