import base64
import re
import xml.etree.ElementTree as ET

import pytest

from schrift import security
from schrift.models import AppPassword, User

D = "{DAV:}"
C = "{urn:ietf:params:xml:ns:caldav}"


@pytest.fixture
def dav(client):
    ctx = client.app.state.ctx
    with ctx.db.session() as s:
        user = User(username="cas", password_hash=security.hash_password("x" * 12))
        s.add(user)
        s.flush()
        token = security.token_hash("app-pw")
        s.add(AppPassword(user_id=user.id, name="iPhone", token_hash=token))
        zoka = ctx.vault(s, user).create_folder("ZOKA")
        ctx.vault(s, user).create_note(
            "Overleg",
            "# Overleg\n\n- [ ] Offerte sturen 📅 2026-10-20 14:00 🆔 aaaaa\n- [ ] Bellen 🆔 bbbbb\n",
            folder=zoka,
        )
    auth = "Basic " + base64.b64encode(b"cas:app-pw").decode()

    def request(method, path, body=None, **headers):
        return client.request(
            method, path, content=body, headers={"Authorization": auth, **headers}
        )

    request.ctx = ctx
    return request


def note_text(dav, title="Overleg"):
    ctx = dav.ctx
    with ctx.db.session() as s:
        user = s.query(User).filter_by(username="cas").one()
        vault = ctx.vault(s, user)
        return vault.body(vault.find_note(title))


PROPFIND_PRINCIPAL = b"""<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>"""

PROPFIND_HOME = b"""<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
<d:prop><d:resourcetype/><d:displayname/><c:supported-calendar-component-set/><cs:getctag/><d:sync-token/><c:calendar-home-set/></d:prop>
</d:propfind>"""

MULTIGET = """<?xml version="1.0"?>
<c:calendar-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:prop><d:getetag/><c:calendar-data/></d:prop>{hrefs}</c:calendar-multiget>"""

SYNC = """<?xml version="1.0"?>
<d:sync-collection xmlns:d="DAV:"><d:sync-token>{token}</d:sync-token><d:sync-level>1</d:sync-level>
<d:prop><d:getetag/></d:prop></d:sync-collection>"""


def vtodo(uid, summary, extra=""):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iOS 18//EN\r\n"
        f"BEGIN:VTODO\r\nUID:{uid}\r\nDTSTAMP:20261014T100000Z\r\nSUMMARY:{summary}\r\n{extra}"
        "END:VTODO\r\nEND:VCALENDAR\r\n"
    ).encode()


