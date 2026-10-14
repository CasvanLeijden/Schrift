import base64
import hashlib
import io
import json
import re
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest

from schrift import security
from schrift.models import ApiToken, NoteVersion, User

HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture
def setup(client):
    ctx = client.app.state.ctx
    secret = security.new_totp_secret()
    with ctx.db.session() as s:
        user = User(
            username="cas",
            password_hash=security.hash_password("geheim-wachtwoord"),
            totp_secret=secret,
            totp_enabled=True,
        )
        s.add(user)
        s.flush()
        s.add(ApiToken(user_id=user.id, name="cli", token_hash=security.token_hash("sk_schrift_t")))
    return {"client": client, "ctx": ctx, "secret": secret}


def rpc(client, method, params=None, token="sk_schrift_t", id_=1):
    r = client.post(
        "/mcp",
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
    )
    assert r.status_code == 200, r.text
    return r.json()


def call(client, tool, **args):
    data = rpc(client, "tools/call", {"name": tool, "arguments": args})
    result = data["result"]
    if result.get("isError"):
        raise AssertionError(result["content"][0]["text"])
    structured = result.get("structuredContent")
    if structured is not None:
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    text = result["content"][0]["text"] if result["content"] else "null"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def call_error(client, tool, **args) -> str:
    data = rpc(client, "tools/call", {"name": tool, "arguments": args})
    assert data["result"]["isError"]
    return data["result"]["content"][0]["text"]


def note_file(ctx, path):
    return (ctx.settings.data_dir / "users" / "1" / "notes" / path).read_text()


def test_requires_auth(setup):
    c = setup["client"]
    r = c.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    r = c.post("/mcp", headers={**HEADERS, "Authorization": "Bearer nope"}, json={})
    assert r.status_code == 401


def test_notes_lifecycle(setup):
    c, ctx = setup["client"], setup["ctx"]
    n = call(
        c,
        "create_note",
        title="Klant X",
        content="Info over klant X.",
        folder="ZOKA/Klanten",
        tags=["klant"],
    )
    assert n["path"] == "ZOKA/Klanten/Klant X.md"
    assert note_file(ctx, n["path"]).endswith("# Klant X\n\nInfo over klant X.\n")
    got = call(c, "get_note", note="ZOKA/Klanten/Klant X")
    assert got["content"].startswith("# Klant X") and got["tags"] == ["klant"]

    call(c, "update_note", note="Klant X", mode="append", content="## Contact\n\nJan")
    call(
        c, "update_note", note="Klant X", mode="replace_section", section="Contact", content="Piet"
    )
    body = call(c, "get_note", note="Klant X")["content"]
    assert "## Contact\n\nPiet" in body and "Jan" not in body

    call(c, "edit_note", note="Klant X", old_text="Piet", new_text="Piet de Vries")
    assert "Piet de Vries" in call(c, "get_note", note="Klant X")["content"]
    assert "occurs 0 times" in call_error(
        c, "edit_note", note="Klant X", old_text="zzz", new_text="y"
    )

    versions = call(c, "list_versions", note="Klant X")
    assert all(v["source"] == "mcp" for v in versions)
    call(c, "restore_version", note="Klant X", version_id=versions[-1]["id"])
    assert "Contact" not in call(c, "get_note", note="Klant X")["content"]

    call(c, "move_note", note="Klant X", folder="Archief")
    assert call(c, "get_note", note="Klant X")["path"] == "Archief/Klant X.md"
    assert "trash" in call(c, "delete_note", note="Klant X")
    assert "niet gevonden" in call_error(c, "get_note", note="Klant X")
    call(c, "restore_note", note_id=n["id"])
    assert call(c, "search_notes", query="klant")[0]["title"] == "Klant X"
    assert "Archief" in call(c, "list_folders")
    assert call(c, "list_tags") == ["klant"]


def test_web_sees_claude_as_author(setup):
    c, ctx = setup["client"], setup["ctx"]
    call(c, "create_note", title="Plan", content="tekst")
    with ctx.db.session() as s:
        assert {v.source for v in s.query(NoteVersion)} == {"mcp"}


def test_tasks(setup):
    c, ctx = setup["client"], setup["ctx"]
    t = call(c, "create_task", title="Bellen", due="2026-10-20 09:00")
    assert t["context"] == "Inbox"
    assert f"- [ ] Bellen 📅 2026-10-20 09:00 🆔 {t['id']}" in note_file(ctx, "Inbox.md")
    call(c, "create_note", title="Project", content="## Taken\n\n- [ ] Plan maken", folder="ZOKA")
    t2 = call(c, "create_task", title="Offerte", note="Project", section="Taken")
    assert t2["context"] == "ZOKA"
    assert "- [ ] Plan maken" in note_file(ctx, "ZOKA/Project.md")
    open_tasks = call(c, "list_tasks", context="ZOKA")
    assert {x["title"] for x in open_tasks} == {"Plan maken", "Offerte"}
    call(c, "complete_task", task_id=t2["id"])
    assert "- [x] Offerte ✅" in note_file(ctx, "ZOKA/Project.md")
    assert [x["title"] for x in call(c, "list_tasks", status="done")] == ["Offerte"]
    call(c, "update_task", task_id=t["id"], clear_due=True, title="Mama bellen")
    assert f"- [ ] Mama bellen 🆔 {t['id']}" in note_file(ctx, "Inbox.md")
    call(c, "delete_task", task_id=t["id"])
    assert "Mama" not in note_file(ctx, "Inbox.md")
    overview = call(c, "overview")
    assert "due_today" in overview and overview["recent_notes"]


