import hashlib
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schrift import __version__
from schrift.context import AppContext
from schrift.i18n import LANGUAGES, translator
from schrift.models import Note, Task, User
from schrift.services.vault import Vault

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

MONTHS = {
    "nl": ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}
DAYS = {
    "nl": ["ma", "di", "wo", "do", "vr", "za", "zo"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}


def due_label(due: str, lang: str = "nl") -> str:
    """'2026-10-20 14:00' -> 'ma 20 okt, 14:00'."""
    lang = lang if lang in MONTHS else "nl"
    try:
        day = date.fromisoformat(due[:10])
    except ValueError:
        return due
    label = f"{DAYS[lang][day.weekday()]} {day.day} {MONTHS[lang][day.month - 1]}"
    if day.year != date.today().year:
        label += f" {day.year}"
    if len(due) > 10:
        label += f", {due[11:16]}"
    return label


def ago(moment: datetime, lang: str = "nl") -> str:
    seconds = (datetime.now(UTC).replace(tzinfo=None) - moment).total_seconds()
    nl = lang != "en"
    if seconds < 90:
        return "zojuist" if nl else "just now"
    if seconds < 3600:
        n = int(seconds // 60)
        return f"{n} minuten geleden" if nl else f"{n} minutes ago"
    if seconds < 86400:
        n = int(seconds // 3600)
        return (
            (f"{n} uur geleden" if nl else f"{n} hours ago")
            if n > 1
            else ("een uur geleden" if nl else "an hour ago")
        )
    n = int(seconds // 86400)
    if n == 1:
        return "gisteren" if nl else "yesterday"
    return f"{n} dagen geleden" if nl else f"{n} days ago"


STATIC_DIR = TEMPLATES_DIR.parent / "static"
_asset_cache: dict[str, tuple[float, str]] = {}


def asset(path: str) -> str:
    """URL for a static file with a content hash, so browsers never use a stale copy."""
    file = STATIC_DIR / path
    try:
        mtime = file.stat().st_mtime
    except OSError:
        return f"/static/{path}"
    cached = _asset_cache.get(path)
    if cached is None or cached[0] != mtime:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
        cached = (mtime, digest)
        _asset_cache[path] = cached
    return f"/static/{path}?v={cached[1]}"


templates.env.globals["asset"] = asset
templates.env.filters["due_label"] = due_label
templates.env.filters["ago"] = ago


class NeedLogin(Exception):
    """Raised by dependencies; turned into a redirect (pages) or 401 (API)."""

    def __init__(self, location: str = "/login"):
        self.location = location


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_session(request: Request) -> Iterator[Session]:
    with get_ctx(request).db.session() as session:
        yield session


SessionDep = Depends(get_session, scope="function")


def _load_user(request: Request, session: Session) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    user = session.get(User, uid)
    if user is None or request.session.get("sv") != user.session_version:
        request.session.clear()
        return None
    return user


def login_user(request: Request, user: User) -> None:
    request.session.clear()
    request.session["uid"] = user.id
    request.session["sv"] = user.session_version


def current_user_any(request: Request, session: Session = SessionDep) -> User:
    """Logged in, but setup (password change, 2FA) may still be pending."""
    user = _load_user(request, session)
    if user is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise NeedLogin(f"/login?next={quote(target, safe='')}")
    return user


def current_user(request: Request, session: Session = SessionDep) -> User:
    user = current_user_any(request, session)
    if user.must_change_password:
        raise NeedLogin("/setup/password")
    if not user.totp_enabled:
        raise NeedLogin("/setup/2fa")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise NeedLogin("/")
    return user


UserDep = Depends(current_user)


def vault_for(request: Request, session: Session, user: User) -> Vault:
    return get_ctx(request).vault(session, user)


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **ctx):
    lang = user.language if user else request.session.get("lang", "nl")
    ctx.setdefault("theme", user.theme if user else "system")
    return templates.TemplateResponse(
        request,
        name,
        {
            "user": user,
            "t": translator(lang),
            "lang": lang,
            "languages": LANGUAGES,
            "version": __version__,
            **ctx,
        },
        status_code=status_code,
    )


def shell_context(vault: Vault, current_note: Note | None = None) -> dict:
    """Data for the sidebar that every app page shows."""
    roots, root_notes = vault.tree()
    inbox = vault.inbox()
    open_tasks = vault.session.scalar(
        select(func.count(Task.id))
        .join(Note, Task.note_id == Note.id)
        .where(Task.user_id == vault.user.id, ~Task.done, Note.deleted_at.is_(None))
    )
    expanded = set((vault.user.ui_state or {}).get("expanded", []))
    if current_note is not None and current_note.folder is not None:
        folder = current_note.folder
        while folder is not None:
            expanded.add(folder.id)
            folder = folder.parent
    tags = sorted(
        {
            t
            for n in vault.session.scalars(
                select(Note.tags).where(Note.user_id == vault.user.id, Note.deleted_at.is_(None))
            )
            for t in (n or [])
        },
        key=str.lower,
    )
    options: list[dict] = []

    def walk(nodes, depth=0):
        for node in nodes:
            options.append({"id": node.folder.id, "path": node.path})
            walk(node.folders, depth + 1)

    walk(roots)
    return {
        "folder_options": options,
        "tree": roots,
        "root_notes": root_notes,
        "inbox": inbox,
        "open_task_count": open_tasks or 0,
        "expanded": expanded,
        "current_note": current_note,
        "all_tags": tags,
    }


class RateLimiter:
    def __init__(self, attempts: int, window: int):
        self.attempts, self.window = attempts, window
        self.hits: dict[str, deque] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.attempts

    def hit(self, key: str) -> None:
        self.hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self.hits.pop(key, None)


login_limiter = RateLimiter(attempts=10, window=15 * 60)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"
