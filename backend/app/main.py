import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import SessionLocal
from .migrations import upgrade_database
from . import daily
from .routers import auth, daily as daily_routes, problems, submissions, users
from .security import SESSION_COOKIE, csrf_ok
from .seed import seed

log = logging.getLogger("promptcode")
# Show the app's own INFO logs (daily generation progress, etc.) next to uvicorn's.
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    if settings.auto_migrate:
        upgrade_database()
    with SessionLocal() as db:
        seed(db)
    if not settings.gemini_api_key:
        log.warning("GEMINI_API_KEY is not set - running prompts will fail until it is.")
    scheduler = None
    if settings.daily_enabled and settings.daily_scheduler:
        scheduler = asyncio.create_task(daily.scheduler())
    yield
    if scheduler:
        scheduler.cancel()


app = FastAPI(
    title="PromptCode",
    lifespan=lifespan,
    # Interactive docs are handy locally but needless attack surface in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)


# ---------- security middleware ----------

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://accounts.google.com/gsi/client",
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    "frame-src https://accounts.google.com/gsi/",
    "connect-src 'self' https://accounts.google.com/gsi/",
    "img-src 'self' data: https://*.googleusercontent.com",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # non-browser client; cookies + CSRF token still protect sessions
    if origin in settings.cors_origins:
        return True
    host = request.headers.get("host", "")
    return urlsplit(origin).netloc == host


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
        if not _origin_allowed(request):
            return JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
        # Every state-changing request made with a session must carry the CSRF token.
        if request.cookies.get(SESSION_COOKIE) and not csrf_ok(request):
            return JSONResponse(
                {"detail": "Your session security token is missing or stale. Refresh the page and try again."},
                status_code=403,
            )

    response = await call_next(request)

    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # Google's sign-in popup needs to talk back to this window.
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    if settings.cookie_secure:
        headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        headers.setdefault("Cache-Control", "no-store")
    else:
        headers.setdefault("Content-Security-Policy", CSP)
    return response


app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return readable messages plus a field -> message map the UI can show inline."""
    errors, fields = [], {}
    for err in exc.errors():
        loc = [str(p) for p in err["loc"] if p != "body"]
        msg = err["msg"].removeprefix("Value error, ")
        errors.append({"loc": loc, "msg": msg})
        if loc:
            fields.setdefault(".".join(loc), msg)
    return JSONResponse({"detail": errors, "fields": fields}, status_code=422)


app.include_router(auth.router)
app.include_router(problems.router)
app.include_router(submissions.router)
app.include_router(users.router)
app.include_router(daily_routes.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---------- built frontend (single-origin deployment) ----------

if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        file = (FRONTEND_DIST / path).resolve()
        if path and file.is_file() and FRONTEND_DIST in file.parents:
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")
