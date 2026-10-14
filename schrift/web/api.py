"""JSON endpoints used by the editor and the sidebar."""

from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schrift import markdown as md
from schrift.i18n import translate
from schrift.models import Note, User
from schrift.services import attachments as att
from schrift.services.search import hybrid
from schrift.services.vault import ConflictError, Vault, VaultError, content_hash
from schrift.web.common import SessionDep, UserDep, get_ctx, vault_for

router = APIRouter(prefix="/api")


def error(message: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def note_payload(vault: Vault, note: Note) -> dict:
    body = vault.body(note)
    title, content = md.split_title(body)
    return {
        "id": note.id,
        "title": title if title is not None else note.title,
        "content": content,
        "hasTitle": title is not None,
        "tags": note.tags or [],
        "hash": note.content_hash,
        "path": note.path,
        "updatedAt": note.updated_at.isoformat() + "Z",
        "updatedBy": note.updated_by,
        "isInbox": note.is_inbox,
    }


# --- notes ------------------------------------------------------------


@router.get("/notes/{note_id}")
def get_note(note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    try:
        return note_payload(vault, vault.get_note(note_id))
    except VaultError as e:
        return error(str(e), 404)


@router.get("/notes/{note_id}/meta")
def note_meta(note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    try:
        note = vault.get_note(note_id)
    except VaultError as e:
        return error(str(e), 404)
    return {"hash": note.content_hash, "updatedBy": note.updated_by, "title": note.title}


class SaveNote(BaseModel):
    title: str
    content: str
    baseHash: str | None = None
    force: bool = False


@router.put("/notes/{note_id}")
def save_note(
    note_id: str,
    data: SaveNote,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        note = vault.get_note(note_id)
        content = md.normalize_editor_markdown(data.content)
        title = data.title.strip() or note.title
        if note.is_inbox:
            title = note.title
        body = md.join_title(title, content)
        old_path = note.path
        vault.save(note, body, source="web", base_hash=None if data.force else data.baseHash)
    except ConflictError as e:
        title, content = md.split_title(e.body)
        return error(
            str(e),
            409,
            content=content,
            title=title or e.note.title,
            hash=content_hash(e.body),
            updatedBy=e.note.updated_by,
        )
    except VaultError as e:
        return error(str(e))
    return {
        "hash": note.content_hash,
        "title": note.title,
        "path": note.path,
        "renamed": old_path != note.path,
    }


class TagsIn(BaseModel):
    tags: list[str]


@router.put("/notes/{note_id}/tags")
def set_tags(
    note_id: str,
    data: TagsIn,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        note = vault.set_tags(vault.get_note(note_id), data.tags)
    except VaultError as e:
        return error(str(e))
    return {"tags": note.tags, "hash": note.content_hash}


class NewNote(BaseModel):
    title: str = ""
    folderId: int | None = None


@router.post("/notes")
def create_note(
    data: NewNote, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    try:
        folder = vault.get_folder(data.folderId)
        note = vault.create_note(data.title.strip() or "Naamloos", folder=folder)
    except VaultError as e:
        return error(str(e))
    return {"id": note.id, "url": f"/n/{note.id}"}


@router.delete("/notes/{note_id}")
def delete_note(
    note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    try:
        vault.delete_note(vault.get_note(note_id))
    except VaultError as e:
        return error(str(e))
    return {"ok": True}


@router.post("/notes/{note_id}/restore")
def restore_note(
    note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    try:
        note = vault.restore_note(vault.get_note(note_id, include_deleted=True))
    except VaultError as e:
        return error(str(e))
    return {"ok": True, "url": f"/n/{note.id}"}


class MoveIn(BaseModel):
    folderId: int | None = None
    position: int | None = None


@router.post("/notes/{note_id}/move")
def move_note(
    note_id: str,
    data: MoveIn,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        note = vault.move_note(
            vault.get_note(note_id), vault.get_folder(data.folderId), data.position
        )
    except VaultError as e:
        return error(str(e))
    return {"ok": True, "path": note.path}


@router.get("/notes/{note_id}/versions")
def versions(note_id: str, request: Request, session: Session = SessionDep, user: User = UserDep):
    vault = vault_for(request, session, user)
    try:
        note = vault.get_note(note_id)
    except VaultError as e:
        return error(str(e), 404)
    return [
        {
            "id": v.id,
            "source": v.source,
            "createdAt": v.created_at.isoformat() + "Z",
            "updatedAt": v.updated_at.isoformat() + "Z",
            "title": v.title,
        }
        for v in vault.versions(note)
    ]


@router.get("/notes/{note_id}/versions/{version_id}")
def version(
    note_id: str,
    version_id: int,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        note = vault.get_note(note_id)
    except VaultError as e:
        return error(str(e), 404)
    for v in vault.versions(note):
        if v.id == version_id:
            return {"id": v.id, "content": v.content, "source": v.source}
    return error("Versie niet gevonden.", 404)


@router.post("/notes/{note_id}/versions/{version_id}/restore")
def restore_version(
    note_id: str,
    version_id: int,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        note = vault.restore_version(vault.get_note(note_id), version_id)
    except VaultError as e:
        return error(str(e))
    return {"ok": True, "hash": note.content_hash}


@router.get("/titles")
def titles(
    q: str = "", request: Request = None, session: Session = SessionDep, user: User = UserDep
):
    """Note titles for [[ autocomplete."""
    query = select(Note.id, Note.title, Note.path).where(
        Note.user_id == user.id, Note.deleted_at.is_(None), ~Note.is_inbox
    )
    if q.strip():
        query = query.where(func.lower(Note.title).contains(q.strip().lower()))
    rows = session.execute(query.order_by(Note.updated_at.desc()).limit(8)).all()
    return [{"id": r.id, "title": r.title, "path": r.path} for r in rows]


@router.get("/search")
def search(q: str, request: Request, session: Session = SessionDep, user: User = UserDep):
    results = hybrid(session, user.id, q, semantic=get_ctx(request).semantic, limit=15)
    return [
        {
            "id": r.note.id,
            "title": r.note.title,
            "path": r.note.path,
            "snippet": r.snippet,
            "via": r.via,
        }
        for r in results
    ]


# --- folders ------------------------------------------------------------


class FolderIn(BaseModel):
    name: str
    parentId: int | None = None


@router.post("/folders")
def create_folder(
    data: FolderIn, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    try:
        folder = vault.create_folder(data.name, data.parentId)
    except VaultError as e:
        return error(str(e))
    expanded = set((user.ui_state or {}).get("expanded", []))
    if data.parentId:
        expanded.add(data.parentId)
    user.ui_state = {**(user.ui_state or {}), "expanded": sorted(expanded)}
    return {"id": folder.id, "name": folder.name}


class RenameIn(BaseModel):
    name: str


@router.patch("/folders/{folder_id}")
def rename_folder(
    folder_id: int,
    data: RenameIn,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        folder = vault.rename_folder(vault.get_folder(folder_id), data.name)
    except VaultError as e:
        return error(str(e))
    return {"id": folder.id, "name": folder.name}


@router.delete("/folders/{folder_id}")
def delete_folder(
    folder_id: int, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    try:
        vault.delete_folder(vault.get_folder(folder_id))
    except VaultError as e:
        return error(str(e))
    return {"ok": True}


class FolderMoveIn(BaseModel):
    parentId: int | None = None
    position: int | None = None


@router.post("/folders/{folder_id}/move")
def move_folder(
    folder_id: int,
    data: FolderMoveIn,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        vault.move_folder(vault.get_folder(folder_id), data.parentId, data.position)
    except VaultError as e:
        return error(str(e))
    return {"ok": True}


class ExpandIn(BaseModel):
    folderId: int
    open: bool


@router.post("/ui/expanded")
def set_expanded(data: ExpandIn, session: Session = SessionDep, user: User = UserDep):
    expanded = set((user.ui_state or {}).get("expanded", []))
    (expanded.add if data.open else expanded.discard)(data.folderId)
    user.ui_state = {**(user.ui_state or {}), "expanded": sorted(expanded)}
    return {"ok": True}


# --- tasks ------------------------------------------------------------


class TaskUpdate(BaseModel):
    title: str | None = None
    due: str | None = None
    clearDue: bool = False
    done: bool | None = None


@router.patch("/tasks/{short_id}")
def update_task(
    short_id: str,
    data: TaskUpdate,
    request: Request,
    session: Session = SessionDep,
    user: User = UserDep,
):
    vault = vault_for(request, session, user)
    try:
        task = vault.get_task(short_id)
        due = None if data.clearDue else (data.due if data.due is not None else ...)
        vault.update_task(task, title=data.title, due=due, done=data.done)
    except VaultError as e:
        return error(str(e))
    return {"shortId": task.short_id, "done": task.done, "due": task.due, "title": task.title}


class NewTask(BaseModel):
    title: str
    due: str | None = None
    noteId: str | None = None


@router.post("/tasks")
def create_task(
    data: NewTask, request: Request, session: Session = SessionDep, user: User = UserDep
):
    vault = vault_for(request, session, user)
    if not data.title.strip():
        return error(translate(user.language, "Typ eerst een taak."))
    try:
        note = vault.get_note(data.noteId) if data.noteId else None
        task = vault.create_task(data.title, due=data.due or None, note=note)
    except VaultError as e:
        return error(str(e))
    return {"shortId": task.short_id, "noteId": task.note_id}


# --- attachments ------------------------------------------------------------


@router.post("/attachments")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    noteId: str | None = Form(None),
    session: Session = SessionDep,
    user: User = UserDep,
):
    ctx = get_ctx(request)
    vault = vault_for(request, session, user)
    max_bytes = ctx.settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    try:
        if noteId:
            vault.get_note(noteId)
        attachment = att.save_attachment(vault, file.filename or "bestand", data, noteId, max_bytes)
    except VaultError as e:
        return error(str(e), 413 if "groot" in str(e) else 400)
    return {
        "id": attachment.id,
        "url": att.attachment_url(attachment),
        "filename": attachment.filename,
        "isImage": att.is_image(attachment),
        "markdown": att.markdown_for(attachment),
    }
