"""A small CalDAV server that exposes each user's tasks as one VTODO list.

It implements what Apple Reminders, Thunderbird and DAVx5 use: discovery through
`/.well-known/caldav`, PROPFIND, REPORT (calendar-query, calendar-multiget,
sync-collection), GET, PUT and DELETE with ETags. Every change goes through the
Vault, so a reminder ticked on a phone becomes `- [x]` in the Markdown note.
"""

import base64
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from icalendar import Calendar, Todo
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schrift import security
from schrift.models import AppPassword, Note, Task, TaskChange, User, utcnow
from schrift.services.vault import Vault, VaultError

router = APIRouter()

DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"
CS = "http://calendarserver.org/ns/"
ICAL = "http://apple.com/ns/ical/"
NS = {"d": DAV, "c": CAL, "cs": CS, "ical": ICAL}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

METHODS = ["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "GET", "HEAD", "PUT", "DELETE"]
LIST_NAME = "schrift"
COLOR = "#2443B0FF"
SYNC_PREFIX = "http://schrift.app/sync/"

# Properties we write ourselves; everything else a client sends (alarms, sort order,
# priority…) is kept and sent back unchanged. URL and DESCRIPTION are listed so the
# note links older versions of Schrift wrote are dropped instead of kept.
MANAGED = {
    "UID",
    "SUMMARY",
    "DUE",
    "DTSTART",
    "STATUS",
    "COMPLETED",
    "PERCENT-COMPLETE",
    "DESCRIPTION",
    "URL",
    "DTSTAMP",
    "CREATED",
    "LAST-MODIFIED",
    "SEQUENCE",
}


def q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


# --- authentication -----------------------------------------------------------------------


def authenticate(request: Request, session: Session) -> User | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        username, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return None
    user = session.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user is None:
        return None
    app_password = session.scalar(
        select(AppPassword).where(
            AppPassword.user_id == user.id,
            AppPassword.token_hash == security.token_hash(password.strip()),
        )
    )
    if app_password is None:
        return None
    now = utcnow()
    if app_password.last_used_at is None or now - app_password.last_used_at > timedelta(minutes=5):
        app_password.last_used_at = now
    return user


def unauthorized() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Schrift", charset="UTF-8"'},
    )


# --- iCalendar conversion ------------------------------------------------------------------


def user_tz(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone)
    except Exception:
        return ZoneInfo("UTC")


def task_context(vault: Vault, task: Task) -> str | None:
    note = task.note
    if note.is_inbox:
        return None
    top = vault.top_folder(note.folder)
    return top.name if top else None


def to_ical(vault: Vault, task: Task) -> bytes:
    user = vault.user
    tz = user_tz(user)
    cal = Calendar()
    cal.add("prodid", "-//Schrift//Schrift//NL")
    cal.add("version", "2.0")
    todo = Todo()
    extras = None
    if task.ical_extra:
        try:
            extras = Calendar.from_ical(task.ical_extra)
        except Exception:
            extras = None
    if extras is not None:
        for component in extras.walk("VTODO"):
            for key, value in component.property_items(recursive=False):
                if key not in MANAGED and key not in ("BEGIN", "END"):
                    todo.add(key, value, encode=False)
            for sub in component.subcomponents:
                todo.add_component(sub)
            break
    context = task_context(vault, task)
    todo.add("uid", task.caldav_uid)
    todo.add("summary", f"{task.title} [{context}]" if context else task.title)
    todo.add("dtstamp", task.updated_at.replace(tzinfo=UTC))
    todo.add("created", task.created_at.replace(tzinfo=UTC))
    todo.add("last-modified", task.updated_at.replace(tzinfo=UTC))
    if task.due:
        if len(task.due) > 10:
            local = datetime.fromisoformat(task.due).replace(tzinfo=tz)
            todo.add("due", local.astimezone(UTC))
        else:
            todo.add("due", date.fromisoformat(task.due))
    if task.done:
        todo.add("status", "COMPLETED")
        todo.add("percent-complete", 100)
        done_day = date.fromisoformat(task.done_on) if task.done_on else task.updated_at.date()
        todo.add("completed", datetime.combine(done_day, time(12), tz).astimezone(UTC))
    else:
        todo.add("status", "NEEDS-ACTION")
    cal.add_component(todo)
    return cal.to_ical()


def etag_of(data: bytes) -> str:
    return '"' + hashlib.sha1(data).hexdigest() + '"'


def parse_todo(data: bytes) -> Todo:
    cal = Calendar.from_ical(data)
    for component in cal.walk("VTODO"):
        return component
    raise ValueError("No VTODO in request")


