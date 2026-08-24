"""Safe structured search decision logging."""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

LOGGER = logging.getLogger("flight_price_lab.search")


def search_log(event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        **fields,
    }
    LOGGER.info(json.dumps(payload, sort_keys=True, default=str))


def development_diagnostics_enabled() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() in {
        "dev",
        "development",
        "local",
    }
