"""OAuth 2.1 authorization server for MCP clients such as claude.ai.

The MCP SDK provides the protocol endpoints (/authorize, /token, /register, metadata);
this provider stores clients, codes and tokens in SQLite and sends the user through
Schrift's own login and a consent page. Personal API tokens (`sk_schrift_…`) are
accepted as bearer tokens too, for clients that cannot do OAuth.
"""

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import delete, select

from schrift import security
from schrift.context import AppContext
from schrift.models import ApiToken, OAuthClient, OAuthCode, User, utcnow
from schrift.models import OAuthToken as TokenRow
from schrift.web.common import current_user, render

SCOPE = "schrift"
ACCESS_TTL = timedelta(hours=8)
REFRESH_TTL = timedelta(days=90)
CODE_TTL = timedelta(minutes=10)
API_CLIENT_ID = "schrift-api-token"


def _serializer(ctx: AppContext) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(ctx.settings.secret_key, salt="schrift-oauth-authorize")


class SchriftOAuthProvider:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.resource = ctx.settings.base_url.rstrip("/") + "/mcp"

    # --- clients -------------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.ctx.db.session() as s:
            row = s.get(OAuthClient, client_id)
            return OAuthClientInformationFull.model_validate(row.info) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with self.ctx.db.session() as s:
            s.merge(
                OAuthClient(
                    client_id=client_info.client_id, info=client_info.model_dump(mode="json")
                )
            )

    # --- authorization -------------------------------------------------------------------
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        request = {
            "client_id": client.client_id,
            "client_name": client.client_name or client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "state": params.state,
            "scopes": params.scopes or [SCOPE],
            "resource": params.resource,
        }
        signed = _serializer(self.ctx).dumps(request)
        return f"{self.ctx.settings.base_url.rstrip('/')}/oauth/consent?r={signed}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self.ctx.db.session() as s:
            row = s.get(OAuthCode, security.token_hash(authorization_code))
            if row is None or row.client_id != client.client_id or row.expires_at < utcnow():
                return None
            data = row.data
            return AuthorizationCode(
                code=authorization_code,
                scopes=data["scopes"],
                expires_at=row.expires_at.timestamp(),
                client_id=row.client_id,
                code_challenge=data["code_challenge"],
                redirect_uri=data["redirect_uri"],
                redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
                resource=data.get("resource"),
                subject=str(row.user_id),
            )

    def _issue(self, s, client_id: str, user_id: int, scopes: list[str]):
        family = secrets.token_hex(8)
        access, refresh = security.new_token("sat_"), security.new_token("srt_")
        now = utcnow()
        s.add(
            TokenRow(
                token_hash=security.token_hash(access),
                kind="access",
                family=family,
                client_id=client_id,
                user_id=user_id,
                scopes=scopes,
                resource=self.resource,
                expires_at=now + ACCESS_TTL,
            )
        )
        s.add(
            TokenRow(
                token_hash=security.token_hash(refresh),
                kind="refresh",
                family=family,
                client_id=client_id,
                user_id=user_id,
                scopes=scopes,
                resource=self.resource,
                expires_at=now + REFRESH_TTL,
            )
        )
        return OAuthToken(
            access_token=access,
            expires_in=int(ACCESS_TTL.total_seconds()),
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with self.ctx.db.session() as s:
            s.execute(
                delete(OAuthCode).where(
                    OAuthCode.code_hash == security.token_hash(authorization_code.code)
                )
            )
            return self._issue(
                s,
                client.client_id,
                int(authorization_code.subject),
                authorization_code.scopes,
            )

    # --- tokens -----------------------------------------------------------------------------
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with self.ctx.db.session() as s:
            row = s.scalar(
                select(TokenRow).where(
                    TokenRow.token_hash == security.token_hash(refresh_token),
                    TokenRow.kind == "refresh",
                )
            )
            if row is None or row.client_id != client.client_id or row.expires_at < utcnow():
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=int(row.expires_at.timestamp()),
                resource=row.resource,
                subject=str(row.user_id),
            )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        with self.ctx.db.session() as s:
            row = s.scalar(
                select(TokenRow).where(
                    TokenRow.token_hash == security.token_hash(refresh_token.token)
                )
            )
            if row is not None:
                s.execute(delete(TokenRow).where(TokenRow.family == row.family))
            return self._issue(
                s,
                client.client_id,
                int(refresh_token.subject),
                scopes or refresh_token.scopes,
            )

    async def load_access_token(self, token: str) -> AccessToken | None:
        with self.ctx.db.session() as s:
            now = utcnow()
            if token.startswith(security.API_TOKEN_PREFIX):
                row = s.scalar(
                    select(ApiToken).where(ApiToken.token_hash == security.token_hash(token))
                )
                if row is None:
                    return None
                if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=5):
                    row.last_used_at = now
                return AccessToken(
                    token=token,
                    client_id=API_CLIENT_ID,
                    scopes=[SCOPE],
                    resource=self.resource,
                    subject=str(row.user_id),
                )
            row = s.scalar(
                select(TokenRow).where(
                    TokenRow.token_hash == security.token_hash(token), TokenRow.kind == "access"
                )
            )
            if row is None or row.expires_at < now or s.get(User, row.user_id) is None:
                return None
            if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=5):
                row.last_used_at = now
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=int(row.expires_at.timestamp()),
                resource=row.resource,
                subject=str(row.user_id),
            )

    async def revoke_token(self, token) -> None:
        with self.ctx.db.session() as s:
            row = s.scalar(
                select(TokenRow).where(TokenRow.token_hash == security.token_hash(token.token))
            )
            if row is not None:
                s.execute(delete(TokenRow).where(TokenRow.family == row.family))


# --- consent page ----------------------------------------------------------------------------

router = APIRouter()


def _load_request(request: Request, signed: str) -> dict | None:
    ctx: AppContext = request.app.state.ctx
    try:
        return _serializer(ctx).loads(signed, max_age=int(CODE_TTL.total_seconds()) * 3)
    except BadSignature:
        return None


@router.get("/oauth/consent")
def consent_page(request: Request, r: str, user: User = Depends(current_user)):
    data = _load_request(request, r)
    if data is None:
        return render(request, "auth/consent.html", user=user, data=None, signed=r, status_code=400)
    return render(request, "auth/consent.html", user=user, data=data, signed=r)


@router.post("/oauth/consent")
def consent(
    request: Request,
    r: str = Form(...),
    decision: str = Form("deny"),
    user: User = Depends(current_user),
):
    data = _load_request(request, r)
    if data is None:
        return RedirectResponse("/", status_code=303)
    if decision != "allow":
        url = construct_redirect_uri(
            data["redirect_uri"], error="access_denied", state=data.get("state")
        )
        return RedirectResponse(url, status_code=303)
    ctx: AppContext = request.app.state.ctx
    code = secrets.token_urlsafe(32)
    with ctx.db.session() as s:
        s.add(
            OAuthCode(
                code_hash=security.token_hash(code),
                client_id=data["client_id"],
                user_id=user.id,
                data=data,
                expires_at=utcnow() + CODE_TTL,
            )
        )
    url = construct_redirect_uri(data["redirect_uri"], code=code, state=data.get("state"))
    return RedirectResponse(url, status_code=303)