def due_from_ical(todo: Todo, user: User) -> str | None:
    prop = todo.get("DUE")
    if prop is None:
        return None
    value = prop.dt
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=user_tz(user))
        local = value.astimezone(user_tz(user))
        return local.strftime("%Y-%m-%d %H:%M")
    return value.isoformat()


def strip_context(summary: str, context: str | None) -> str:
    """Remove the folder label we add, behind the title (or in front, as it used to be)."""
    summary = summary.strip()
    if context:
        label = f"[{context}]".lower()
        if summary.lower().endswith(label):
            return summary[: -len(label)].strip()
        if summary.lower().startswith(label):
            return summary[len(label) :].strip()
    return summary


def extras_of(todo: Todo) -> str | None:
    keep = Calendar()
    keep.add("prodid", "-//Schrift//Schrift//NL")
    keep.add("version", "2.0")
    copy = Todo()
    for key, value in todo.property_items(recursive=False):
        if key not in MANAGED and key not in ("BEGIN", "END"):
            copy.add(key, value, encode=False)
    for sub in todo.subcomponents:
        copy.add_component(sub)
    if not copy.property_items(recursive=False) and not copy.subcomponents:
        return None
    keep.add_component(copy)
    return keep.to_ical().decode()


# --- helpers for paths and XML --------------------------------------------------------------


def principal_href(user: User) -> str:
    return f"/caldav/{quote(user.username)}/"


def list_href(user: User) -> str:
    return f"/caldav/{quote(user.username)}/{LIST_NAME}/"


def item_href(user: User, task: Task) -> str:
    return list_href(user) + quote(task.caldav_name)


def sync_revision(session: Session, user: User) -> int:
    return session.scalar(select(func.max(TaskChange.id)).where(TaskChange.user_id == user.id)) or 0


def user_tasks(session: Session, user: User, excluded: set[int] = frozenset()) -> list[Task]:
    tasks = session.scalars(
        select(Task)
        .join(Note, Task.note_id == Note.id)
        .where(Task.user_id == user.id, Note.deleted_at.is_(None))
    )
    return [t for t in tasks if t.note.folder_id not in excluded]


def find_task(
    session: Session, user: User, name: str, excluded: set[int] = frozenset()
) -> Task | None:
    task = session.scalar(
        select(Task)
        .join(Note, Task.note_id == Note.id)
        .where(Task.user_id == user.id, Task.caldav_name == name, Note.deleted_at.is_(None))
    )
    return None if task is None or task.note.folder_id in excluded else task


def requested_props(body: bytes) -> list[str] | None:
    """Clark names of the requested properties; None means all (allprop or empty body)."""
    if not body.strip():
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    prop = root.find(q(DAV, "prop"))
    if prop is None:
        return None
    return [child.tag for child in prop]


def multistatus() -> ET.Element:
    return ET.Element(q(DAV, "multistatus"))


def add_response(ms: ET.Element, href: str, found: dict, missing: list[str]) -> None:
    response = ET.SubElement(ms, q(DAV, "response"))
    ET.SubElement(response, q(DAV, "href")).text = href
    if found:
        propstat = ET.SubElement(response, q(DAV, "propstat"))
        prop = ET.SubElement(propstat, q(DAV, "prop"))
        for tag, value in found.items():
            el = ET.SubElement(prop, tag)
            if callable(value):
                value(el)
            elif value is not None:
                el.text = str(value)
        ET.SubElement(propstat, q(DAV, "status")).text = "HTTP/1.1 200 OK"
    if missing:
        propstat = ET.SubElement(response, q(DAV, "propstat"))
        prop = ET.SubElement(propstat, q(DAV, "prop"))
        for tag in missing:
            ET.SubElement(prop, tag)
        ET.SubElement(propstat, q(DAV, "status")).text = "HTTP/1.1 404 Not Found"


def add_missing_response(ms: ET.Element, href: str) -> None:
    response = ET.SubElement(ms, q(DAV, "response"))
    ET.SubElement(response, q(DAV, "href")).text = href
    ET.SubElement(response, q(DAV, "status")).text = "HTTP/1.1 404 Not Found"


def xml_response(root: ET.Element, status: int = 207) -> Response:
    body = b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root)
    return Response(body, status_code=status, media_type="application/xml; charset=utf-8")


def href_child(href: str):
    def fill(el):
        ET.SubElement(el, q(DAV, "href")).text = href

    return fill


def element(*tags: str):
    def fill(el):
        for tag in tags:
            ET.SubElement(el, tag)

    return fill


