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
});
