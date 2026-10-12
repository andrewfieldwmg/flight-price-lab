import type { ConnectionProfile } from "@/lib/api/types";

export const LAST_SEARCH_FORM_KEY = "flight-price-lab:last-search-form:v1";

export interface PersistedSearchForm {
  version: 1;
  origins: string[];
  destinations: string[];
  outboundDate: string;
  returnDate: string;
  roundTrip: boolean;
  adults: number;
  children: number;
  cabinBags: number;
  checkedBags: number;
  outboundTransfer: boolean;
  returnTransfer: boolean;
  connectionProfile: ConnectionProfile;
  excludeBaggage: boolean;
  cacheOnly?: boolean;
}

const LONDON = new Set(["LGW", "STN", "LTN", "LHR", "LCY"]);
const SARDINIA = new Set(["CAG", "OLB", "AHO"]);
const CONNECTION_PROFILES = new Set<ConnectionProfile>(["CONSERVATIVE", "STANDARD", "AGGRESSIVE"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function airportList(value: unknown, allowed: Set<string>): string[] | null {
  if (!Array.isArray(value) || value.length === 0 || !value.every((item) => typeof item === "string" && allowed.has(item))) return null;
  return [...new Set(value)];
}

function dateValue(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  return Number.isNaN(Date.parse(`${value}T12:00:00Z`)) ? null : value;
}

function count(value: unknown, minimum: number): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum ? value : null;
}

export function loadLastSearchForm(): PersistedSearchForm | null {
  try {
    const raw = window.localStorage.getItem(LAST_SEARCH_FORM_KEY);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value) || value.version !== 1) throw new Error("Unsupported saved search form");
    const origins = airportList(value.origins, LONDON);
    const destinations = airportList(value.destinations, SARDINIA);
    const outboundDate = dateValue(value.outboundDate);
    const returnDate = dateValue(value.returnDate);
    const adults = count(value.adults, 1);
    const children = count(value.children, 0);
    const cabinBags = count(value.cabinBags, 0);
    const checkedBags = count(value.checkedBags, 0);
    const connectionProfile = value.connectionProfile;
    if (
      !origins || !destinations || !outboundDate || !returnDate
      || adults === null || children === null || cabinBags === null || checkedBags === null
      || typeof value.roundTrip !== "boolean"
      || typeof value.outboundTransfer !== "boolean"
      || typeof value.returnTransfer !== "boolean"
      || typeof value.excludeBaggage !== "boolean"
      || typeof connectionProfile !== "string"
      || !CONNECTION_PROFILES.has(connectionProfile as ConnectionProfile)
    ) throw new Error("Invalid saved search form");
    return {
      version: 1,
      origins,
      destinations,
      outboundDate,
      returnDate,
      roundTrip: value.roundTrip,
      adults,
      children,
      cabinBags,
      checkedBags,
      outboundTransfer: value.outboundTransfer,
      returnTransfer: value.returnTransfer,
      connectionProfile: connectionProfile as ConnectionProfile,
      excludeBaggage: value.excludeBaggage,
      cacheOnly: typeof value.cacheOnly === "boolean" ? value.cacheOnly : false,
    };
  } catch {
    try { window.localStorage.removeItem(LAST_SEARCH_FORM_KEY); } catch { /* storage unavailable */ }
    return null;
  }
}

export function saveLastSearchForm(value: Omit<PersistedSearchForm, "version">): void {
  try {
    window.localStorage.setItem(LAST_SEARCH_FORM_KEY, JSON.stringify({ version: 1, ...value }));
  } catch {
    // A blocked or full localStorage must not prevent a search.
  }
}