def privileges(el):
    for name in (
        "read",
        "write",
        "write-properties",
        "write-content",
        "bind",
        "unbind",
        "read-acl",
    ):
        priv = ET.SubElement(el, q(DAV, "privilege"))
        ET.SubElement(priv, q(DAV, name))


def supported_reports(*reports: tuple[str, str]):
    def fill(el):
        for ns, name in reports:
            sr = ET.SubElement(el, q(DAV, "supported-report"))
            report = ET.SubElement(sr, q(DAV, "report"))
            ET.SubElement(report, q(ns, name))

    return fill


def select_props(available: dict, wanted: list[str] | None) -> tuple[dict, list[str]]:
    if wanted is None:
        return available, []
    found = {t: available[t] for t in wanted if t in available}
    missing = [t for t in wanted if t not in available]
    return found, missing


# --- property sets ---------------------------------------------------------------------------


def principal_props(user: User) -> dict:
    href = principal_href(user)
    return {
        q(DAV, "resourcetype"): element(q(DAV, "collection"), q(DAV, "principal")),
        q(DAV, "displayname"): user.username,
        q(DAV, "current-user-principal"): href_child(href),
        q(DAV, "principal-URL"): href_child(href),
        q(DAV, "principal-collection-set"): href_child("/caldav/"),
        q(DAV, "owner"): href_child(href),
        q(CAL, "calendar-home-set"): href_child(href),
        q(CAL, "calendar-user-address-set"): href_child(href),
        q(DAV, "current-user-privilege-set"): privileges,
        q(DAV, "supported-report-set"): supported_reports(),
    }


def list_props(session: Session, user: User) -> dict:
    revision = sync_revision(session, user)

    def components(el):
        ET.SubElement(el, q(CAL, "comp"), name="VTODO")

    return {
        q(DAV, "resourcetype"): element(q(DAV, "collection"), q(CAL, "calendar")),
        q(DAV, "displayname"): "Schrift",
        q(CAL, "calendar-description"): "Taken uit Schrift",
        q(CAL, "supported-calendar-component-set"): components,
        q(CS, "getctag"): str(revision),
        q(DAV, "sync-token"): f"{SYNC_PREFIX}{revision}",
        q(ICAL, "calendar-color"): COLOR,
        q(ICAL, "calendar-order"): "1",
        q(DAV, "current-user-principal"): href_child(principal_href(user)),
        q(DAV, "owner"): href_child(principal_href(user)),
        q(DAV, "current-user-privilege-set"): privileges,
        q(DAV, "supported-report-set"): supported_reports(
            (CAL, "calendar-query"), (CAL, "calendar-multiget"), (DAV, "sync-collection")
        ),
        q(CAL, "supported-calendar-data"): lambda el: ET.SubElement(
            el, q(CAL, "calendar-data"), {"content-type": "text/calendar", "version": "2.0"}
        ),
    }


def item_props(data: bytes, include_data: bool = False) -> dict:
    props = {
        q(DAV, "getetag"): etag_of(data),
        q(DAV, "getcontenttype"): "text/calendar; charset=utf-8; component=VTODO",
        q(DAV, "resourcetype"): None,
        q(DAV, "getcontentlength"): str(len(data)),
    }
    if include_data:
        props[q(CAL, "calendar-data")] = data.decode()
    return props


# --- request handling -------------------------------------------------------------------------


