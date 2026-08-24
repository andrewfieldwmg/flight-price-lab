import type { SearchSnapshot, TripOption, TripSearchRequest } from "@/lib/api/types";
import { uniqueOptions } from "./calculations";

export interface SearchUiState {
  request: TripSearchRequest | null;
  snapshot: SearchSnapshot | null;
  selectedOutboundId: string | null;
  selectedReturnId: string | null;
  sseDisconnected: boolean;
  hubsStarted: number;
  hubsCompleted: number;
  directionProgress: Record<"OUTBOUND" | "RETURN", { started: number; completed: number }>;
  outboundSelectionTouched: boolean;
  returnSelectionTouched: boolean;
}

export const initialSearchState: SearchUiState = {
  request: null,
  snapshot: null,
  selectedOutboundId: null,
  selectedReturnId: null,
  sseDisconnected: false,
  hubsStarted: 0,
  hubsCompleted: 0,
  directionProgress: {
    OUTBOUND: { started: 0, completed: 0 },
    RETURN: { started: 0, completed: 0 },
  },
  outboundSelectionTouched: false,
  returnSelectionTouched: false,
};

export type SearchAction =
  | { type: "started"; request: TripSearchRequest }
  | { type: "snapshot"; snapshot: SearchSnapshot }
  | { type: "restore_cached"; snapshot: SearchSnapshot; selectedOutboundId: string | null; selectedReturnId: string | null; directionProgress: SearchUiState["directionProgress"] }
  | { type: "event"; event: string; data: Record<string, unknown> }
  | { type: "disconnect" }
  | { type: "select_outbound"; id: string }
  | { type: "select_return"; id: string };

function preferred(options: TripOption[]): string | null {
  const cheaper = options.filter(
    (option) =>
      option.is_self_transfer && Number(option.saving_vs_nonstop_amount ?? 0) > 0,
  ).sort((left, right) => Number(left.effective_price_low ?? left.base_price) - Number(right.effective_price_low ?? right.base_price))[0];
  return (cheaper ?? options.find((option) => option.is_nonstop) ?? options[0])?.id ?? null;
}

export function searchReducer(
  state: SearchUiState,
  action: SearchAction,
): SearchUiState {
  if (action.type === "started") {
    return { ...initialSearchState, request: action.request };
  }
  if (action.type === "snapshot") {
    const outboundOptions = uniqueOptions(action.snapshot.outbound);
    const returnOptions = uniqueOptions(action.snapshot.return);
    return {
      ...state,
      snapshot: action.snapshot,
      selectedOutboundId:
        state.outboundSelectionTouched && outboundOptions.some((item) => item.id === state.selectedOutboundId)
          ? state.selectedOutboundId
          : preferred(outboundOptions),
      selectedReturnId: state.returnSelectionTouched && returnOptions.some((item) => item.id === state.selectedReturnId)
        ? state.selectedReturnId
        : preferred(returnOptions),
    };
  }
  if (action.type === "restore_cached") {
    const outboundOptions = uniqueOptions(action.snapshot.outbound);
    const returnOptions = uniqueOptions(action.snapshot.return);
    const selectedOutboundId = outboundOptions.some((item) => item.id === action.selectedOutboundId) ? action.selectedOutboundId : preferred(outboundOptions);
    const selectedReturnId = returnOptions.some((item) => item.id === action.selectedReturnId) ? action.selectedReturnId : preferred(returnOptions);
    return { ...state, snapshot: action.snapshot, selectedOutboundId, selectedReturnId, directionProgress: action.directionProgress, hubsStarted: action.directionProgress.OUTBOUND.started + action.directionProgress.RETURN.started, hubsCompleted: action.directionProgress.OUTBOUND.completed + action.directionProgress.RETURN.completed };
  }
  if (action.type === "event") {
    const direction = action.data.direction;
    const directionProgress = { ...state.directionProgress };
    if (direction === "OUTBOUND" || direction === "RETURN") {
      const current = directionProgress[direction];
      directionProgress[direction] = {
        started: current.started + (action.event === "hub_started" ? 1 : 0),
        completed: current.completed + (action.event === "hub_completed" ? 1 : 0),
      };
    }
    return {
      ...state,
      hubsStarted: state.hubsStarted + (action.event === "hub_started" ? 1 : 0),
      hubsCompleted:
        state.hubsCompleted + (action.event === "hub_completed" ? 1 : 0),
      directionProgress,
    };
  }
  if (action.type === "disconnect") return { ...state, sseDisconnected: true };
  if (action.type === "select_outbound")
    return { ...state, selectedOutboundId: action.id, outboundSelectionTouched: true };
  return { ...state, selectedReturnId: action.id, returnSelectionTouched: true };
}
