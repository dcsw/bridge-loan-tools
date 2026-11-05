import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function run(env: Record<string, string>) {
  return spawnSync("node", ["scripts/check-types.mjs"], {
    env: { ...process.env, ...env },
    encoding: "utf8",
  });
}

describe("types drift check", () => {
  it("passes against the committed schema", () => {
    const r = run({});
    expect(r.status, r.stderr).toBe(0);
  });
  it("exits non-zero when the committed schema is stale", () => {
    const dir = mkdtempSync(join(tmpdir(), "stale-"));
    const stale = join(dir, "schema.d.ts");
    copyFileSync("src/api/schema.d.ts", stale);
    writeFileSync(stale, readFileSync(stale, "utf8") + "\n// stale\n");
    const r = run({ SCHEMA_FILE: stale });
    expect(r.status).toBe(1);
    expect(r.stderr).toContain("DRIFT");
  });
  it("exits non-zero when the spec changes but the types do not", () => {
    const dir = mkdtempSync(join(tmpdir(), "spec-"));
    const spec = join(dir, "openapi.json");
    const doc = JSON.parse(readFileSync("../openapi.json", "utf8"));
    doc.components.schemas.Health.properties.extra = { type: "string" };
    writeFileSync(spec, JSON.stringify(doc));
    const r = run({ OPENAPI_SPEC: spec });
    expect(r.status).toBe(1);
  });
});
