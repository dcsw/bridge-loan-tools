import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

async function lint(code: string) {
  const eslint = new ESLint({ cwd: process.cwd() });
  const [result] = await eslint.lintText(code, { filePath: "src/components/__fixture__.tsx" });
  return result?.messages ?? [];
}

describe("dangerouslySetInnerHTML ban", () => {
  it("flags dangerouslySetInnerHTML in JSX", async () => {
    const msgs = await lint(
      'export const X = ({ h }: { h: string }) => <div dangerouslySetInnerHTML={{ __html: h }} />;\n',
    );
    expect(msgs.map((m) => m.ruleId)).toContain("react/no-danger");
  });
  it("flags innerHTML assignment", async () => {
    const msgs = await lint("export function f(el: HTMLElement, h: string) { el.innerHTML = h; }\n");
    expect(msgs.map((m) => m.ruleId)).toContain("no-restricted-syntax");
  });
  it("accepts plain escaped rendering", async () => {
    const msgs = await lint("export const X = ({ t }: { t: string }) => <div>{t}</div>;\n");
    expect(msgs.filter((m) => m.severity === 2)).toEqual([]);
  });
});
