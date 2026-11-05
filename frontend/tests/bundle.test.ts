import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

describe("production bundle", () => {
  const SENTINEL = "sentinel-jwt-should-never-ship-0123456789";
  const out = mkdtempSync(join(tmpdir(), "bundle-"));
  const build = spawnSync("npx", ["vite", "build", "--outDir", out, "--emptyOutDir"], {
    env: { ...process.env, BRIDGE_API_JWT: SENTINEL, BRIDGE_API_URL: "http://127.0.0.1:1" },
    encoding: "utf8",
  });
  const files = build.status === 0 ? walk(out) : [];
  const all = files.map((f) => readFileSync(f, "utf8")).join("\n");

  it("builds", () => {
    expect(build.status, build.stderr).toBe(0);
  });
  it("never contains the API credential (dev proxy holds it)", () => {
    expect(all).not.toContain(SENTINEL);
    expect(all).not.toMatch(/Authorization/i);
  });
  it("ships neither MSW nor mock data", () => {
    expect(all).not.toContain("mockServiceWorker");
    expect(all).not.toContain("Synthetic Borrower");
    expect(files.some((f) => f.endsWith("mockServiceWorker.js"))).toBe(false);
  });
  it("embeds a strict CSP meta with no inline script", () => {
    const html = readFileSync(join(out, "index.html"), "utf8").replaceAll("&#39;", "'");
    expect(html).toContain("Content-Security-Policy");
    expect(html).toContain("script-src 'self'");
    expect(html).not.toContain("unsafe-inline");
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/);
  });
  it("works offline: no remote URLs in the built html or css", () => {
    const shell = files.filter((f) => /\.(html|css)$/.test(f)).map((f) => readFileSync(f, "utf8")).join("\n");
    expect(shell).not.toMatch(/https?:\/\//);
  });
});