def test_discovery(dav, client):
    r = client.request("PROPFIND", "/.well-known/caldav", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/caldav/"
    assert client.request("PROPFIND", "/caldav/").status_code == 401
    r = dav("PROPFIND", "/", PROPFIND_PRINCIPAL, Depth="0")
    assert r.status_code == 207 and "/caldav/cas/" in r.text
    r = dav("PROPFIND", "/caldav/cas/", PROPFIND_HOME, Depth="1")
    root = ET.fromstring(r.content)
    hrefs = [h.text for h in root.iter(D + "href")]
    assert "/caldav/cas/schrift/" in hrefs
    assert root.find(f".//{C}comp").get("name") == "VTODO"


def test_wrong_password_and_other_user(dav, client):
    bad = "Basic " + base64.b64encode(b"cas:nope").decode()
    assert client.request("PROPFIND", "/caldav/", headers={"Authorization": bad}).status_code == 401
    assert dav("PROPFIND", "/caldav/someone/schrift/").status_code == 403


def test_list_and_multiget(dav):
    r = dav("PROPFIND", "/caldav/cas/schrift/", b"", Depth="1")
    hrefs = re.findall(r"<d:href>([^<]+\.ics)</d:href>", r.text)
    assert sorted(hrefs) == ["/caldav/cas/schrift/aaaaa.ics", "/caldav/cas/schrift/bbbbb.ics"]
    body = MULTIGET.format(hrefs="".join(f"<d:href>{h}</d:href>" for h in hrefs)).encode()
    r = dav("REPORT", "/caldav/cas/schrift/", body, Depth="1")
    assert r.status_code == 207
    assert "SUMMARY:[ZOKA] Offerte sturen" in r.text
    assert "DUE:20261020T120000Z" in r.text  # 14:00 Amsterdam (CEST) in UTC
    assert "STATUS:NEEDS-ACTION" in r.text
    assert "URL:" not in r.text and "DESCRIPTION:" not in r.text


def test_complete_from_phone_updates_markdown(dav):
    r = dav("GET", "/caldav/cas/schrift/aaaaa.ics")
    etag = r.headers["etag"]
    body = r.content.replace(
        b"STATUS:NEEDS-ACTION", b"STATUS:COMPLETED\r\nCOMPLETED:20261014T090000Z"
    )
    r = dav("PUT", "/caldav/cas/schrift/aaaaa.ics", body, **{"If-Match": etag})
    assert r.status_code == 204
    assert "- [x] Offerte sturen 📅 2026-10-20 14:00 ✅" in note_text(dav)
    # the old etag no longer matches
    r = dav("PUT", "/caldav/cas/schrift/aaaaa.ics", body, **{"If-Match": etag})
    assert r.status_code == 412


def test_edit_title_and_due_keeps_prefix_out(dav):
    body = vtodo("aaaaa@schrift", "[ZOKA] Offerte versturen", "DUE;VALUE=DATE:20261101\r\n")
    assert dav("PUT", "/caldav/cas/schrift/aaaaa.ics", body).status_code == 204
    assert "- [ ] Offerte versturen 📅 2026-11-01 🆔 aaaaa" in note_text(dav)


def test_new_reminder_goes_to_inbox_and_keeps_alarm(dav):
    alarm = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:x\r\nEND:VALARM\r\n"
    body = vtodo("E1A2-UUID", "Brood halen", "DUE:20261015T070000Z\r\n" + alarm)
    r = dav("PUT", "/caldav/cas/schrift/E1A2-UUID.ics", body, **{"If-None-Match": "*"})
    assert r.status_code == 201
    inbox = note_text(dav, "Inbox")
    assert "- [ ] Brood halen 📅 2026-10-15 09:00 🆔" in inbox
    r = dav("GET", "/caldav/cas/schrift/E1A2-UUID.ics")
    assert "UID:E1A2-UUID" in r.text and "BEGIN:VALARM" in r.text and "TRIGGER:-PT15M" in r.text
    assert "SUMMARY:Brood halen" in r.text


def test_delete_from_phone_removes_line(dav):
    assert dav("DELETE", "/caldav/cas/schrift/bbbbb.ics").status_code == 204
    assert "Bellen" not in note_text(dav)
    assert dav("GET", "/caldav/cas/schrift/bbbbb.ics").status_code == 404


def test_sync_collection(dav):
    r = dav("REPORT", "/caldav/cas/schrift/", SYNC.format(token="").encode())
    token = re.search(r"<d:sync-token>([^<]+)</d:sync-token>", r.text).group(1)
    assert r.text.count("<d:response>") == 2
    # nothing changed
    r = dav("REPORT", "/caldav/cas/schrift/", SYNC.format(token=token).encode())
    assert "<d:response>" not in r.text
    # change in Schrift, then delete via CalDAV
    ctx = dav.ctx
    with ctx.db.session() as s:
        user = s.query(User).filter_by(username="cas").one()
        vault = ctx.vault(s, user)
        vault.update_task(vault.get_task("aaaaa"), done=True)
    dav("DELETE", "/caldav/cas/schrift/bbbbb.ics")
    r = dav("REPORT", "/caldav/cas/schrift/", SYNC.format(token=token).encode())
    assert "aaaaa.ics" in r.text and "bbbbb.ics" in r.text and "404 Not Found" in r.text
    bad = dav("REPORT", "/caldav/cas/schrift/", SYNC.format(token="http://x/9999").encode())
    assert bad.status_code == 403


def test_calendar_query_open_only(dav):
    dav(
        "PUT",
        "/caldav/cas/schrift/bbbbb.ics",
        vtodo("bbbbb@schrift", "Bellen", "STATUS:COMPLETED\r\n"),
    )
    query = b"""<?xml version="1.0"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/></d:prop>
<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO">
<c:prop-filter name="COMPLETED"><c:is-not-defined/></c:prop-filter></c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""
    r = dav("REPORT", "/caldav/cas/schrift/", query, Depth="1")
    assert "aaaaa.ics" in r.text and "bbbbb.ics" not in r.text


def test_options_and_proppatch(dav):
    r = dav("OPTIONS", "/caldav/cas/schrift/")
    assert "calendar-access" in r.headers["dav"]
    body = b"""<?xml version="1.0"?><d:propertyupdate xmlns:d="DAV:" xmlns:a="http://apple.com/ns/ical/">
<d:set><d:prop><a:calendar-order>2</a:calendar-order></d:prop></d:set></d:propertyupdate>"""
    assert dav("PROPPATCH", "/caldav/cas/schrift/", body).status_code == 207


def test_old_note_links_from_the_phone_are_dropped(dav):
    old = "URL:http://localhost/n/x\r\nDESCRIPTION:http://localhost/n/x\r\n"
    assert (
        dav(
            "PUT",
            "/caldav/cas/schrift/aaaaa.ics",
            vtodo("aaaaa@schrift", "[ZOKA] Offerte sturen", old),
        ).status_code
        == 204
    )
    r = dav("GET", "/caldav/cas/schrift/aaaaa.ics")
    assert "URL:" not in r.text and "DESCRIPTION:" not in r.text
