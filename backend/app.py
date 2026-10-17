"""FastAPI entry point. Run with `python backend/app.py` (port from PORT, default 8000)."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # allow `python backend/app.py` from any directory
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402
from starlette.formparsers import MultiPartParser  # noqa: E402

from backend.ai.explainer import ai_available, llm_settings  # noqa: E402
from backend.analyzers.pii_scanner import detector_status  # noqa: E402
from backend.generators.data_card import generate_html, generate_markdown  # noqa: E402
from backend.models.schemas import AnalysisSettings  # noqa: E402
from backend.orchestrator import MAX_BYTES, DatasetError, analyze_dataframe, run_analysis  # noqa: E402
from backend.session_store import SessionStore  # noqa: E402

logger = logging.getLogger("dataset_auditor")

FRONTEND_DIR = ROOT / "frontend"
SAMPLE_DIR = ROOT / "sample_data"
SAMPLE_DESCRIPTIONS = {
    "problematic_hiring.csv": "Synthetic hiring data with planted bias, missing data and fake PII.",
    "clean_survey.csv": "Synthetic customer survey with no PII and balanced classes.",
}

# Uploads stay in memory: the request body is capped below, and the multipart spool limit is
# set above that cap so Starlette never rolls an upload over into a temporary file on disk.
MAX_BODY_BYTES = MAX_BYTES + 64 * 1024
MultiPartParser.spool_max_size = MAX_BODY_BYTES + 1
MultiPartParser.max_part_size = 64 * 1024  # non-file form fields

store = SessionStore()


# --- Middleware and dependencies ---------------------------------------------------------

class BodySizeLimitMiddleware:
    """Reject request bodies over the limit before they are parsed or buffered."""

    def __init__(self, app, max_body: int):
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_body:
            response = JSONResponse({"detail": "File is larger than the 10 MB limit."}, status_code=413)
            return await response(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body:
                    raise HTTPException(status_code=413, detail="File is larger than the 10 MB limit.")
            return message

        await self.app(scope, limited_receive, send)


class RateLimiter:
    """Sliding-window limit per client IP, kept in memory."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Record a hit; return seconds to wait if the client is over the limit."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return self.window - (now - hits[0])
            hits.append(now)
            if len(self._hits) > 10_000:  # drop idle clients so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")))


def rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    wait = rate_limiter.check(client)
    if wait is not None:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a minute and try again.",
                            headers={"Retry-After": str(max(1, int(wait) + 1))})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the NER model once at startup so the first upload is not slow.
    await run_in_threadpool(detector_status)
    yield


app = FastAPI(title="Dataset Bias & Privacy Auditor", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(BodySizeLimitMiddleware, max_body=MAX_BODY_BYTES)
if os.getenv("DEV_CORS", "").strip().lower() in {"1", "true", "yes"}:
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5500").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])


@app.middleware("http")
async def errors_and_security_headers(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:
        # Handled here so the traceback (whose message can echo cell values) is never logged.
        logger.error("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        response = JSONResponse({"detail": "Something went wrong while analysing the file."}, status_code=500)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    return response


@app.exception_handler(DatasetError)
async def dataset_error_handler(_request: Request, exc: DatasetError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse({"detail": "Invalid request.", "errors": errors}, status_code=422)


# --- Helpers -----------------------------------------------------------------------------

def parse_protected(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            raise DatasetError("protected_columns must be a JSON list or comma-separated names.") from None
        return [str(v) for v in values if str(v).strip()]
    return [part.strip() for part in raw.split(",") if part.strip()]


def settings_from_form(label: str | None, protected: str | None) -> AnalysisSettings | None:
    label = (label or "").strip() or None
    columns = parse_protected(protected)
    if label is None and not columns:
        return None
    return AnalysisSettings(label_column=label, protected_columns=columns)


def report_response(report) -> JSONResponse:
    return JSONResponse(report.model_dump(mode="json"))


def require_session(session_id: str):
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired. Please upload the file again.")
    return session


def sample_files() -> dict[str, Path]:
    if not SAMPLE_DIR.is_dir():
        return {}
    order = list(SAMPLE_DESCRIPTIONS)  # known samples first, in a fixed order
    paths = sorted(SAMPLE_DIR.glob("*.csv"),
                   key=lambda p: (order.index(p.name) if p.name in order else len(order), p.name))
    return {p.name: p for p in paths if p.is_file()}


async def analyze_and_store(data: bytes, name: str, settings: AnalysisSettings | None):
    report, df = await run_in_threadpool(run_analysis, data, name, settings)
    store.save(report, df)
    return report_response(report)


# --- Routes ------------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    llm = llm_settings()
    return {
        "status": "ok",
        "ai_enabled": ai_available(),
        "llm_model": llm["model"] if llm["api_key_set"] else None,
        "pii_detectors": detector_status(),
        "active_sessions": len(store),
    }


@app.post("/api/upload", dependencies=[Depends(rate_limit)])
async def upload(request: Request):
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(status_code=400, detail="Send the file as multipart/form-data in a 'file' field.")
    async with request.form(max_files=1, max_fields=5) as form:
        upload_file = form.get("file")
        if upload_file is None or isinstance(upload_file, str):
            raise HTTPException(status_code=400, detail="No file was uploaded.")
        filename = upload_file.filename or ""
        if not filename.lower().endswith(".csv"):
            raise HTTPException(status_code=415, detail="Only .csv files are supported.")
        data = await upload_file.read()
        settings = settings_from_form(form.get("label_column"), form.get("protected_columns"))
    if b"\x00" in data[:4096]:
        raise HTTPException(status_code=415, detail="This file looks binary, not like a CSV text file.")
    return await analyze_and_store(data, filename, settings)


@app.post("/api/reanalyze/{session_id}", dependencies=[Depends(rate_limit)])
async def reanalyze(session_id: str, settings: AnalysisSettings):
    session = require_session(session_id)
    report = await run_in_threadpool(analyze_dataframe, session.df.copy(), session.dataset_name,
                                     settings, session_id)
    store.save(report, session.df)
    return report_response(report)


@app.get("/api/download/{session_id}/datacard/{fmt}", dependencies=[Depends(rate_limit)])
def download_datacard(session_id: str, fmt: str):
    if fmt not in {"html", "md"}:
        raise HTTPException(status_code=400, detail="Format must be 'html' or 'md'.")
    session = require_session(session_id)
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(session.dataset_name).stem) or "dataset"
    if fmt == "html":
        content, media = generate_html(session.report, session.df), "text/html; charset=utf-8"
    else:
        content, media = generate_markdown(session.report, session.df), "text/markdown; charset=utf-8"
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{stem}_data_card.{fmt}"'})


@app.get("/api/sample-datasets", dependencies=[Depends(rate_limit)])
def sample_datasets() -> dict:
    return {"datasets": [
        {"name": name, "description": SAMPLE_DESCRIPTIONS.get(name, ""), "size_bytes": path.stat().st_size}
        for name, path in sample_files().items()
    ]}


@app.post("/api/analyze-sample/{dataset_name}", dependencies=[Depends(rate_limit)])
async def analyze_sample(dataset_name: str):
    path = sample_files().get(dataset_name)  # exact-name lookup: no path traversal
    if path is None:
        raise HTTPException(status_code=404, detail="Unknown sample dataset.")
    return await analyze_and_store(path.read_bytes(), dataset_name, None)


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
