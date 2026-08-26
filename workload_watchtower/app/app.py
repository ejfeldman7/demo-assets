"""
app.py — Workload Watchtower FastAPI entry point.

Serves the REST API under /api and the built React SPA for everything else.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.db import pool
from server.routes import router

# Use uvicorn's logger so timing lines surface in the app runtime logs (uvicorn owns the logging
# config, so a fresh basicConfig logger would emit nowhere).
log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True, timeout=30.0)
    yield
    pool.close()


app = FastAPI(title="Workload Watchtower", lifespan=lifespan)

# Compress JSON/text responses (near-zero cost, ~60-80% smaller payloads — e.g. findings
# lists that repeat query_text). Skips small bodies below minimum_size.
app.add_middleware(GZipMiddleware, minimum_size=500)


# Structured per-request timing for the API so bottlenecks are measured, not guessed. Scoped to
# /api/* so the 15s SPA polling doesn't drown out the signal with static-asset requests.
@app.middleware("http")
async def _timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        dur_ms = (time.perf_counter() - t0) * 1000.0
        log.info("%s %s -> %s %.0fms", request.method, request.url.path, response.status_code, dur_ms)
    return response


app.include_router(router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── serve the React build ────────────────────────────────────────────────────
_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # Unknown /api/* paths must 404 (JSON), not fall through to the SPA HTML.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(os.path.join(_DIST, "index.html"))
