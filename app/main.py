"""NetPulse FastAPI application: app factory, middleware and routes."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import db, dns_lookup, subnet, uptime, vlsm
from app.ratelimit import RateLimiter

BASE_DIR = Path(__file__).resolve().parent


class VlsmRequest(BaseModel):
    base: str = Field(examples=["10.0.0.0/24"])
    hosts: list[int] = Field(examples=[[50, 20, 10, 2]])


class CheckRequest(BaseModel):
    url: str = Field(max_length=2048, examples=["https://example.com"])


def create_app(data_dir: str | None = None, rate_limit: int = 30) -> FastAPI:
    """Build the app. Arguments exist so tests can use a temp dir and fresh limiter."""
    version = os.environ.get("APP_VERSION", "dev")
    db_path = db.init_db(data_dir or os.environ.get("DATA_DIR", "./data"))
    limiter = RateLimiter(limit=rate_limit, window_seconds=60)
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    app = FastAPI(title="NetPulse", version=version)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    # ---------- middleware ----------

    @app.middleware("http")
    async def rate_limit_and_headers(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            # Use the TCP peer address. X-Forwarded-For is NOT trusted because
            # any client can forge it; add proxy-aware logic only behind an ALB.
            client_ip = request.client.host if request.client else "unknown"
            if not limiter.allow(client_ip):
                response = JSONResponse(
                    {"detail": "Rate limit exceeded: 30 requests per minute"},
                    status_code=429,
                    headers={"Retry-After": str(limiter.retry_after(client_ip))},
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Turn FastAPI's list-of-errors 422 into one readable message for the UI.
    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        parts = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"] if p not in ("body", "query"))
            parts.append(f"{field}: {err['msg']}" if field else err["msg"])
        return JSONResponse({"detail": "; ".join(parts)}, status_code=422)

    # ---------- pages ----------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request):
        response = templates.TemplateResponse(request, "index.html")
        # Strict CSP on the UI: only our own JS/CSS, no inline scripts.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "version": version}

    # ---------- API ----------

    @app.get("/api/subnet")
    def api_subnet(cidr: str = Query(max_length=64, examples=["192.168.10.0/26"])):
        try:
            return subnet.calculate(cidr)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/vlsm")
    def api_vlsm(body: VlsmRequest):
        try:
            return vlsm.plan(body.base, body.hosts)
        except vlsm.VlsmError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/dns")
    def api_dns(
        domain: str = Query(max_length=253, examples=["example.com"]),
        record_type: str = Query("A", alias="type"),
    ):
        try:
            return dns_lookup.lookup(domain, record_type)
        except dns_lookup.DnsError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/check")
    def api_check(body: CheckRequest):
        try:
            result = uptime.check(body.url)
        except uptime.InvalidUrlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except uptime.BlockedTargetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.save_check(db_path, result)
        return result

    @app.get("/api/checks")
    def api_checks():
        return db.recent_checks(db_path, limit=20)

    return app


app = create_app()
