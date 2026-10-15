import re

import pyotp
import pytest

from schrift import security
from schrift.models import User


@pytest.fixture
def web(client):
    """A logged-in browser session for user 'cas' (admin, 2FA on)."""
    ctx = client.app.state.ctx
    secret = security.new_totp_secret()
    with ctx.db.session() as s:
        s.add(
            User(
                username="cas",
                password_hash=security.hash_password("geheim-wachtwoord"),
                is_admin=True,
                totp_secret=secret,
                totp_enabled=True,
            )
        )
    client.post("/login", data={"username": "cas", "password": "geheim-wachtwoord"})
    r = client.post("/login/2fa", data={"code": pyotp.TOTP(secret).now()}, follow_redirects=False)
    assert r.status_code == 303
    client.ctx = ctx
    return client


def test_login_errors_and_rate_limit(client):
    ctx = client.app.state.ctx
    with ctx.db.session() as s:
        s.add(User(username="x", password_hash=security.hash_password("geheim-wachtwoord")))
    r = client.post("/login", data={"username": "x", "password": "fout"})
    assert r.status_code == 401 and "klopt niet" in r.text
    for _ in range(12):
        r = client.post("/login", data={"username": "x", "password": "fout"})
    assert r.status_code == 429


def test_first_login_forces_password_and_2fa(client):
    ctx = client.app.state.ctx
    with ctx.db.session() as s:
        s.add(
            User(
                username="nieuw",
                password_hash=security.hash_password("tijdelijk-ww"),
                must_change_password=True,
            )
        )
    r = client.post(
        "/login", data={"username": "nieuw", "password": "tijdelijk-ww"}, follow_redirects=False
    )
    assert r.headers["location"] == "/setup/password"
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup/password"
    r = client.post("/setup/password", data={"password": "kort", "password2": "kort"})
    assert r.status_code == 400
    r = client.post(
        "/setup/password",
        data={"password": "mijn-eigen-wachtwoord", "password2": "mijn-eigen-wachtwoord"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/setup/2fa"
    page = client.get("/setup/2fa")
    secret = re.search(r"<code[^>]*>([A-Z2-7]+)</code>", page.text).group(1)
    assert client.post("/setup/2fa", data={"code": "000000"}).status_code == 400
    r = client.post("/setup/2fa", data={"code": pyotp.TOTP(secret).now()})
    codes = re.findall(r"<li>([0-9a-f]{6}-[0-9a-f]{6})</li>", r.text)
    assert len(codes) == 10
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/n/")
    # a recovery code works once
    client.post("/logout")
    client.post("/login", data={"username": "nieuw", "password": "mijn-eigen-wachtwoord"})
    assert (
        client.post("/login/2fa", data={"code": codes[0]}, follow_redirects=False).status_code
        == 303
    )
    client.post("/logout")
    client.post("/login", data={"username": "nieuw", "password": "mijn-eigen-wachtwoord"})
    assert client.post("/login/2fa", data={"code": codes[0]}).status_code == 401


def test_note_api_flow(web):
    folder = web.post("/api/folders", json={"name": "ZOKA"}).json()
    note = web.post("/api/notes", json={"title": "", "folderId": folder["id"]}).json()
    data = web.get(f"/api/notes/{note['id']}").json()
    assert data["title"] == "Naamloos"
    saved = web.put(
        f"/api/notes/{note['id']}",
        json={
            "title": "Plan",
            "content": "- [ ] Bellen 📅 2026-10-20\n\nZie \\[\\[Klant\\]\\]",
            "baseHash": data["hash"],
        },
    ).json()
    assert saved["renamed"] and saved["path"] == "ZOKA/Plan.md"
    body = web.get(f"/api/notes/{note['id']}").json()["content"]
    assert "[[Klant]]" in body and "🆔" in body
    # stale base hash -> conflict with the current text
    r = web.put(
        f"/api/notes/{note['id']}",
        json={"title": "Plan", "content": "oud", "baseHash": data["hash"]},
    )
    assert r.status_code == 409 and "Bellen" in r.json()["content"]
    assert web.put(f"/api/notes/{note['id']}/tags", json={"tags": ["#a", "b", "A"]}).json()[
        "tags"
    ] == ["a", "b"]
    task = web.get("/api/titles?q=pla").json()
    assert task[0]["title"] == "Plan"
    short = re.search(r"🆔 (\w+)", body).group(1)
    assert web.patch(f"/api/tasks/{short}", json={"done": True}).json()["done"]
    assert "Bellen" in web.get("/tasks?done=1").text
    assert (
        web.post(f"/api/notes/{note['id']}/move", json={"folderId": None}).json()["path"]
        == "Plan.md"
    )
    assert web.delete(f"/api/notes/{note['id']}").json()["ok"]
    assert "Plan" in web.get("/trash").text
    assert web.post(f"/api/notes/{note['id']}/restore").json()["ok"]
    versions = web.get(f"/api/notes/{note['id']}/versions").json()
    assert (
        versions
        and web.get(f"/api/notes/{note['id']}/versions/{versions[0]['id']}").json()["content"]
    )


def test_pages_render(web):
    web.post("/api/folders", json={"name": "Prive"})
    for path in ("/tasks", "/search?q=x", "/trash", "/settings", "/inbox"):
        r = web.get(path)
        assert r.status_code == 200, path
    assert web.get("/n/onbekend").status_code == 404


def test_wikilink_creates_missing_note(web):
    r = web.get("/go?title=Nieuwe%20pagina", follow_redirects=False)
    assert r.status_code == 303
    again = web.get("/go?title=nieuwe%20pagina", follow_redirects=False)
    assert again.headers["location"] == r.headers["location"]


def test_upload_and_download_attachment(web):
    note = web.post("/api/notes", json={"title": "Met bijlage"}).json()
    r = web.post(
        "/api/attachments",
        files={"file": ("notitie.txt", b"hallo wereld", "text/plain")},
        data={"noteId": note["id"]},
    ).json()
    assert r["markdown"].startswith("[notitie.txt](/attachments/")
    assert web.get(r["url"]).content == b"hallo wereld"
    svg = web.post(
        "/api/attachments", files={"file": ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml")}
    ).json()
    assert not svg["isImage"]
    download = web.get(svg["url"])
    assert download.headers["content-type"] == "application/octet-stream"
    assert "attachment" in download.headers["content-disposition"]


def test_cross_origin_post_is_refused(web):
    r = web.post("/api/notes", json={"title": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_settings_secrets_and_admin(web):
    r = web.post("/settings/app-passwords", data={"name": "iPhone"})
    assert re.search(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", r.text)
    r = web.post("/settings/tokens", data={"name": "Claude Code"})
    assert "sk_schrift_" in r.text and "claude mcp add" in r.text
    r = web.post("/settings/users", data={"username": "jan"})
    assert "Tijdelijk wachtwoord voor jan" in r.text
    assert web.post("/settings/users", data={"username": "jan"}).text.count("bestaat al") == 1
    web.post("/settings/general", data={"language": "en", "theme": "dark", "timezone": "UTC"})
    page = web.get("/settings")
    assert "Settings" in page.text and 'data-theme="dark"' in page.text


def test_delete_attachment_removes_file_and_reference(web):
    note = web.post("/api/notes", json={"title": "Met plaatje"}).json()
    r = web.post(
        "/api/attachments",
        files={"file": ("schets.png", b"\x89PNG fake", "image/png")},
        data={"noteId": note["id"]},
    ).json()
    content = f"Voor\n\n![schets.png|320]({r['url']})\n\n- [link]({r['url']})\n\nNa\n"
    saved = web.put(
        f"/api/notes/{note['id']}", json={"title": "Met plaatje", "content": content}
    ).json()
    assert saved["hash"]
    res = web.delete(f"/api/attachments/{r['id']}").json()
    assert res["note"]["content"] == "Voor\n\nNa\n"
    assert web.get(r["url"]).status_code == 404
    assert web.delete(f"/api/attachments/{r['id']}").status_code == 404


def test_caldav_settings_and_search_modes(web):
    folder = web.post("/api/folders", json={"name": "ZOKA"}).json()
    r = web.post("/settings/caldav", data={"excluded": [str(folder["id"])]}, follow_redirects=False)
    assert r.status_code == 303
    page = web.get("/settings").text
    assert f'value="{folder["id"]}" class' in page and "checked" in page
    web.post("/api/notes", json={"title": "Penningen"})
    assert 'class="result"' in web.get("/search?q=penningen&mode=text").text
    with web.ctx.db.session() as s:
        assert s.query(User).filter_by(username="cas").one().ui_state["search_mode"] == "text"
    # without semantic search (as in tests) "meaning" falls back to words
    assert 'class="result"' in web.get("/search?q=penningen&mode=meaning").text
    assert 'class="result"' not in web.get("/search?q=bestaatniet").text


def test_search_mode_choice_shows_with_semantic_search(web):
    class Semantic:
        def search(self, *args, **kwargs):
            return []

        def schedule(self, *args):
            pass

        def stop(self):
            pass

    web.ctx.semantic = Semantic()
    html = web.get("/search?q=x&mode=meaning").text
    assert html.count('name="mode"') == 3
    assert 'value="meaning" class="sr-only" checked' in html


def test_wide_view_is_remembered_per_note(web):
    note = web.post("/api/notes", json={"title": "Breed"}).json()
    assert 'data-wide="false"' in web.get(note["url"]).text
    assert web.post("/api/ui/wide", json={"noteId": note["id"], "wide": True}).json()["ok"]
    assert 'data-wide="true"' in web.get(note["url"]).text
