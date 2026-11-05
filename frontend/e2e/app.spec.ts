import { FIXTURE_ADDRESS, FIXTURE_LENDER, expect, openApp, setScenario, tabToPin, test, waitForPins } from "./fixtures";

test.use({ serviceWorkers: "allow" });

test.describe("map and card", () => {
  test("Tab reaches a pin, Enter opens the card, Esc closes it and returns focus", async ({ page, external, consoleProblems }) => {
    void external;
    await openApp(page);
    await waitForPins(page);
    await tabToPin(page);
    const pin = page.locator("button.pin:focus");
    await expect(pin).toHaveAttribute("aria-label", new RegExp(FIXTURE_ADDRESS));
    await page.keyboard.press("Enter");
    const card = page.getByRole("dialog");
    await expect(card).toBeVisible();
    await expect(card.getByRole("heading", { name: FIXTURE_ADDRESS })).toBeFocused();
    await expect(card).toContainText("placeholder-all-true");
    await expect(card).toContainText("$545,930");
    await expect(card).toContainText("60 months");
    await expect(card).toContainText("2024-07-23");
    await expect(card).toContainText("2029-07-23");
    await expect(card).toContainText("Bridge loan");
    await page.keyboard.press("Escape");
    await expect(card).toBeHidden();
    await expect(page.locator("button.pin:focus")).toHaveAttribute("data-loan-id", /^43fad2cd/);
    expect(consoleProblems).toEqual([]);
  });

  test("hover and focus show a tooltip with address, lender, principal and score; Esc dismisses", async ({ page, external }) => {
    void external;
    await openApp(page);
    await waitForPins(page);
    const pin = page.locator("button.pin").first();
    await pin.hover();
    const tip = page.getByRole("tooltip");
    await expect(tip).toBeVisible();
    for (const text of [FIXTURE_ADDRESS, FIXTURE_LENDER, "$545,930", "100"]) await expect(tip).toContainText(text);
    await page.mouse.move(0, 0);
    await expect(tip).toBeHidden();
    await pin.focus();
    await expect(tip).toContainText(FIXTURE_LENDER);
    await page.keyboard.press("Escape");
    await expect(tip).toBeHidden();
  });

  test("touch tap opens the card", async ({ browser }) => {
    const ctx = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 412, height: 915 }, baseURL: "http://127.0.0.1:4173" });
    const page = await ctx.newPage();
    await openApp(page);
    await waitForPins(page);
    await page.locator("button.pin").first().tap();
    await expect(page.getByRole("dialog")).toContainText(FIXTURE_ADDRESS);
    await ctx.close();
  });

  test("pins encode band by shape, color and number", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    const high = page.locator('button.pin[data-band="high"]').first();
    const low = page.locator('button.pin[data-band="low"]').first();
    await expect(high.locator("polygon")).toHaveCount(1);
    await expect(low.locator("circle")).toHaveCount(1);
    await expect(high).toContainText("100");
    await expect(page.locator('button.pin[data-band="mid"]').first().locator("rect")).toHaveCount(1);
    expect(await high.locator("polygon").getAttribute("fill")).not.toBe(await low.locator("circle").getAttribute("fill"));
  });

  test("zoom buttons and keyboard pan/zoom", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    const map = page.getByTestId("map");
    await expect(map).toHaveAttribute("data-zoom", "1.00");
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect.poll(async () => Number(await map.getAttribute("data-zoom"))).toBeGreaterThan(1.4);
    await page.getByRole("button", { name: "Zoom out" }).click();
    await expect.poll(async () => Number(await map.getAttribute("data-zoom"))).toBeLessThan(1.1);
    // keyboard: with focus inside the map region, + zooms and arrows pan
    await page.locator("button.pin").first().focus();
    await page.keyboard.press("+");
    await expect.poll(async () => Number(await map.getAttribute("data-zoom"))).toBeGreaterThan(1.4);
    const before = await page.locator("button.pin").first().boundingBox();
    await page.keyboard.press("ArrowRight");
    await expect.poll(async () => (await page.locator("button.pin").first().boundingBox())?.x ?? 0).not.toBe(before?.x);
  });
});

