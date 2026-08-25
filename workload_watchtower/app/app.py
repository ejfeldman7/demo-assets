"""
app.py — Workload Watchtower FastAPI entry point.

Serves the REST API under /api and the built React SPA for everything else.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.db import pool
from server.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True, timeout=30.0)
    yield
    pool.close()


app = FastAPI(title="Workload Watchtower", lifespan=lifespan)
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
