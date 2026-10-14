import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from schrift import __version__, caldav, oauth
from schrift.config import Settings, get_settings
from schrift.context import AppContext
from schrift.db import Database
from schrift.mcp_server import build_server, mcp_http_app
from schrift.migrate import upgrade
from schrift.models import User
from schrift.web import api, auth, pages
from schrift.web.common import NeedLogin

log = logging.getLogger("schrift")
PACKAGE_DIR = Path(__file__).parent

# Paths that authenticate without the session cookie, so the same-origin check
# for browser forms does not apply to them.
CSRF_EXEMPT = ("/caldav", "/.well-known", "/mcp", "/token", "/register", "/revoke")


def build_context(settings: Settings) -> AppContext:
    base = urlparse(settings.base_url)
    if base.scheme != "https" and base.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(
            f"SCHRIFT_BASE_URL must use https (got {settings.base_url}); "
            "only localhost may use plain http."
        )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_url)
    upgrade(db.engine)
    semantic = None
    if settings.semantic_search:
        from schrift.services.semantic import SemanticIndex

        semantic = SemanticIndex(db, settings.data_dir, settings.embedding_model)
    return AppContext(settings=settings, db=db, semantic=semantic)


def scan_all(ctx: AppContext) -> None:
    """Pick up Markdown files that were added or edited outside Schrift."""
    with ctx.db.session() as session:
        for user in session.scalars(select(User)):
            try:
                changed = ctx.vault(session, user).scan()
                if changed:
                    log.info("Indexed %d changed notes for %s", changed, user.username)
            except Exception:
                log.exception("Scanning notes of %s failed", user.username)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    ctx = build_context(settings)
    mcp = build_server(ctx)
    mcp_app = mcp_http_app(mcp)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scan_all(ctx)
        if ctx.semantic:
            ctx.semantic.start()
            ctx.semantic.schedule_stale()
        async with mcp.session_manager.run():
            yield
        if ctx.semantic:
            ctx.semantic.stop()

    app = FastAPI(
        title="Schrift", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.ctx = ctx
    app.state.settings = settings

    base = urlparse(settings.base_url)
    allowed_origins = {f"{base.scheme}://{base.netloc}", *settings.trusted_origins}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT") and not (
            request.url.path.startswith(CSRF_EXEMPT)
        ):
            origin = request.headers.get("origin")
            if origin is None and (referer := request.headers.get("referer")):
                r = urlparse(referer)
                origin = f"{r.scheme}://{r.netloc}"
            own = f"{request.url.scheme}://{request.url.netloc}"
            if origin is not None and origin not in allowed_origins and origin != own:
                return JSONResponse({"error": "Cross-origin request refused."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/attachments/"):
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="schrift_session",
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=base.scheme == "https",
    )

    @app.exception_handler(NeedLogin)
    async def need_login(request: Request, exc: NeedLogin):
        if request.url.path.startswith("/api/") or request.headers.get("accept", "").startswith(
            "application/json"
        ):
            return JSONResponse({"error": "Niet ingelogd.", "login": exc.location}, status_code=401)
        return RedirectResponse(exc.location, status_code=303)

    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return JSONResponse(
            {
                "name": "Schrift",
                "short_name": "Schrift",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#ffffff",
                "theme_color": "#2443b0",
                "icons": [
                    {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                    {
                        "src": "/static/icons/icon-maskable-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "maskable",
                    },
                ],
            },
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(
            PACKAGE_DIR / "static" / "sw.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "build": os.environ.get("SCHRIFT_BUILD", "dev"),
        }

    @app.api_route("/", methods=["PROPFIND", "OPTIONS"], include_in_schema=False)
    async def root_dav(request: Request):
        return await caldav.dispatch(request, "root", None, None)

    app.include_router(caldav.router)
    app.include_router(auth.router)
    app.include_router(api.router)
    app.include_router(pages.router)
    app.include_router(oauth.router)
    # MCP endpoint and OAuth endpoints (/mcp, /authorize, /token, /register, metadata).
    app.mount("/", mcp_app)
    return HeadAsGet(app)


class HeadAsGet:
    """Answer HEAD like GET without a body; FastAPI only routes the methods it is given.

    CalDAV paths are left alone, they handle HEAD themselves.
    """

    def __init__(self, app: FastAPI):
        self.app = app
        self.state = app.state

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "HEAD"
            or scope["path"].startswith(("/caldav", "/.well-known/caldav"))
        ):
            return await self.app(scope, receive, send)

        async def send_without_body(message):
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


def __getattr__(name: str):
    # `uvicorn schrift.main:app` builds the app lazily, so importing this module
    # (in tests or the CLI) has no side effects.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