test.describe("table", () => {
  test("default sort is score desc, selection focuses the pin, unmapped rows are listed", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    const rows = page.locator("#loan-table tbody tr");
    await expect(rows).toHaveCount(8);
    await expect(rows.first()).toContainText(FIXTURE_ADDRESS);
    await expect(page.getByRole("columnheader", { name: /Score/ })).toHaveAttribute("aria-sort", "descending");
    await expect(page.locator("#loan-table")).toContainText("Not mapped");
    await page.locator("button.row-select").nth(1).click();
    await expect(page.locator("button.pin:focus")).toHaveAttribute("aria-pressed", "true");
    const id = await page.locator("button.pin:focus").getAttribute("data-loan-id");
    expect(page.url()).toContain(`selected=${id}`);
  });

  test("sorting by a column toggles aria-sort and order", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await page.getByRole("button", { name: /^Principal/ }).click();
    await expect(page.getByRole("columnheader", { name: /Principal/ })).toHaveAttribute("aria-sort", "ascending");
    await expect(page.locator("#loan-table tbody tr").first()).toContainText("$150,000");
    expect(page.url()).toContain("sort=principal");
  });

  test("'N not mapped' link filters the table to unmapped rows", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await page.getByRole("link", { name: "2 not mapped" }).click();
    await expect(page.locator("#loan-table tbody tr")).toHaveCount(2);
    expect(page.url()).toContain("view=unmapped");
    await page.getByRole("button", { name: "Show all loans" }).click();
    await expect(page.locator("#loan-table tbody tr")).toHaveCount(8);
  });

  test("selecting an unmapped row opens its card", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await page.getByRole("link", { name: "2 not mapped" }).click();
    await page.locator("button.row-select").first().click();
    await expect(page.getByRole("dialog")).toContainText("UNMAPPED LENDING CO");
  });
});

test.describe("filters", () => {
  test("persist in the URL and survive a reload", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await page.getByLabel("Min score").fill("50");
    await page.getByLabel("Lender type").selectOption("private_llc");
    await page.getByLabel("Recorded from").fill("2024-01-01");
    await expect.poll(() => page.url()).toContain("min_score=50");
    expect(page.url()).toContain("lender_type=private_llc");
    expect(page.url()).toContain("recorded_from=2024-01-01");
    await expect(page.locator("button.pin")).toHaveCount(2);
    await page.reload();
    await expect(page.getByLabel("Min score")).toHaveValue("50");
    await expect(page.getByLabel("Lender type")).toHaveValue("private_llc");
    await expect(page.getByLabel("Recorded from")).toHaveValue("2024-01-01");
    await expect(page.locator("button.pin")).toHaveCount(2);
  });

  test("empty state offers clear-filters", async ({ page }) => {
    await openApp(page, "/?min_score=100&lender_type=bank");
    await expect(page.getByTestId("empty")).toBeVisible();
    await page.getByTestId("empty").getByRole("button", { name: "Clear filters" }).click();
    await waitForPins(page);
    expect(page.url()).not.toContain("min_score");
  });
});

test.describe("borrower and detail", () => {
  test("borrower is absent from DOM and network until 'Show borrower' is clicked", async ({ page }) => {
    const detailCalls: string[] = [];
    page.on("request", (r) => {
      if (/\/api\/loans\/[^/?]+$/.test(new URL(r.url()).pathname)) detailCalls.push(r.url());
    });
    await openApp(page);
    await waitForPins(page);
    expect(await page.content()).not.toContain("Ann Mccoy");
    await page.locator("button.pin").first().click();
    const card = page.getByRole("dialog");
    await expect(card).toBeVisible();
    expect(await page.content()).not.toContain("Ann Mccoy");
    expect(detailCalls).toEqual([]);
    // fired signals come from the list; unevaluable ones need the detail call
    await expect(card).toContainText("Short term");
    await expect(card).not.toContainText("Signals not evaluable");
    await card.getByRole("button", { name: "Show borrower" }).click();
    await expect(page.getByTestId("borrower")).toHaveText("Ann Mccoy");
    expect(detailCalls).toHaveLength(1);
    await expect(card).toContainText("Signals not evaluable");
    await expect(card).toContainText("Purchase then mortgage within 90 days");
    await expect(card).toContainText("Private lender");
    await expect(card).toContainText("Bridge-loan keyword");
    await expect(card).toContainText("97%");
    await expect(card).toContainText("Extractors disagree");
    await expect(card).toContainText("book 315, page 275");
    await expect(card.getByRole("link", { name: /source document/i })).toHaveAttribute("href", /^\/api\/loans\/.+\/source$/);
    await card.getByRole("button", { name: "Hide borrower" }).click();
    expect(await page.content()).not.toContain("Ann Mccoy");
  });

  test("API strings are escaped, never parsed as HTML", async ({ page }) => {
    let dialogs = 0;
    page.on("dialog", (d) => {
      dialogs++;
      void d.dismiss();
    });
    await openApp(page);
    await waitForPins(page);
    await expect(page.locator("#loan-table")).toContainText("<img src=x onerror=alert(1)> 9 ELM ST");
    expect(await page.locator("#loan-table img").count()).toBe(0);
    await page.locator("button.row-select", { hasText: "ELM ST" }).click();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Show borrower" }).click();
    await expect(page.getByTestId("borrower")).toHaveText("<script>alert(1)</script>");
    expect(dialogs).toBe(0);
  });
});

