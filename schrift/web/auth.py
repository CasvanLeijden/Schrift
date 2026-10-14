from urllib.parse import urlparse

import segno
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schrift import security
from schrift.i18n import translate
from schrift.models import User
from schrift.web.common import (
    SessionDep,
    client_ip,
    current_user_any,
    login_limiter,
    login_user,
    render,
)

router = APIRouter()

BAD_CODE = "Die code klopt niet. Probeer de volgende code uit je app."


def safe_next(target: str | None) -> str:
    if not target:
        return "/"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    return render(request, "auth/login.html", next=safe_next(next), error=None)


@router.post("/login")
def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    session: Session = SessionDep,
):
    lang = request.session.get("lang", "nl")
    keys = (f"ip:{client_ip(request)}", f"user:{username.lower()}")
    if any(login_limiter.blocked(k) for k in keys):
        return render(
            request,
            "auth/login.html",
            next=safe_next(next),
            error=translate(lang, "Te veel pogingen. Probeer het over een kwartier opnieuw."),
            status_code=429,
        )
    user = session.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if user is None or not security.verify_password(user.password_hash, password):
        for k in keys:
            login_limiter.hit(k)
        return render(
            request,
            "auth/login.html",
            next=safe_next(next),
            error=translate(lang, "Gebruikersnaam of wachtwoord klopt niet."),
            status_code=401,
        )
    if user.totp_enabled:
        request.session.clear()
        request.session["pending_uid"] = user.id
        request.session["next"] = safe_next(next)
        return redirect("/login/2fa")
    for k in keys:
        login_limiter.reset(k)
    login_user(request, user)
    if user.must_change_password:
        return redirect("/setup/password")
    return redirect("/setup/2fa")


@router.get("/login/2fa")
def second_factor_page(request: Request):
    if not request.session.get("pending_uid"):
        return redirect("/login")
    return render(request, "auth/2fa.html", error=None)


@router.post("/login/2fa")
def second_factor(request: Request, code: str = Form(""), session: Session = SessionDep):
    uid = request.session.get("pending_uid")
    user = session.get(User, uid) if uid else None
    if user is None:
        return redirect("/login")
    key = f"2fa:{user.id}"
    lang = user.language
    if login_limiter.blocked(key):
        return render(
            request,
            "auth/2fa.html",
            user=None,
            error=translate(lang, "Te veel pogingen. Probeer het over een kwartier opnieuw."),
            status_code=429,
        )
    ok = security.verify_totp(user.totp_secret, code)
    if not ok:
        remaining = security.use_recovery_code(user.recovery_codes or [], code)
        if remaining is not None:
            user.recovery_codes = remaining
            ok = True
    if not ok:
        login_limiter.hit(key)
        return render(
            request,
            "auth/2fa.html",
            error=translate(lang, BAD_CODE),
            status_code=401,
        )
    login_limiter.reset(key)
    target = request.session.get("next", "/")
    login_user(request, user)
    if user.must_change_password:
        return redirect("/setup/password")
    return redirect(safe_next(target))


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@router.get("/setup/password")
def password_page(request: Request, user: User = Depends(current_user_any)):
    return render(request, "auth/password.html", user=user, error=None)


@router.post("/setup/password")
def set_password(
    request: Request,
    password: str = Form(""),
    password2: str = Form(""),
    user: User = Depends(current_user_any),
):
    problem = security.password_problem(password)
    if not problem and password != password2:
        problem = "De wachtwoorden zijn niet gelijk."
    if problem:
        return render(
            request,
            "auth/password.html",
            user=user,
            error=translate(user.language, problem),
            status_code=400,
        )
    user.password_hash = security.hash_password(password)
    user.must_change_password = False
    user.session_version += 1
    login_user(request, user)
    return redirect("/" if user.totp_enabled else "/setup/2fa")


def _qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#000000", light=None)


@router.get("/setup/2fa")
def totp_setup_page(request: Request, user: User = Depends(current_user_any)):
    if user.must_change_password:
        return redirect("/setup/password")
    if user.totp_enabled:
        return redirect("/")
    if not request.session.get("totp_secret"):
        request.session["totp_secret"] = security.new_totp_secret()
    secret = request.session["totp_secret"]
    return render(
        request,
        "auth/totp_setup.html",
        user=user,
        secret=secret,
        qr=_qr_svg(security.totp_uri(secret, user.username)),
        error=None,
    )


@router.post("/setup/2fa")
def totp_setup(request: Request, code: str = Form(""), user: User = Depends(current_user_any)):
    secret = request.session.get("totp_secret")
    if not secret:
        return redirect("/setup/2fa")
    if not security.verify_totp(secret, code):
        return render(
            request,
            "auth/totp_setup.html",
            user=user,
            secret=secret,
            qr=_qr_svg(security.totp_uri(secret, user.username)),
            error=translate(user.language, BAD_CODE),
            status_code=400,
        )
    codes = security.new_recovery_codes()
    user.totp_secret = secret
    user.totp_enabled = True
    user.recovery_codes = [security.token_hash(c) for c in codes]
    request.session.pop("totp_secret", None)
    return render(request, "auth/recovery_codes.html", user=user, codes=codes, next="/")
