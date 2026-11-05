import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ALL_FEATURES, COVERAGE, detailResponse, listResponse, loanIdFor } from "../src/mocks/data";

// The committed OpenAPI document is the contract; the mocks must satisfy its component schemas.
const doc = JSON.parse(readFileSync("../openapi.json", "utf8")) as { components: { schemas: Record<string, Schema> } };

interface Schema {
  $ref?: string;
  type?: string;
  const?: unknown;
  enum?: unknown[];
  anyOf?: Schema[];
  properties?: Record<string, Schema>;
  required?: string[];
  additionalProperties?: boolean | Schema;
  items?: Schema;
  prefixItems?: Schema[];
  minimum?: number;
  maximum?: number;
}

function validate(value: unknown, schema: Schema, path: string, errors: string[]): void {
  if (schema.$ref) {
    const target = doc.components.schemas[schema.$ref.split("/").pop() ?? ""];
    if (!target) return void errors.push(`${path}: unknown ref ${schema.$ref}`);
    return validate(value, target, path, errors);
  }
  if (schema.anyOf) {
    const attempts = schema.anyOf.map((s) => {
      const e: string[] = [];
      validate(value, s, path, e);
      return e;
    });
    if (!attempts.some((e) => e.length === 0)) errors.push(`${path}: matches no anyOf branch`);
    return;
  }
  if (schema.const !== undefined && value !== schema.const) errors.push(`${path}: expected const ${String(schema.const)}`);
  if (schema.enum && !schema.enum.includes(value)) errors.push(`${path}: ${String(value)} not in enum`);
  switch (schema.type) {
    case "null":
      if (value !== null) errors.push(`${path}: expected null`);
      return;
    case "string":
      if (typeof value !== "string") errors.push(`${path}: expected string`);
      return;
    case "boolean":
      if (typeof value !== "boolean") errors.push(`${path}: expected boolean`);
      return;
    case "integer":
    case "number":
      if (typeof value !== "number" || (schema.type === "integer" && !Number.isInteger(value))) errors.push(`${path}: expected ${schema.type}`);
      else {
        if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${path}: below minimum`);
        if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${path}: above maximum`);
      }
      return;
    case "array":
      if (!Array.isArray(value)) return void errors.push(`${path}: expected array`);
      value.forEach((v, i) => validate(v, schema.prefixItems?.[i] ?? schema.items ?? {}, `${path}[${i}]`, errors));
      return;
    case "object": {
      if (typeof value !== "object" || value === null) return void errors.push(`${path}: expected object`);
      const obj = value as Record<string, unknown>;
      for (const r of schema.required ?? []) if (!(r in obj)) errors.push(`${path}: missing ${r}`);
      for (const [k, v] of Object.entries(obj)) {
        const sub = schema.properties?.[k];
        if (sub) validate(v, sub, `${path}.${k}`, errors);
        else if (schema.additionalProperties === false) errors.push(`${path}: unexpected ${k}`);
        else if (typeof schema.additionalProperties === "object") validate(v, schema.additionalProperties, `${path}.${k}`, errors);
      }
    }
  }
}

function check(value: unknown, name: string): string[] {
  const errors: string[] = [];
  validate(value, { $ref: `#/components/schemas/${name}` }, name, errors);
  return errors;
}

describe("MSW mock data satisfies openapi.json", () => {
  it("list response", () => {
    expect(check(listResponse({ town: null, minScore: null, lenderType: null, recordedFrom: null, recordedTo: null }, false), "FeatureCollection")).toEqual([]);
  });
  it("every detail response", () => {
    for (let n = 0; n < ALL_FEATURES.length; n++) expect(check(detailResponse(loanIdFor(n)), "LoanDetail")).toEqual([]);
  });
  it("coverage response", () => {
    expect(check(COVERAGE, "Coverage")).toEqual([]);
  });
  it("the validator itself rejects bad data", () => {
    expect(check({ features: [], total: -1 }, "FeatureCollection").length).toBeGreaterThan(0);
  });
  it("list properties carry no borrower field", () => {
    for (const f of ALL_FEATURES) expect(Object.keys(f.properties)).not.toContain("borrower");
  });
});
