"""
Interactive Unity Catalog ERD Viewer — Databricks App backend.

FastAPI serves the ERD graph API, the Genie chat proxy, and the built React SPA.
Catalog scope is configurable via ERD_CATALOGS (defaults to the demo `megacorp` catalog);
queries UC metadata as the app's service principal.
"""
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.config import get_catalogs
from server.routes import admin, genie, graph, insights

# Logging setup so the per-query/per-request timing (logger name "erd", used here and in
# server/graph.py) actually surfaces in `databricks apps logs`. Under `uvicorn app:app`,
# uvicorn configures logging BEFORE importing this module and does NOT touch the root
# logger, so a bare logging.basicConfig here is unreliable -- records can emit nowhere.
# Instead, bind the "erd" logger to uvicorn's own handlers when they exist; fall back to a
# basic stderr handler for local/non-uvicorn runs (e.g. pytest, `python app.py`).
logger = logging.getLogger("erd")
logger.setLevel(logging.INFO)
_uvicorn_handlers = logging.getLogger("uvicorn.error").handlers
if _uvicorn_handlers:
    logger.handlers = _uvicorn_handlers
    logger.propagate = False
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="UC ERD Viewer",
    description="Interactive entity-relationship diagram for a configurable Unity Catalog allow-list",
    version="1.0.0",
)

# CORS for local Vite dev server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compress responses (the /api/graph JSON is easily hundreds of KB of columns/tags/edges,
# and the built JS bundle is ~650KB). minimum_size skips tiny bodies where framing
# overhead isn't worth it.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def _log_request_timing(request: Request, call_next):
    """Log method/path/status/duration for API calls -- the app previously had no timing
    data at all, so "it's slow" could only be diagnosed by guessing. Static asset fetches
    are skipped to keep the log signal-heavy."""
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("request %s %s -> %d %.0fms", request.method, request.url.path, response.status_code, elapsed_ms)
    return response


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "app": "UC ERD Viewer", "catalogs": get_catalogs()}


app.include_router(graph.router, prefix="/api")
app.include_router(genie.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(insights.router, prefix="/api")


class _CachedStaticFiles(StaticFiles):
    """Vite emits content-hashed asset filenames (index-<hash>.js/.css), so a given URL's
    bytes never change -- serve them with a long immutable cache so browsers skip
    re-downloading on every visit. index.html itself is served by serve_spa with no-cache
    (its content changes each build and points at the current hashed assets)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        # Only on a real hit -- never cache a 404 (or any error) immutably, or a client
        # that briefly requested a since-removed hashed asset would cache the miss for a year.
        if resp.status_code in (200, 206):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


# --- serve the built React frontend (frontend/dist) ---
frontend_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "frontend", "dist"))
assets_dir = os.path.join(frontend_dir, "assets")
if os.path.exists(assets_dir):
    app.mount("/assets", _CachedStaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve the React SPA (with client-side routing fallback).

    full_path is FastAPI's raw catch-all -- it is NOT traversal-sanitized, so a request
    like /../../server/config.py must be rejected here rather than trusted to resolve
    safely under frontend_dir.
    """
    if full_path:
        file_path = os.path.realpath(os.path.join(frontend_dir, full_path))
        if file_path.startswith(frontend_dir + os.sep) and os.path.isfile(file_path):
            return FileResponse(file_path)
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        # no-cache (revalidate every load): index.html is the un-hashed entry point and
        # references the current build's hashed assets, so a stale cached copy would point
        # at assets that no longer exist after a redeploy.
        return FileResponse(index_path, headers={"Cache-Control": "no-cache"})
    return {
        "message": "Frontend not built. Run: cd frontend && npm install && npm run build",
        "api_docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
