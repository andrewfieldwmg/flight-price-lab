import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");

describe("results page scrolling layout", () => {
  it("keeps the selected-trip summary in normal document flow", () => {
    const rules = [...css.matchAll(/\.summary-strip\s*\{([^}]*)\}/g)].map((match) => match[1]);
    expect(rules.length).toBeGreaterThan(0);
    for (const rule of rules) {
      expect(rule).not.toMatch(/position\s*:\s*(sticky|fixed)/);
      expect(rule).not.toMatch(/z-index\s*:/);
      expect(rule).not.toMatch(/top\s*:/);
    }
  });

  it("does not create an independently vertically scrolling results layer", () => {
    const tableScroll = css.match(/\.table-scroll\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(tableScroll).not.toMatch(/max-height\s*:/);
    expect(tableScroll).not.toMatch(/overflow-y\s*:\s*(auto|scroll)/);
    expect(tableScroll).toMatch(/overflow-x\s*:\s*auto/);
  });

  it("contains the sticky summary and uses a compact mobile grid", () => {
    const compact = css.match(/\.compact-trip-summary\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(compact).toMatch(/width\s*:\s*100%/);
    expect(compact).toMatch(/max-width\s*:\s*100vw/);
    expect(compact).toMatch(/overflow\s*:\s*hidden/);
    expect(compact).toMatch(/box-sizing\s*:\s*border-box/);
    expect(css).toMatch(/grid-template-columns\s*:\s*minmax\(0,1fr\) max-content/);
    expect(css).toMatch(/grid-template-rows\s*:\s*28px 25px/);
    expect(css).toMatch(/compact-trip-sparkline[^}]*width\s*:\s*52px/);
  });
});