test.describe("coverage, disclaimer and states", () => {
  test("coverage banner shows counts, last ingested, and unknown for null gaps", async ({ page }) => {
    await openApp(page);
    const banner = page.getByRole("region", { name: "Coverage" });
    await expect(banner).toContainText("42 documents fetched");
    await expect(banner.getByTestId("gap-paid")).toHaveText("3 paid");
    await expect(banner.getByTestId("gap-blocked")).toHaveText("1 blocked");
    await expect(banner.getByTestId("gap-failed")).toHaveText("2 failed");
    await expect(banner).toContainText("Last ingested: 2026-01-01T00:00:00+00:00");
    await expect(banner).toContainText("Gaps: unknown");
  });

  test("placeholder classifier disclaimer shows the classifier version", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await expect(page.getByTestId("disclaimer")).toContainText("Unverified placeholder classifier");
    await expect(page.getByTestId("classifier-version")).toHaveText("placeholder-all-true");
  });

  test("loading state is aria-busy until the response is released", async ({ page }) => {
    // Park the first /loans response in the mock until the test releases it (no timers).
    await page.addInitScript(() => {
      const t = setInterval(() => {
        const w = window as unknown as { __mockScenario?: (n: string) => void };
        if (w.__mockScenario) {
          w.__mockScenario("hold");
          clearInterval(t);
        }
      }, 1);
    });
    await openApp(page);
    await expect(page.locator("main")).toHaveAttribute("aria-busy", "true");
    await expect(page.locator("button.pin")).toHaveCount(0);
    await page.evaluate(() => (window as unknown as { __mockRelease: () => void }).__mockRelease());
    await waitForPins(page);
    await expect(page.locator("main")).toHaveAttribute("aria-busy", "false");
  });

  test("partial results are called out", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await setScenario(page, "partial");
    await page.getByLabel("Min score").fill("1");
    await expect(page.getByTestId("partial")).toContainText("Partial results");
  });

  test("429 with Retry-After header and body retries after the advertised delay", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await setScenario(page, "rate-limit-once");
    await page.getByLabel("Min score").fill("1");
    const alert = page.getByTestId("error-loans");
    await expect(alert).toContainText("Too many requests");
    await expect(alert).toContainText("Retrying automatically in 2 seconds");
    await waitForPins(page, 6);
    await expect(alert).toBeHidden();
  });

  test("429 with only the body field uses retry_after_seconds", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await setScenario(page, "rate-limit-body-only-once");
    await page.getByLabel("Min score").fill("1");
    await expect(page.getByTestId("error-loans")).toContainText("Retrying automatically in 3 seconds");
  });

  test("503 shows unavailable and can be retried manually", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await setScenario(page, "unavailable-once");
    await page.getByLabel("Min score").fill("1");
    const alert = page.getByTestId("error-loans");
    await expect(alert).toContainText("temporarily unavailable");
    await alert.getByRole("button", { name: "Retry now" }).click();
    await waitForPins(page, 6);
  });

  test("a non-retryable server error offers Retry without auto-retry", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await setScenario(page, "server-error");
    await page.getByLabel("Min score").fill("1");
    const alert = page.getByTestId("error-loans");
    await expect(alert).toContainText("Could not load loans");
    await expect(alert).not.toContainText("Retrying automatically");
    await setScenario(page, "ok");
    await alert.getByRole("button", { name: "Retry now" }).click();
    await waitForPins(page, 6);
  });

  test("detail 503 shows retry UX inside the card", async ({ page }) => {
    await openApp(page);
    await waitForPins(page);
    await page.locator("button.pin").first().click();
    await setScenario(page, "detail-unavailable-once");
    await page.getByRole("button", { name: "Show borrower" }).click();
    await expect(page.getByTestId("error-loan-details")).toContainText("Retrying automatically in 2 seconds");
    await expect(page.getByTestId("borrower")).toHaveText("Ann Mccoy");
  });

  test("coverage failure does not break the map", async ({ page }) => {
    await page.addInitScript(() => {
      // arm the scenario as soon as the mock API is up
      const t = setInterval(() => {
        const w = window as unknown as { __mockScenario?: (n: string) => void };
        if (w.__mockScenario) {
          w.__mockScenario("coverage-error");
          clearInterval(t);
        }
      }, 1);
    });
    await openApp(page);
    await expect(page.getByTestId("error-coverage")).toBeVisible();
  });
});
