"""
Interactive Unity Catalog ERD Viewer — Databricks App backend.

FastAPI serves the ERD graph API, the Genie chat proxy, and the built React SPA.
Catalog scope is configurable via ERD_CATALOGS (defaults to the demo `megacorp` catalog);
queries UC metadata as the app's service principal.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.config import get_catalogs
from server.routes import genie, graph

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


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "app": "UC ERD Viewer", "catalogs": get_catalogs()}


app.include_router(graph.router, prefix="/api")
app.include_router(genie.router, prefix="/api")

# --- serve the built React frontend (frontend/dist) ---
frontend_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "frontend", "dist"))
assets_dir = os.path.join(frontend_dir, "assets")
if os.path.exists(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


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
        return FileResponse(index_path)
    return {
        "message": "Frontend not built. Run: cd frontend && npm install && npm run build",
        "api_docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
