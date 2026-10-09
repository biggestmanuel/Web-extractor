"""Scrapely API.

Endpoints
    GET /api/health    service status
    GET /api/extract   extract structured data from a public HTML page

Design notes
    * A user-supplied URL is untrusted input; every fetch goes through
      safety.validate_url and fetcher.fetch_html, which re-checks each redirect.
    * Anonymous callers share a per-IP token bucket, so one client cannot spend
      the whole budget.
    * Repeat requests for the same URL are served from a short-lived cache.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import extractor
import fetcher
import safety
from ratelimit import TokenBucketLimiter, TTLCache

VERSION = "1.1.0"
STATIC_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        return default
    return value if value > 0 else default


RATE_LIMIT_REQUESTS = max(1, _env_int("SCRAPELY_RATE_LIMIT", 10))
RATE_LIMIT_WINDOW = _env_float("SCRAPELY_RATE_WINDOW", 60)
CACHE_TTL = _env_float("SCRAPELY_CACHE_TTL", 300)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("SCRAPELY_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

limiter = TokenBucketLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)
cache = TTLCache(max_entries=256, ttl=CACHE_TTL)

app = FastAPI(title="Scrapely API", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def client_ip(request: Request) -> str:
    """Identify the caller.

    X-Forwarded-For is honoured only when the app is told it runs behind a proxy
    that sets it, otherwise a client could spoof its IP to dodge the limiter.
    """
    if os.environ.get("SCRAPELY_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> str:
    ip = client_ip(request)
    allowed, remaining, retry_after = limiter.check(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {retry_after:.0f}s.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    request.state.remaining = remaining
    return ip


@app.middleware("http")
async def rate_limit_headers(request: Request, call_next):
    response = await call_next(request)
    remaining = getattr(request.state, "remaining", None)
    if remaining is not None:
        response.headers["X-RateLimit-Remaining"] = f"{remaining:.0f}"
    return response


RateLimited = Annotated[str, Depends(enforce_rate_limit)]


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": VERSION,
        "rateLimit": {"requests": RATE_LIMIT_REQUESTS, "windowSeconds": RATE_LIMIT_WINDOW},
        "allowedPorts": sorted(safety.DEFAULT_ALLOWED_PORTS),
        "maxBytes": fetcher.MAX_BYTES,
    }


@app.get("/api/extract")
def extract(url: Annotated[str, Query(min_length=8, max_length=safety.MAX_URL_LENGTH)], _: RateLimited) -> JSONResponse:
    """Extract structured data from a public HTML page."""
    try:
        target = safety.validate_url(url)
    except safety.UnsafeUrlError as exc:
        raise HTTPException(400, str(exc)) from exc

    cached = cache.get(target)
    if cached is not None:
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    try:
        html, final_url = fetcher.fetch_html(target)
    except fetcher.FetchError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    # The redirect target is re-validated by the fetcher, but confirm the final
    # URL still points at a public host before parsing and caching it.
    try:
        safety.validate_url(final_url)
    except safety.UnsafeUrlError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        data = extractor.extract(html, final_url)
    except Exception as exc:  # malformed markup should never crash the service
        raise HTTPException(422, "The page could not be parsed.") from exc

    cache.set(target, data)
    return JSONResponse(data, headers={"X-Cache": "MISS"})


if (STATIC_DIR / "index.html").exists():
    # Mounted last so /api/* routes take precedence; html=True serves index.html at /.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
