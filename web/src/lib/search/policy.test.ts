import { describe, expect, it } from "vitest";
import { mapSelfTransferPolicy } from "./policy";

describe("self-transfer policy mapping", () => {
  it.each([
    [false, false, "NONE"],
    [true, false, "OUTBOUND_ONLY"],
    [false, true, "RETURN_ONLY"],
    [true, true, "BOTH"],
  ] as const)("maps outbound=%s return=%s", (outbound, inbound, expected) => {
    expect(mapSelfTransferPolicy(outbound, inbound)).toBe(expected);
  });
});