def test_meeting_report(setup):
    c, ctx = setup["client"], setup["ctx"]
    r = call(
        c,
        "create_meeting_report",
        context="ZOKA",
        title="Overleg Klant X",
        meeting_date="2026-10-14",
        participants=["Cas", "Jan"],
        summary="Fase 2 start dit kwartaal.",
        decisions=["Start 1 december"],
        my_actions=[{"task": "Offerte sturen", "due": "2026-10-20"}],
        others_actions=[{"who": "Jan", "action": "contract nalezen"}],
        raw_notes="jan - budget ok",
        related_note="Klant X",
    )
    assert r["path"] == "ZOKA/Verslagen/2026-10-14 Overleg Klant X.md"
    assert [t["title"] for t in r["tasks"]] == ["Offerte sturen"]
    text = note_file(ctx, r["path"])
    assert "**Over:** [[Klant X]]" in text
    assert "- [ ] Offerte sturen 📅 2026-10-20 🆔" in text
    assert "- Jan: contract nalezen" in text
    assert "<details>\n<summary>Ruwe aantekeningen</summary>\n\njan - budget ok" in text


def test_import_and_attachments(setup):
    c, ctx = setup["client"], setup["ctx"]
    result = call(
        c,
        "import_notes",
        notes=[
            {"path": "Prive/Recepten/Pasta", "content": "Kook 10 min.", "tags": ["eten"]},
            {"path": "Prive/Boodschappen", "content": "# Boodschappen\n\n- [ ] Melk"},
        ],
    )
    assert [r["path"] for r in result] == ["Prive/Recepten/Pasta.md", "Prive/Boodschappen.md"]
    assert call(c, "list_tasks")[0]["title"] == "Melk"
    a = call(c, "add_attachment", note="Pasta", filename="recept.txt", text="Saus met tomaat")
    assert a["has_text"] and "recept.txt" in note_file(ctx, "Prive/Recepten/Pasta.md")
    assert call(c, "get_attachment", attachment_id=a["id"])["text"] == "Saus met tomaat"
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    img = call(c, "add_attachment", note="Pasta", filename="foto.png", content_base64=png)
    assert img["markdown"].startswith("![foto.png](/attachments/")
    assert call(c, "get_note", note="Pasta")["attachments"][1]["is_image"]
    assert call(c, "search_notes", query="tomaat")[0]["title"] == "Pasta"


def test_note_images_are_returned_scaled_down(setup):
    from PIL import Image

    c = setup["client"]
    buf = io.BytesIO()
    Image.new("RGB", (3000, 1500), "red").save(buf, "PNG")
    call(c, "create_note", title="Schets", content="Zie foto")
    call(
        c,
        "add_attachment",
        note="Schets",
        filename="schets.png",
        content_base64=base64.b64encode(buf.getvalue()).decode(),
    )
    data = rpc(c, "tools/call", {"name": "get_note_images", "arguments": {"note": "Schets"}})
    [block] = data["result"]["content"]
    assert block["type"] == "image" and block["mimeType"] == "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(block["data"]))) as img:
        assert img.size == (1568, 784)


def test_prompts(setup):
    c = setup["client"]
    names = [p["name"] for p in rpc(c, "prompts/list")["result"]["prompts"]]
    assert names == ["gespreksverslag", "importeren"]
    msg = rpc(
        c,
        "prompts/get",
        {"name": "gespreksverslag", "arguments": {"aantekeningen": "x", "context": "ZOKA"}},
    )["result"]["messages"][0]["content"]["text"]
    assert "create_meeting_report" in msg and "ZOKA" in msg


def test_oauth_flow(setup):
    c, secret = setup["client"], setup["secret"]
    meta = c.get("/.well-known/oauth-authorization-server").json()
    reg = c.post(
        "/register",
        json={
            "client_name": "Claude",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier = "v" * 64
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    r = c.get(
        urlparse(meta["authorization_endpoint"]).path,
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": "http://localhost/mcp",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    consent_url = r.headers["location"]
    assert "/oauth/consent?r=" in consent_url
    consent_path = consent_url.replace("http://localhost", "")

    # not logged in yet: sent to the login page, which comes back to the consent page
    r = c.get(consent_path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    c.post("/login", data={"username": "cas", "password": "geheim-wachtwoord"})
    r = c.post("/login/2fa", data={"code": pyotp.TOTP(secret).now()}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get(consent_path)
    assert "Claude wil toegang tot Schrift" in page.text
    signed = re.search(r'name="r" value="([^"]+)"', page.text).group(1)
    r = c.post("/oauth/consent", data={"r": signed, "decision": "allow"}, follow_redirects=False)
    target = urlparse(r.headers["location"])
    assert target.netloc == "claude.ai"
    query = parse_qs(target.query)
    assert query["state"] == ["xyz"]

    tokens = c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": "http://localhost/mcp",
        },
    )
    assert tokens.status_code == 200, tokens.text
    access = tokens.json()["access_token"]
    tools = rpc(c, "tools/list", token=access)["result"]["tools"]
    assert any(t["name"] == "create_meeting_report" for t in tools)

    refreshed = c.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens.json()["refresh_token"],
            "client_id": client_id,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    assert rpc(c, "tools/list", token=refreshed.json()["access_token"])["result"]["tools"]
    # the old access token was rotated out
    r = c.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {access}"}, json={})
    assert r.status_code == 401
