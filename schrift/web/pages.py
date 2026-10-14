from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schrift import security
from schrift.i18n import translate
from schrift.models import ApiToken, AppPassword, Link, Note, OAuthClient, OAuthToken, Task, User
from schrift.services import attachments as att
from schrift.services.search import hybrid
from schrift.services.vault import VaultError
from schrift.web.api import note_payload
from schrift.web.common import (
    SessionDep,
    UserDep,
    get_ctx,
    render,
    require_admin,
    shell_context,
    vault_for,
)

router = APIRouter()

SAFE_INLINE = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
    "audio/mpeg",
    "video/mp4",
}


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def flash(request: Request, message: str, kind: str = "ok", **extra) -> None:
    request.session["flash"] = {"message": message, "kind": kind, **extra}


def pop_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)


# --- notes ------------------------------------------------------------


@router.get("/")
def home(request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    last = (user.ui_state or {}).get("last_note")
    if last:
        note = session.get(Note, last)
        if note and note.user_id == user.id and note.deleted_at is None:
            return redirect(f"/n/{note.id}")
    return redirect(f"/n/{vault.inbox().id}")


@router.get("/inbox")
def inbox(request: Request, session: Session = SessionDep, user: User = UserDep):
    return redirect(f"/n/{vault_for(request, session, user).inbox().id}")


def backlinks(session: Session, note: Note) -> list[Note]:
    return list(
        session.scalars(
            select(Note)
            .join(Link, Link.source_id == Note.id)
            .where(
                Link.target_title == note.title.lower(),
                Note.user_id == note.user_id,
                Note.deleted_at.is_(None),
                Note.id != note.id,
            )
            .order_by(Note.updated_at.desc())
            .distinct()
        )
    )


@router.get("/n/{note_id}")
def note_page(note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    try:
        note = vault.get_note(note_id)
    except VaultError:
        return render(
            request,
            "not_found.html",
            user=user,
            status_code=404,
            **shell_context(vault),
        )
    user.ui_state = {**(user.ui_state or {}), "last_note": note.id}
    payload = note_payload(vault, note)
    crumbs = []
    folder = note.folder
    while folder is not None:
        crumbs.insert(0, folder)
        folder = folder.parent
    return render(
        request,
        "note.html",
        user=user,
        note=note,
        crumbs=crumbs,
        payload=payload,
        backlinks=backlinks(session, note),
        attachments=att.note_attachments(vault, note.id),
        attachment_url=att.attachment_url,
        flash=pop_flash(request),
        **shell_context(vault, note),
    )


@router.get("/go")
def go(
    title: str,
    request: Request,
    origin: str | None = None,
    session: Session = SessionDep,
    user: User = UserDep,
):
    """Follow a [[wikilink]]; create the note when it does not exist yet."""
    vault = vault_for(request, session, user)
    match = session.scalar(
        select(Note)
        .where(
            Note.user_id == user.id,
            Note.deleted_at.is_(None),
            func.lower(Note.title) == title.strip().lower(),
        )
        .order_by(Note.updated_at.desc())
    )
    if match is None:
        folder = None
        if origin:
            source = session.get(Note, origin)
            if source and source.user_id == user.id and not source.is_inbox:
                folder = source.folder
        match = vault.create_note(title.strip() or "Naamloos", folder=folder)
    return redirect(f"/n/{match.id}")


@router.get("/attachments/{attachment_id}/{filename}")
def attachment(
    attachment_id: str,
    filename: str,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        item = att.get_attachment(vault, attachment_id)
    except VaultError:
        return Response(status_code=404)
    path = att.attachment_path(vault, item)
    if not path.exists():
        return Response(status_code=404)
    inline = item.content_type in SAFE_INLINE
    return FileResponse(
        path,
        media_type=item.content_type if inline else "application/octet-stream",
        filename=item.filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


# --- tasks ------------------------------------------------------------


def task_groups(tasks: list[Task], today: date) -> list[tuple[str, list[Task]]]:
    groups: dict[str, list[Task]] = {
        k: []
        for k in ("Verlopen", "Vandaag", "Komende 7 dagen", "Later", "Zonder datum", "Afgerond")
    }
    week = (today + timedelta(days=7)).isoformat()
    t = today.isoformat()
    for task in tasks:
        day = task.due[:10] if task.due else None
        if task.done:
            groups["Afgerond"].append(task)
        elif day is None:
            groups["Zonder datum"].append(task)
        elif day < t:
            groups["Verlopen"].append(task)
        elif day == t:
            groups["Vandaag"].append(task)
        elif day <= week:
            groups["Komende 7 dagen"].append(task)
        else:
            groups["Later"].append(task)
    groups["Afgerond"].sort(key=lambda x: x.done_on or "", reverse=True)
    return [(k, v) for k, v in groups.items() if v]


def user_today(user: User) -> date:
    try:
        return datetime.now(ZoneInfo(user.timezone)).date()
    except Exception:
        return date.today()


@router.get("/tasks")
def tasks_page(
    request: Request,
    f: str = "",
    done: int = 0,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    tasks = vault.tasks(include_done=bool(done))
    contexts = [tf.folder.name for tf in vault.tree()[0]]

    def context_of(task: Task) -> str:
        if task.note.is_inbox:
            return "Inbox"
        top = vault.top_folder(task.note.folder)
        return top.name if top else ""

    if f:
        tasks = [t for t in tasks if context_of(t) == f]
    return render(
        request,
        "tasks.html",
        user=user,
        groups=task_groups(tasks, user_today(user)),
        contexts=contexts,
        active_filter=f,
        show_done=bool(done),
        context_of=context_of,
        today=user_today(user).isoformat(),
        flash=pop_flash(request),
        **shell_context(vault),
    )


@router.post("/tasks")
def quick_add_task(
    request: Request,
    title: str = Form(""),
    due: str = Form(""),
    f: str = Form(""),
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    if title.strip():
        vault.create_task(title, due=due or None)
    return redirect("/tasks" + (f"?f={f}" if f else ""))


# --- search & trash ------------------------------------------------------------


@router.get("/search")
def search_page(request: Request, q: str = "", session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    results = hybrid(session, user.id, q, semantic=get_ctx(request).semantic) if q.strip() else []
    return render(request, "search.html", user=user, q=q, results=results, **shell_context(vault))


@router.get("/trash")
def trash_page(request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    return render(
        request,
        "trash.html",
        user=user,
        notes=vault.trash(),
        flash=pop_flash(request),
        **shell_context(vault),
    )


@router.post("/trash/{note_id}/restore")
def trash_restore(
    note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    note = vault.restore_note(vault.get_note(note_id, include_deleted=True))
    return redirect(f"/n/{note.id}")


@router.post("/trash/{note_id}/purge")
def trash_purge(
    note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    note = vault.get_note(note_id, include_deleted=True)
    if note.deleted_at is not None:
        vault.purge_note(note)
        flash(request, translate(user.language, "Definitief verwijderd."))
    return redirect("/trash")


@router.post("/trash/empty")
def trash_empty(request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    for note in vault.trash():
        vault.purge_note(note)
    flash(request, translate(user.language, "Prullenbak geleegd."))
    return redirect("/trash")


# --- settings ------------------------------------------------------------


def settings_context(request: Request, session: Session, user: User) -> dict:
    ctx = get_ctx(request)
    base = ctx.settings.base_url.rstrip("/")
    users = list(session.scalars(select(User).order_by(User.username))) if user.is_admin else []
    clients = session.execute(
        select(OAuthClient.client_id, OAuthClient.info, func.max(OAuthToken.last_used_at))
        .join(OAuthToken, OAuthToken.client_id == OAuthClient.client_id)
        .where(OAuthToken.user_id == user.id)
        .group_by(OAuthClient.client_id)
    ).all()
    return {
        "base_url": base,
        "app_passwords": list(
            session.scalars(
                select(AppPassword).where(AppPassword.user_id == user.id).order_by(AppPassword.id)
            )
        ),
        "api_tokens": list(
            session.scalars(
                select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id)
            )
        ),
        "oauth_clients": [
            {"id": c[0], "name": (c[1] or {}).get("client_name") or c[0], "last_used": c[2]}
            for c in clients
        ],
        "users": users,
        "timezones": ["Europe/Amsterdam", "Europe/Brussels", "Europe/London", "UTC"],
    }


@router.get("/settings")
def settings_page(request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    return render(
        request,
        "settings.html",
        user=user,
        flash=pop_flash(request),
        **settings_context(request, session, user),
        **shell_context(vault),
    )


@router.post("/settings/general")
def settings_general(
    request: Request,
    language: str = Form("nl"),
    theme: str = Form("system"),
    timezone: str = Form("Europe/Amsterdam"),
    user: User = UserDep,
):
    if language in ("nl", "en"):
        user.language = language
    if theme in ("system", "light", "dark"):
        user.theme = theme
    try:
        ZoneInfo(timezone)
        user.timezone = timezone
    except Exception:
        pass
    flash(request, translate(user.language, "Opgeslagen."))
    return redirect("/settings#algemeen")


@router.post("/settings/theme")
def settings_theme(theme: str = Form("system"), user: User = UserDep):
    if theme in ("system", "light", "dark"):
        user.theme = theme
    return {"ok": True}


@router.post("/settings/password")
def settings_password(
    request: Request,
    current: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    user: User = UserDep,
):
    t = lambda s: translate(user.language, s)  # noqa: E731
    if not security.verify_password(user.password_hash, current):
        flash(request, t("Je huidige wachtwoord klopt niet."), "error")
    elif problem := security.password_problem(password):
        flash(request, t(problem), "error")
    elif password != password2:
        flash(request, t("De wachtwoorden zijn niet gelijk."), "error")
    else:
        user.password_hash = security.hash_password(password)
        user.session_version += 1
        request.session["sv"] = user.session_version
        flash(request, t("Wachtwoord gewijzigd. Andere sessies zijn uitgelogd."))
    return redirect("/settings#beveiliging")


@router.post("/settings/recovery-codes")
def settings_recovery(request: Request, code: str = Form(""), user: User = UserDep):
    if not security.verify_totp(user.totp_secret, code):
        flash(request, translate(user.language, "Die code klopt niet."), "error")
        return redirect("/settings#beveiliging")
    codes = security.new_recovery_codes()
    user.recovery_codes = [security.token_hash(c) for c in codes]
    return render(request, "auth/recovery_codes.html", user=user, codes=codes, next="/settings")


def _secret_flash(request: Request, user: User, label: str, secret: str, anchor: str, **kw):
    flash(request, translate(user.language, label, **kw), "secret", secret=secret, anchor=anchor)
    return redirect(f"/settings#{anchor}")


@router.post("/settings/app-passwords")
def create_app_password(
    request: Request, name: str = Form(""), session: Session = SessionDep, user: User = UserDep
):
    password = security.new_app_password()
    session.add(
        AppPassword(
            user_id=user.id,
            name=name.strip()[:100] or "Apparaat",
            token_hash=security.token_hash(password),
        )
    )
    return _secret_flash(request, user, "Nieuw app-wachtwoord", password, "caldav")


@router.post("/settings/app-passwords/{item_id}/delete")
def delete_app_password(
    item_id: int, request: Request, session: Session = SessionDep, user: User = UserDep
):
    item = session.get(AppPassword, item_id)
    if item and item.user_id == user.id:
        session.delete(item)
        flash(request, translate(user.language, "Ingetrokken."))
    return redirect("/settings#caldav")


@router.post("/settings/tokens")
def create_token(
    request: Request, name: str = Form(""), session: Session = SessionDep, user: User = UserDep
):
    token = security.new_token(security.API_TOKEN_PREFIX)
    session.add(
        ApiToken(
            user_id=user.id,
            name=name.strip()[:100] or "Token",
            token_hash=security.token_hash(token),
        )
    )
    return _secret_flash(request, user, "Nieuw token", token, "mcp")


@router.post("/settings/tokens/{item_id}/delete")
def delete_token(
    item_id: int, request: Request, session: Session = SessionDep, user: User = UserDep
):
    item = session.get(ApiToken, item_id)
    if item and item.user_id == user.id:
        session.delete(item)
        flash(request, translate(user.language, "Ingetrokken."))
    return redirect("/settings#mcp")


@router.post("/settings/clients/{client_id}/delete")
def delete_client(
    client_id: str, request: Request, session: Session = SessionDep, user: User = UserDep
):
    for token in session.scalars(
        select(OAuthToken).where(OAuthToken.client_id == client_id, OAuthToken.user_id == user.id)
    ):
        session.delete(token)
    flash(request, translate(user.language, "Ontkoppeld."))
    return redirect("/settings#mcp")


# --- admin ------------------------------------------------------------


@router.post("/settings/users")
def admin_create_user(
    request: Request,
    username: str = Form(""),
    session: Session = SessionDep,
    admin: User = Depends(require_admin),
):
    t = lambda s: translate(admin.language, s)  # noqa: E731
    username = username.strip()
    if (
        not username
        or len(username) > 64
        or not username.replace(".", "").replace("-", "").replace("_", "").isalnum()
    ):
        flash(request, t("Gebruik alleen letters, cijfers, punt, streepje of underscore."), "error")
        return redirect("/settings#gebruikers")
    if session.scalar(select(User).where(func.lower(User.username) == username.lower())):
        flash(request, t("Die gebruikersnaam bestaat al."), "error")
        return redirect("/settings#gebruikers")
    password = security.new_app_password()
    session.add(
        User(
            username=username,
            password_hash=security.hash_password(password),
            must_change_password=True,
            language=admin.language,
        )
    )
    return _secret_flash(
        request, admin, "Tijdelijk wachtwoord voor {name}", password, "gebruikers", name=username
    )


@router.post("/settings/users/{user_id}/reset")
def admin_reset_user(
    user_id: int,
    request: Request,
    session: Session = SessionDep,
    admin: User = Depends(require_admin),
):
    target = session.get(User, user_id)
    if target is None:
        return redirect("/settings#gebruikers")
    password = security.new_app_password()
    target.password_hash = security.hash_password(password)
    target.must_change_password = True
    target.totp_enabled = False
    target.totp_secret = None
    target.recovery_codes = []
    target.session_version += 1
    if target.id == admin.id:
        request.session["sv"] = target.session_version
    return _secret_flash(
        request,
        admin,
        "Tijdelijk wachtwoord voor {name}",
        password,
        "gebruikers",
        name=target.username,
    )


@router.post("/settings/users/{user_id}/delete")
def admin_delete_user(
    user_id: int,
    request: Request,
    session: Session = SessionDep,
    admin: User = Depends(require_admin),
):
    target = session.get(User, user_id)
    if target is not None and target.id != admin.id:
        import shutil

        from sqlalchemy import text

        session.execute(text("DELETE FROM notes_fts WHERE user_id = :u"), {"u": target.id})
        session.delete(target)
        shutil.rmtree(
            get_ctx(request).settings.data_dir / "users" / str(target.id), ignore_errors=True
        )
        flash(request, translate(admin.language, "Gebruiker verwijderd."))
    return redirect("/settings#gebruikers")
