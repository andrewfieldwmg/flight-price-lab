import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI

from backend.main import app
from flight_price_lab.api.app import app as application_app


def test_vercel_adapter_exports_existing_fastapi_application() -> None:
    assert isinstance(app, FastAPI)
    assert app is application_app


def test_vercel_adapter_runs_as_a_script() -> None:
    result = subprocess.run(
        [sys.executable, "backend/main.py"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
