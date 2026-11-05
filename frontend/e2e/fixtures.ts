import { expect, test as base } from "@playwright/test";
import type { Page } from "@playwright/test";

export const FIXTURE_ADDRESS = "180 SONIA PLAINS APT 842 MATTAPOISETT MA";
export const FIXTURE_LENDER = "ROBERTS INDUSTRY CAPITAL LLC";

type Scenario =
  | "ok"
  | "empty"
  | "partial"
  | "server-error"
  | "rate-limit-once"
  | "rate-limit-body-only-once"
  | "unavailable-once"
  | "detail-unavailable-once"
  | "coverage-error"
  | "hold";

export async function setScenario(page: Page, s: Scenario): Promise<void> {
  await page.evaluate((name) => {
    (window as unknown as { __mockScenario: (n: string) => void }).__mockScenario(name);
  }, s);
}

/** Open the app with the MSW worker ready and data rendered. Fails the test on any off-loopback request. */
export const test = base.extend<{ external: string[]; consoleProblems: string[] }>({
  external: [async ({ page }, use) => {
    const external: string[] = [];
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (!["127.0.0.1", "localhost"].includes(u.hostname) && u.protocol.startsWith("http")) external.push(r.url());
    });
    await use(external);
    expect(external, "no request may leave loopback").toEqual([]);
  }, { auto: true }],
  consoleProblems: async ({ page }, use) => {
    const problems: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" || /content security policy/i.test(m.text())) problems.push(m.text());
    });
    page.on("pageerror", (e) => problems.push(String(e)));
    await use(problems);
  },
});

export async function openApp(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: "Bridge loan map" })).toBeVisible();
}

export async function waitForPins(page: Page, count = 6): Promise<void> {
  await expect(page.locator("button.pin")).toHaveCount(count);
}

export async function tabToPin(page: Page): Promise<void> {
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement?.classList.contains("pin") ?? false)) return;
  }
  throw new Error("never reached a pin by Tab");
}

export { expect };
