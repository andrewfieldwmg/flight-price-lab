from fastapi import FastAPI

from backend.main import app
from flight_price_lab.api.app import app as application_app


def test_vercel_adapter_exports_existing_fastapi_application() -> None:
    assert isinstance(app, FastAPI)
    assert app is application_app
