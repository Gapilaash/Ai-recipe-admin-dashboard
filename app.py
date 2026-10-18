"""Standalone admin website. Run:  uvicorn app:app --port 8001 --reload   ->  http://localhost:8001"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import admin
from recipes_data import RECIPES

app = FastAPI(title="What Can I Cook - Admin", docs_url=None, openapi_url=None, redoc_url=None)
app.include_router(admin.router)
try:
    admin.seed(RECIPES)
except Exception as e:  # noqa: BLE001
    print("[admin] seed skipped:", e)
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "public"), html=True), name="ui")
