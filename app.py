"""Standalone admin website. Run:  uvicorn app:app --port 8001 --reload   ->  http://localhost:8001"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import os

from fastapi import Request
from fastapi.responses import JSONResponse

import admin
import database
from recipes_data import RECIPES

app = FastAPI(title="What Can I Cook - Admin", docs_url=None, openapi_url=None, redoc_url=None)


@app.exception_handler(Exception)
async def _all_errors(request: Request, exc: Exception):
    # Return the real reason as JSON so the login box shows it instead of "Request failed"
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)[:200]}"})


@app.get("/api/health")
def health():
    return {
        "database": database.backend_name(),
        "db_check": database.ping(),
        "ADMIN_USERNAME_set": bool(os.environ.get("ADMIN_USERNAME", "").strip()),
        "ADMIN_PASSWORD_set": bool(os.environ.get("ADMIN_PASSWORD", "")),
    }


app.include_router(admin.router)
try:
    admin.seed(RECIPES)
except Exception as e:  # noqa: BLE001
    print("[admin] seed skipped:", e)
# On Vercel, /public is served by the CDN and is NOT bundled into the function,
# so only mount it when the folder actually exists (local dev).
_public = Path(__file__).parent / "public"
if _public.is_dir():
    app.mount("/", StaticFiles(directory=str(_public), html=True), name="ui")
