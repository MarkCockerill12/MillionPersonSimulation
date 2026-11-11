"""Map snapshot files (written by the generator into SIM_DIR).

In Docker, nginx serves /api/sim/* straight from disk with gzip_static, so this route only
matters in local development. It serves the pre-gzipped chunk when the client accepts gzip.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(prefix="/sim", tags=["simulation"])
SIM_DIR = Path(os.environ.get("SIM_DIR", Path(__file__).resolve().parents[3] / "data" / "sim"))
SAFE = re.compile(r"^(manifest\.json|city\.json|chunk-\d{3}\.bin)$")


@router.get("/{name}")
def sim_file(name: str, request: Request):
    if not SAFE.match(name):
        raise HTTPException(404, "Not found")
    path = SIM_DIR / name
    if not path.exists():
        raise HTTPException(404, "No map snapshot yet. Run the generator first.")
    if name.endswith(".json"):
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})
    gz = path.with_name(name + ".gz")
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    if gz.exists() and "gzip" in request.headers.get("accept-encoding", ""):
        return FileResponse(gz, media_type="application/octet-stream", headers={**headers, "Content-Encoding": "gzip"})
    return FileResponse(path, media_type="application/octet-stream", headers=headers)
