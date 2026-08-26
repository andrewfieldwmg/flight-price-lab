"""Compare direct and synthetic fare rankings across a small fixed date sample."""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from flight_price_lab.api.app import create_app
from flight_price_lab.storage.database import SearchSessionEntry, create_database_engine

DATES = (
    "2026-12-18",
    "2026-12-20",
    "2026-12-22",
    "2026-12-24",
    "2026-12-26",
    "2026-12-28",
    "2026-12-30",
)
ORIGINS = ["LGW", "STN", "LTN", "LHR", "LCY"]
DESTINATIONS = ["CAG", "OLB", "AHO"]


def request_payload(travel_date: str) -> dict[str, object]:
    return {
        "origins": ORIGINS,
        "destinations": DESTINATIONS,
        "outbound_date": travel_date,
        "return_date": None,
        "adults": 2,
        "children": 2,
        "baggage": {"cabin_bags": 1, "checked_bags": 0},
        "outbound_time_window": {"max_connection_minutes": 360},
        "return_time_window": {"max_connection_minutes": 360},
        "self_transfer_policy": "OUTBOUND_ONLY",
        "connection_profile": "CONSERVATIVE",
        "currency": "GBP",
        "refresh_prices": False,
    }


def reusable_snapshot(travel_date: str) -> dict[str, object] | None:
    with Session(create_database_engine()) as session:
        rows = session.scalars(
            select(SearchSessionEntry).order_by(SearchSessionEntry.updated_at.desc())
        )
        for row in rows:
            request = json.loads(row.request_json)
            snapshot = json.loads(row.snapshot_json)
            outbound = snapshot.get("outbound") or {}
            if (
                request.get("outbound_date") == travel_date
                and request.get("adults") == 2
                and request.get("children") == 2
                and request.get("currency") == "GBP"
                and request.get("baggage") == {"cabin_bags": 1, "checked_bags": 0}
                and request.get("self_transfer_policy") in {"OUTBOUND_ONLY", "BOTH"}
                and outbound.get("nonstop_options")
                and outbound.get("feasible_options")
            ):
                return snapshot
    return None


def live_snapshot(client: TestClient, travel_date: str) -> tuple[dict, int]:
    response = client.post("/api/search/stream", json=request_payload(travel_date))
    response.raise_for_status()
    events = [json.loads(line) for line in response.text.splitlines() if line]
    completed = next(
        event for event in reversed(events) if event["event"] == "search_completed"
    )
    return completed["data"]["snapshot"], int(
        completed["data"]["timings"]["provider_calls_total"]
    )


def metrics(travel_date: str, snapshot: dict, source: str) -> dict[str, object]:
    outbound = snapshot["outbound"]
    directs = [Decimal(item["base_price"]) for item in outbound["nonstop_options"]]
    synthetics = sorted(
        Decimal(item["base_price"])
        for item in outbound["feasible_options"]
        if item["is_self_transfer"]
    )
    if not directs or not synthetics:
        raise RuntimeError(f"{travel_date} lacks comparable direct/synthetic results")
    direct = min(directs)
    synthetic = synthetics[0]
    saving = direct - synthetic
    return {
        "date": travel_date,
        "direct_min": float(direct),
        "synthetic_min": float(synthetic),
        "synthetic_top3_median": float(median(synthetics[:3])),
        "synthetic_count": len(synthetics),
        "best_synthetic_saving_amount": float(saving),
        "best_synthetic_saving_percent": float(saving / direct * 100),
        "source": source,
        "search_id": snapshot["search_id"],
    }


def ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average = (cursor + 1 + end) / 2
        for index, _ in ordered[cursor:end]:
            result[index] = average
        cursor = end
    return result


def pearson(left: list[float], right: list[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    snapshots = {date: reusable_snapshot(date) for date in DATES}
    missing = [date for date, snapshot in snapshots.items() if snapshot is None]
    print(f"Reusable dates: {len(DATES) - len(missing)}/{len(DATES)}")
    print(f"Expected maximum additional provider calls: {len(missing) * 17}")
    if missing and not args.execute:
        print("Pass --execute only after explicit quota authorization.")
        return

    calls = 0
    client = TestClient(create_app())
    rows = []
    for travel_date in DATES:
        snapshot = snapshots[travel_date]
        source = "existing_live_observation"
        if snapshot is None:
            print(f"Running {travel_date} ...", flush=True)
            snapshot, used = live_snapshot(client, travel_date)
            calls += used
            source = "experiment_live_search"
            print(f"Completed {travel_date}: {used} provider calls", flush=True)
        rows.append(metrics(travel_date, snapshot, source))

    direct = [float(row["direct_min"]) for row in rows]
    synthetic = [float(row["synthetic_min"]) for row in rows]
    top3 = [float(row["synthetic_top3_median"]) for row in rows]
    direct_ranks = ranks(direct)
    synthetic_ranks = ranks(synthetic)
    for row, direct_rank, synthetic_rank in zip(
        rows, direct_ranks, synthetic_ranks, strict=True
    ):
        row["direct_rank"] = direct_rank
        row["synthetic_rank"] = synthetic_rank
        row["rank_difference"] = abs(direct_rank - synthetic_rank)
        row["saving"] = row["best_synthetic_saving_amount"]

    group_size = math.ceil(len(rows) / 3)
    direct_order = sorted(range(len(rows)), key=lambda index: direct[index])
    synthetic_order = sorted(range(len(rows)), key=lambda index: synthetic[index])
    cheap_direct = set(direct_order[:group_size])
    cheap_synthetic = set(synthetic_order[:group_size])
    expensive_direct = set(direct_order[-group_size:])
    expensive_synthetic = set(synthetic_order[-group_size:])
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "additional_provider_calls": calls,
        "sample_size": len(rows),
        "quantile_method": f"tercile-like groups of {group_size} dates",
        "spearman_direct_vs_synthetic_min": pearson(direct_ranks, synthetic_ranks),
        "spearman_direct_vs_synthetic_top3_median": pearson(direct_ranks, ranks(top3)),
        "pearson_direct_vs_synthetic_min": pearson(direct, synthetic),
        "pearson_direct_vs_synthetic_top3_median": pearson(direct, top3),
        "cheap_overlap_count": len(cheap_direct & cheap_synthetic),
        "cheap_group_size": group_size,
        "cheap_precision": len(cheap_direct & cheap_synthetic) / group_size,
        "expensive_overlap_count": len(expensive_direct & expensive_synthetic),
        "expensive_group_size": group_size,
        "mean_absolute_rank_error": sum(float(row["rank_difference"]) for row in rows)
        / len(rows),
        "maximum_absolute_rank_error": max(
            float(row["rank_difference"]) for row in rows
        ),
    }
    output = Path("data/processed/experiments")
    output.mkdir(parents=True, exist_ok=True)
    stem = output / "direct_synthetic_date_proxy_2026-08-26"
    (stem.with_suffix(".json")).write_text(
        json.dumps({"rows": rows, "summary": summary}, indent=2), encoding="utf-8"
    )
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"rows": rows, "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
