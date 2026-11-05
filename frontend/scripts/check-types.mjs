// Drift check: regenerate types from the committed OpenAPI document and fail if the committed
// schema.d.ts differs. Env overrides exist so the check itself can be tested.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const spec = resolve(process.env.OPENAPI_SPEC ?? "../openapi.json");
const committed = resolve(process.env.SCHEMA_FILE ?? "src/api/schema.d.ts");
const dir = mkdtempSync(join(tmpdir(), "oas-drift-"));
const generated = join(dir, "schema.d.ts");
try {
  execFileSync("npx", ["openapi-typescript", spec, "-o", generated], { stdio: "ignore" });
  if (readFileSync(generated, "utf8") !== readFileSync(committed, "utf8")) {
    console.error(`DRIFT: ${committed} is stale relative to ${spec}. Run: npm run gen:types`);
    process.exitCode = 1;
  } else {
    console.log("generated types match openapi.json");
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}
