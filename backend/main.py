"""Thin Vercel adapter for the existing src-layout FastAPI application."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from flight_price_lab.api.app import app

__all__ = ["app"]
