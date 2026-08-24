import type { SelfTransferPolicy } from "@/lib/api/types";

export function mapSelfTransferPolicy(
  outboundAllowed: boolean,
  returnAllowed: boolean,
): SelfTransferPolicy {
  if (outboundAllowed && returnAllowed) return "BOTH";
  if (outboundAllowed) return "OUTBOUND_ONLY";
  if (returnAllowed) return "RETURN_ONLY";
  return "NONE";
}
