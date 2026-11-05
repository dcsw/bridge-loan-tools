import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, openApp, setScenario, test, waitForPins } from "./fixtures";

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

test.describe("axe (0 serious/critical)", () => {
  test("loaded page including the coverage banner and legend", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await expect(page.getByRole("region", { name: "Coverage" })).toContainText("paid");
    expect(await seriousViolations(page)).toEqual([]);
  });

  test("with the card open and borrower/detail revealed", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await page.locator("button.pin").first().click();
    await page.getByRole("button", { name: "Show borrower" }).click();
    await expect(page.getByTestId("borrower")).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
  });

  test("with tooltip visible", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await page.locator("button.pin").first().hover();
    await expect(page.getByRole("tooltip")).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
  });

  test("error and partial states", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await setScenario(page, "server-error");
    await page.getByLabel("Min score").fill("1");
    await expect(page.getByTestId("error-loans")).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
  });

  test("empty state", async ({ page }) => {
    await openApp(page, "/?min_score=100&lender_type=bank");
    await expect(page.getByTestId("empty")).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
  });

  test("pin and legend colors meet WCAG AA (axe color-contrast plus computed outline contrast)", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    const results = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
    expect(results.violations).toEqual([]);
    const fills = await page.evaluate(() =>
      [...document.querySelectorAll(".legend svg, button.pin svg")].map((svg) => {
        const shape = svg.querySelector("circle, rect, polygon");
        const text = svg.querySelector("text");
        return { fill: shape?.getAttribute("fill"), text: text?.getAttribute("fill") };
      }),
    );
    expect(fills.length).toBeGreaterThanOrEqual(9);
    for (const f of fills) expect(f.text).toMatch(/^#(000000|ffffff)$/);
  });
});

test.describe("motion and CSP", () => {
  test("prefers-reduced-motion: zoom is immediate and the tooltip has no transition", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openApp(page);
    await waitForPins(page);
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(page.getByTestId("map")).toHaveAttribute("data-zoom", "1.50", { timeout: 100 });
    await page.locator("button.pin").first().hover();
    const duration = await page.getByRole("tooltip").evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(duration).toBe("0s");
  });

  test("without the preference the tooltip fades and zoom animates", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await openApp(page);
    await waitForPins(page);
    await page.locator("button.pin").first().hover();
    expect(await page.getByRole("tooltip").evaluate((el) => getComputedStyle(el).transitionDuration)).toBe("0.15s");
  });

  test("CSP meta is present and the app runs without violations", async ({ page, consoleProblems }) => {
    await openApp(page);
    await waitForPins(page);
    await page.locator("button.pin").first().click();
    await page.getByRole("button", { name: "Show borrower" }).click();
    await expect(page.getByTestId("borrower")).toBeVisible();
    const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content");
    expect(csp).toContain("script-src 'self'");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).not.toContain("unsafe-eval");
    expect(consoleProblems).toEqual([]);
  });
});