class CalDav:
    def __init__(self, request: Request, session: Session, user: User):
        ctx = request.app.state.ctx
        self.request = request
        self.session = session
        self.user = user
        self.vault = ctx.vault(session, user)
        self.excluded = self.vault.caldav_excluded_ids()

    def ical(self, task: Task) -> bytes:
        return to_ical(self.vault, task)

    # PROPFIND -------------------------------------------------------------
    async def propfind(self, kind: str, name: str | None) -> Response:
        body = await self.request.body()
        wanted = requested_props(body)
        depth = self.request.headers.get("depth", "1")
        ms = multistatus()
        if kind == "root":
            found, missing = select_props(
                {
                    q(DAV, "resourcetype"): element(q(DAV, "collection")),
                    q(DAV, "current-user-principal"): href_child(principal_href(self.user)),
                    q(DAV, "principal-URL"): href_child(principal_href(self.user)),
                    q(CAL, "calendar-home-set"): href_child(principal_href(self.user)),
                },
                wanted,
            )
            add_response(ms, self.request.url.path, found, missing)
        elif kind == "principal":
            found, missing = select_props(principal_props(self.user), wanted)
            add_response(ms, principal_href(self.user), found, missing)
            if depth != "0":
                found, missing = select_props(list_props(self.session, self.user), wanted)
                add_response(ms, list_href(self.user), found, missing)
        elif kind == "list":
            found, missing = select_props(list_props(self.session, self.user), wanted)
            add_response(ms, list_href(self.user), found, missing)
            if depth != "0":
                for task in user_tasks(self.session, self.user, self.excluded):
                    data = self.ical(task)
                    found, missing = select_props(item_props(data), wanted)
                    add_response(ms, item_href(self.user, task), found, missing)
        else:
            task = find_task(self.session, self.user, name, self.excluded)
            if task is None:
                return Response(status_code=404)
            data = self.ical(task)
            found, missing = select_props(item_props(data, include_data=True), wanted)
            add_response(ms, item_href(self.user, task), found, missing)
        return xml_response(ms)

    # REPORT ---------------------------------------------------------------
    async def report(self) -> Response:
        body = await self.request.body()
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return Response(status_code=400)
        wanted = requested_props(body)
        ms = multistatus()

        def add_task(task: Task):
            data = self.ical(task)
            found, missing = select_props(item_props(data, include_data=True), wanted)
            add_response(ms, item_href(self.user, task), found, missing)

        if root.tag == q(CAL, "calendar-multiget"):
            for href_el in root.findall(q(DAV, "href")):
                name = unquote((href_el.text or "").rstrip("/").rsplit("/", 1)[-1])
                task = find_task(self.session, self.user, name, self.excluded)
                if task is None:
                    add_missing_response(ms, href_el.text or "")
                else:
                    add_task(task)
            return xml_response(ms)

        if root.tag == q(CAL, "calendar-query"):
            only_open = any(
                pf.get("name", "").upper() == "COMPLETED"
                and pf.find(q(CAL, "is-not-defined")) is not None
                for pf in root.iter(q(CAL, "prop-filter"))
            )
            for task in user_tasks(self.session, self.user, self.excluded):
                if only_open and task.done:
                    continue
                add_task(task)
            return xml_response(ms)

        if root.tag == q(DAV, "sync-collection"):
            token = (root.findtext(q(DAV, "sync-token")) or "").strip()
            current = sync_revision(self.session, self.user)
            if not token:
                for task in user_tasks(self.session, self.user, self.excluded):
                    add_task(task)
            else:
                match = re.fullmatch(re.escape(SYNC_PREFIX) + r"(\d+)", token)
                if not match or int(match.group(1)) > current:
                    error = ET.Element(q(DAV, "error"))
                    ET.SubElement(error, q(DAV, "valid-sync-token"))
                    return xml_response(error, status=403)
                since = int(match.group(1))
                names = list(
                    dict.fromkeys(
                        self.session.scalars(
                            select(TaskChange.caldav_name)
                            .where(TaskChange.user_id == self.user.id, TaskChange.id > since)
                            .order_by(TaskChange.id)
                        )
                    )
                )
                for name in names:
                    task = find_task(self.session, self.user, name, self.excluded)
                    if task is None:
                        add_missing_response(ms, list_href(self.user) + quote(name))
                    else:
                        add_task(task)
            ET.SubElement(ms, q(DAV, "sync-token")).text = f"{SYNC_PREFIX}{current}"
            return xml_response(ms)

        return Response(status_code=501)

    # GET / PUT / DELETE ----------------------------------------------------
    def get(self, name: str, head: bool = False) -> Response:
        task = find_task(self.session, self.user, name, self.excluded)
        if task is None:
            return Response(status_code=404)
        data = self.ical(task)
        return Response(
            b"" if head else data,
            media_type="text/calendar; charset=utf-8",
            headers={"ETag": etag_of(data)},
        )

    async def put(self, name: str) -> Response:
        body = await self.request.body()
        try:
            todo = parse_todo(body)
        except Exception:
            return Response("Expected a VTODO", status_code=415)
        if_match = self.request.headers.get("if-match")
        if_none_match = self.request.headers.get("if-none-match")
        task = find_task(self.session, self.user, name, self.excluded)

        if task is not None:
            current = etag_of(self.ical(task))
            if if_none_match == "*":
                return Response(status_code=412)
            if if_match and if_match not in ("*", current):
                return Response(status_code=412)
            context = task_context(self.vault, task)
            title = strip_context(str(todo.get("SUMMARY", "")), context) or task.title
            done = str(todo.get("STATUS", "")).upper() == "COMPLETED" or "COMPLETED" in todo
            task.ical_extra = extras_of(todo)
            try:
                self.vault.update_task(
                    task,
                    title=title,
                    due=due_from_ical(todo, self.user),
                    done=done,
                    source="caldav",
                )
            except VaultError as e:
                return Response(str(e), status_code=409)
            status = 204
        else:
            if if_match:
                return Response(status_code=412)
            if find_task(self.session, self.user, name) is not None:
                return Response(status_code=403)  # a task in a folder that is not synced
            title = strip_context(str(todo.get("SUMMARY", "")), None) or "Naamloos"
            task = self.vault.create_task(
                title, due=due_from_ical(todo, self.user), source="caldav"
            )
            task.caldav_name = name
            task.caldav_uid = str(todo.get("UID") or name.removesuffix(".ics"))
            task.ical_extra = extras_of(todo)
            self.session.flush()
            if str(todo.get("STATUS", "")).upper() == "COMPLETED":
                self.vault.update_task(task, done=True, source="caldav")
            self.vault._log_change(task.caldav_name)
            status = 201
        self.session.flush()
        return Response(status_code=status, headers={"ETag": etag_of(self.ical(task))})

    def delete(self, name: str) -> Response:
        task = find_task(self.session, self.user, name, self.excluded)
        if task is None:
            return Response(status_code=404)
        if_match = self.request.headers.get("if-match")
        if if_match and if_match not in ("*", etag_of(self.ical(task))):
            return Response(status_code=412)
        try:
            self.vault.delete_task(task, source="caldav")
        except VaultError:
            self.session.delete(task)
            self.vault._log_change(task.caldav_name, deleted=True)
        return Response(status_code=204)

    async def proppatch(self, href: str) -> Response:
        """Accept (and ignore) client-side display settings like colour and order."""
        body = await self.request.body()
        ms = multistatus()
        try:
            root = ET.fromstring(body)
            tags = [p.tag for prop in root.iter(q(DAV, "prop")) for p in prop]
        except ET.ParseError:
            tags = []
        add_response(ms, href, {t: None for t in tags}, [])
        return xml_response(ms)


def options_response() -> Response:
    return Response(
        status_code=200,
        headers={
            "DAV": "1, 3, calendar-access",
            "Allow": ", ".join(METHODS),
        },
    )


async def dispatch(request: Request, kind: str, username: str | None, name: str | None):
    if request.method == "OPTIONS":
        return options_response()
    ctx = request.app.state.ctx
    with ctx.db.session() as session:
        user = authenticate(request, session)
        if user is None:
            return unauthorized()
        if username is not None and unquote(username).lower() != user.username.lower():
            return Response(status_code=403)
        dav = CalDav(request, session, user)
        method = request.method
        if method == "PROPFIND":
            return await dav.propfind(kind, name)
        if method == "PROPPATCH" and kind in ("list", "principal"):
            return await dav.proppatch(request.url.path)
        if method == "REPORT" and kind == "list":
            return await dav.report()
        if kind == "item":
            if method in ("GET", "HEAD"):
                return dav.get(name, head=method == "HEAD")
            if method == "PUT":
                return await dav.put(name)
            if method == "DELETE":
                return dav.delete(name)
        if method in ("GET", "HEAD") and kind != "item":
            return Response("Schrift CalDAV", media_type="text/plain")
        if method in ("MKCALENDAR", "MKCOL"):
            return Response(status_code=403)
        return Response(status_code=405, headers={"Allow": ", ".join(METHODS)})


ALL = METHODS + ["MKCALENDAR", "MKCOL", "MOVE", "COPY", "LOCK", "UNLOCK", "POST"]


@router.api_route("/.well-known/caldav", methods=ALL, include_in_schema=False)
async def well_known(request: Request):
    return Response(status_code=301, headers={"Location": "/caldav/"})


@router.api_route("/caldav", methods=ALL, include_in_schema=False)
@router.api_route("/caldav/", methods=ALL, include_in_schema=False)
async def root(request: Request):
    return await dispatch(request, "root", None, None)


@router.api_route("/caldav/{username}", methods=ALL, include_in_schema=False)
@router.api_route("/caldav/{username}/", methods=ALL, include_in_schema=False)
async def principal(request: Request, username: str):
    return await dispatch(request, "principal", username, None)


@router.api_route(f"/caldav/{{username}}/{LIST_NAME}", methods=ALL, include_in_schema=False)
@router.api_route(f"/caldav/{{username}}/{LIST_NAME}/", methods=ALL, include_in_schema=False)
async def task_list(request: Request, username: str):
    return await dispatch(request, "list", username, None)


@router.api_route(
    f"/caldav/{{username}}/{LIST_NAME}/{{name}}", methods=ALL, include_in_schema=False
)
async def task_item(request: Request, username: str, name: str):
    return await dispatch(request, "item", username, unquote(name))
