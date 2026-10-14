"""The single write path for notes, folders and tasks of one user.

Every change, whether it comes from the web UI, MCP or CalDAV, goes through `Vault`.
It writes the Markdown file, stores a version, and re-indexes tags, links, tasks and
the full-text index, so the three entry points can never drift apart.
"""

import hashlib
import os
import shutil
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from schrift import markdown as md
from schrift.models import (
    Attachment,
    Folder,
    Link,
    Note,
    NoteVersion,
    Task,
    TaskChange,
    User,
    utcnow,
)

INBOX_TITLE = "Inbox"
VERSION_COALESCE = timedelta(minutes=10)

_user_locks: dict[int, threading.RLock] = defaultdict(threading.RLock)


class VaultError(Exception):
    """A user-facing error (bad name, missing note, conflict)."""


class ConflictError(VaultError):
    def __init__(self, note: Note, body: str):
        super().__init__("De notitie is intussen elders gewijzigd.")
        self.note = note
        self.body = body


def content_hash(text_: str) -> str:
    return hashlib.sha256(text_.encode()).hexdigest()


@dataclass
class TreeFolder:
    folder: Folder
    path: str
    folders: list["TreeFolder"] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


class Vault:
    def __init__(self, session: Session, user: User, data_dir: Path, on_change=None):
        self.session = session
        self.user = user
        self.root = Path(data_dir) / "users" / str(user.id)
        self.notes_dir = self.root / "notes"
        self.attachments_dir = self.root / "attachments"
        self.trash_dir = self.root / "trash"
        for d in (self.notes_dir, self.attachments_dir, self.trash_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.lock = _user_locks[user.id]
        self.on_change = on_change  # callback(note_id) after a note's content changed

    # --- folders --------------------------------------------------------------------

    def folders(self) -> list[Folder]:
        return list(
            self.session.scalars(
                select(Folder)
                .where(Folder.user_id == self.user.id)
                .order_by(Folder.position, Folder.name)
            )
        )

    def folder_path(self, folder: Folder | None) -> str:
        parts = []
        while folder is not None:
            parts.append(folder.name)
            folder = folder.parent
        return "/".join(reversed(parts))

    def get_folder(self, folder_id: int | None) -> Folder | None:
        if folder_id is None:
            return None
        folder = self.session.get(Folder, folder_id)
        if folder is None or folder.user_id != self.user.id:
            raise VaultError("Map niet gevonden.")
        return folder

    def top_folder(self, folder: Folder | None) -> Folder | None:
        while folder is not None and folder.parent is not None:
            folder = folder.parent
        return folder

    def folder_by_path(self, path: str, create: bool = False) -> Folder | None:
        parent = None
        for name in [p.strip() for p in path.strip("/").split("/") if p.strip()]:
            cond = Folder.parent_id == parent.id if parent else Folder.parent_id.is_(None)
            found = self.session.scalar(
                select(Folder).where(
                    Folder.user_id == self.user.id, cond, func.lower(Folder.name) == name.lower()
                )
            )
            if found is None:
                if not create:
                    raise VaultError(f"Map '{path}' bestaat niet.")
                found = self.create_folder(name, parent.id if parent else None)
            parent = found
        return parent

    def _check_name(self, name: str) -> str:
        clean = md.safe_filename(name)
        if not name.strip() or clean in (".", "..") or clean.lower() == "inbox.md":
            raise VaultError("Ongeldige naam.")
        return clean

    def create_folder(self, name: str, parent_id: int | None = None) -> Folder:
        with self.lock:
            parent = self.get_folder(parent_id)
            name = self._check_name(name)
            siblings = self._sibling_folders(parent_id)
            if any(f.name.lower() == name.lower() for f in siblings):
                raise VaultError(f"Er bestaat al een map '{name}'.")
            folder = Folder(
                user_id=self.user.id,
                parent_id=parent_id,
                parent=parent,
                name=name,
                position=max((f.position for f in siblings), default=-1) + 1,
            )
            self.session.add(folder)
            self.session.flush()
            (self.notes_dir / self.folder_path(folder)).mkdir(parents=True, exist_ok=True)
            return folder

    def _sibling_folders(self, parent_id: int | None) -> list[Folder]:
        cond = Folder.parent_id == parent_id if parent_id else Folder.parent_id.is_(None)
        return list(
            self.session.scalars(
                select(Folder).where(Folder.user_id == self.user.id, cond).order_by(Folder.position)
            )
        )

    def _descendant_ids(self, folder: Folder) -> list[int]:
        ids = [folder.id]
        children = {f.id: f for f in self.folders()}
        changed = True
        while changed:
            changed = False
            for f in children.values():
                if f.parent_id in ids and f.id not in ids:
                    ids.append(f.id)
                    changed = True
        return ids

    def _relocate_folder(self, folder: Folder, apply) -> None:
        """Move a folder on disk after `apply()` changes its name or parent."""
        old_rel = self.folder_path(folder)
        apply()
        self.session.flush()
        new_rel = self.folder_path(folder)
        if old_rel == new_rel:
            return
        old_dir, new_dir = self.notes_dir / old_rel, self.notes_dir / new_rel
        if new_dir.exists() and old_rel.lower() != new_rel.lower():
            raise VaultError("Er bestaat al een map met die naam.")
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if old_dir.exists():
            os.replace(old_dir, new_dir)
        else:
            new_dir.mkdir(parents=True, exist_ok=True)
        ids = self._descendant_ids(folder)
        for note in self.session.scalars(
            select(Note).where(Note.folder_id.in_(ids), Note.deleted_at.is_(None))
        ):
            note.path = new_rel + note.path[len(old_rel) :]
            self._touch_tasks(note)

    def rename_folder(self, folder: Folder, name: str) -> Folder:
        with self.lock:
            name = self._check_name(name)
            siblings = self._sibling_folders(folder.parent_id)
            if any(f.id != folder.id and f.name.lower() == name.lower() for f in siblings):
                raise VaultError(f"Er bestaat al een map '{name}'.")
            self._relocate_folder(folder, lambda: setattr(folder, "name", name))
            return folder

    def move_folder(self, folder: Folder, parent_id: int | None, position: int | None = None):
        with self.lock:
            parent = self.get_folder(parent_id)
            if parent is not None and parent.id in self._descendant_ids(folder):
                raise VaultError("Een map kan niet in zichzelf.")
            siblings = [f for f in self._sibling_folders(parent_id) if f.id != folder.id]
            if folder.parent_id != parent_id and any(
                f.name.lower() == folder.name.lower() for f in siblings
            ):
                raise VaultError(f"Er bestaat daar al een map '{folder.name}'.")

            def apply():
                folder.parent_id = parent_id
                folder.parent = parent

            self._relocate_folder(folder, apply)
            pos = len(siblings) if position is None else max(0, min(position, len(siblings)))
            siblings.insert(pos, folder)
            for i, f in enumerate(siblings):
                f.position = i

    def delete_folder(self, folder: Folder, source: str = "web") -> None:
        """Move all notes inside to the trash and remove the folder."""
        with self.lock:
            ids = self._descendant_ids(folder)
            for note in list(
                self.session.scalars(
                    select(Note).where(Note.folder_id.in_(ids), Note.deleted_at.is_(None))
                )
            ):
                self.delete_note(note, source=source)
            rel = self.folder_path(folder)
            for f in reversed(ids):
                obj = self.session.get(Folder, f)
                if obj:
                    self.session.delete(obj)
            self.session.flush()
            shutil.rmtree(self.notes_dir / rel, ignore_errors=True)

    def tree(self) -> tuple[list[TreeFolder], list[Note]]:
        """Folders with their notes, plus loose notes at the root (excluding the inbox)."""
        folders = self.folders()
        nodes = {f.id: TreeFolder(f, "") for f in folders}
        roots: list[TreeFolder] = []
        for f in folders:
            node = nodes[f.id]
            node.path = self.folder_path(f)
            if f.parent_id and f.parent_id in nodes:
                nodes[f.parent_id].folders.append(node)
            else:
                roots.append(node)
        root_notes = []
        for note in self.session.scalars(
            select(Note)
            .where(Note.user_id == self.user.id, Note.deleted_at.is_(None), ~Note.is_inbox)
            .order_by(Note.position, Note.title)
        ):
            if note.folder_id and note.folder_id in nodes:
                nodes[note.folder_id].notes.append(note)
            else:
                root_notes.append(note)
        return roots, root_notes

    # --- notes ------------------------------------------------------------------------

    def get_note(self, note_id: str, include_deleted: bool = False) -> Note:
        note = self.session.get(Note, note_id)
        if note is None or note.user_id != self.user.id:
            raise VaultError("Notitie niet gevonden.")
        if note.deleted_at is not None and not include_deleted:
            raise VaultError("Notitie staat in de prullenbak.")
        return note

    def find_note(self, ref: str) -> Note:
        """Find a note by id, by 'Folder/Title' path, or by title."""
        ref = ref.strip()
        note = self.session.get(Note, ref)
        if note is not None and note.user_id == self.user.id and note.deleted_at is None:
            return note
        base = select(Note).where(Note.user_id == self.user.id, Note.deleted_at.is_(None))
        path = ref.removesuffix(".md")
        note = self.session.scalar(base.where(func.lower(Note.path) == (path + ".md").lower()))
        if note:
            return note
        matches = list(self.session.scalars(base.where(func.lower(Note.title) == ref.lower())))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            paths = ", ".join(m.path for m in matches)
            raise VaultError(f"Meerdere notities heten '{ref}': {paths}. Gebruik het pad of id.")
        raise VaultError(f"Notitie '{ref}' niet gevonden.")

    def file_path(self, note: Note) -> Path:
        return self.notes_dir / note.path

    def read(self, note: Note) -> tuple[dict, str]:
        """Return (frontmatter, body) of a note."""
        path = self.file_path(note) if note.deleted_at is None else self._trash_path(note)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, ""
        return md.split_frontmatter(raw)

    def body(self, note: Note) -> str:
        return self.read(note)[1]

    def _unique_path(self, folder: Folder | None, title: str, exclude: Note | None = None) -> str:
        rel_dir = self.folder_path(folder)
        stem = md.safe_filename(title)
        candidate, n = stem, 2
        while True:
            rel = f"{rel_dir}/{candidate}.md" if rel_dir else f"{candidate}.md"
            if rel.lower() == "inbox.md" and not (exclude and exclude.is_inbox):
                candidate = f"{stem} ({n})"
                n += 1
                continue
            other = self.session.scalar(
                select(Note).where(
                    Note.user_id == self.user.id,
                    Note.deleted_at.is_(None),
                    func.lower(Note.path) == rel.lower(),
                )
            )
            on_disk = (self.notes_dir / rel).exists()
            if (other is None or other is exclude) and (
                not on_disk or (exclude is not None and exclude.path.lower() == rel.lower())
            ):
                return rel
            candidate = f"{stem} ({n})"
            n += 1

    def _write_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _shift_positions(self, folder_id: int | None) -> int:
        """Make room at the top of a folder; returns the position for a new note."""
        cond = Note.folder_id == folder_id if folder_id else Note.folder_id.is_(None)
        lowest = self.session.scalar(
            select(func.min(Note.position)).where(
                Note.user_id == self.user.id, Note.deleted_at.is_(None), cond
            )
        )
        return (lowest if lowest is not None else 1) - 1

    def create_note(
        self,
        title: str,
        body: str | None = None,
        folder: Folder | None = None,
        tags: list[str] | None = None,
        source: str = "web",
        is_inbox: bool = False,
        created: datetime | None = None,
    ) -> Note:
        with self.lock:
            title = (title or "").strip() or (md.extract_title(body or "") or "Naamloos")
            body = body if body is not None else ""
            if md.extract_title(body) is None:
                body = md.set_title(body, title)
            else:
                title = md.extract_title(body) or title
            note = Note(
                id=md.new_id(),
                user_id=self.user.id,
                folder_id=folder.id if folder else None,
                folder=folder,
                title=title,
                path="",
                is_inbox=is_inbox,
                position=self._shift_positions(folder.id if folder else None),
                tags=md.normalize_tags(tags or []),
                created_at=created or utcnow(),
                updated_by=source,
            )
            note.path = "Inbox.md" if is_inbox else self._unique_path(folder, title)
            self.session.add(note)
            self.session.flush()
            self._store(note, body, source, new=True)
            return note

    def save(
        self,
        note: Note,
        body: str,
        source: str = "web",
        tags: list[str] | None = None,
        base_hash: str | None = None,
    ) -> Note:
        """Replace a note's body (without frontmatter). Title follows the first H1."""
        with self.lock:
            if base_hash and base_hash != note.content_hash:
                current = self.body(note)
                if content_hash(current) != base_hash:
                    raise ConflictError(note, current)
            if tags is not None:
                note.tags = md.normalize_tags(tags)
            self._store(note, body, source)
            return note

    def set_tags(self, note: Note, tags: list[str], source: str = "web") -> Note:
        return self.save(note, self.body(note), source=source, tags=tags)

    def _store(self, note: Note, body: str, source: str, new: bool = False) -> None:
        body = body.replace("\r\n", "\n")
        taken = set(
            self.session.scalars(
                select(Task.short_id).where(Task.user_id == self.user.id, Task.note_id != note.id)
            )
        )
        body = md.assign_task_ids(body, taken)
        title = md.extract_title(body) or note.title or "Naamloos"
        if title != note.title and not note.is_inbox:
            old = self.file_path(note)
            note.title = title
            new_rel = self._unique_path(note.folder, title, exclude=note)
            if new_rel != note.path:
                note.path = new_rel
                if old.exists():
                    self._write_file(self.file_path(note), old.read_text(encoding="utf-8"))
                    old.unlink()
        meta, _ = self.read(note) if not new else ({}, "")
        meta = {k: v for k, v in meta.items() if k not in ("id", "tags", "created", "updated")}
        now = utcnow()
        front = {
            "id": note.id,
            "tags": list(note.tags or []),
            "created": note.created_at.replace(microsecond=0).isoformat(),
            "updated": now.replace(microsecond=0).isoformat(),
            **meta,
        }
        changed = new or content_hash(body) != note.content_hash
        self._write_file(self.file_path(note), md.join_frontmatter(front, body))
        if changed or tags_changed(note):
            note.updated_at = now
            note.updated_by = source
        note.content_hash = content_hash(body)
        self._add_version(note, body, source)
        self._reindex(note, body)
        self.session.flush()
        if changed and self.on_change:
            self.on_change(note.id)

    def _add_version(self, note: Note, body: str, source: str) -> None:
        last = self.session.scalar(
            select(NoteVersion)
            .where(NoteVersion.note_id == note.id)
            .order_by(NoteVersion.id.desc())
            .limit(1)
        )
        now = utcnow()
        if last and last.content == body:
            return
        if last and last.source == source == "web" and now - last.created_at < VERSION_COALESCE:
            last.content, last.title, last.updated_at = body, note.title, now
            return
        self.session.add(
            NoteVersion(note_id=note.id, title=note.title, content=body, source=source)
        )

    def versions(self, note: Note) -> list[NoteVersion]:
        return list(
            self.session.scalars(
                select(NoteVersion)
                .where(NoteVersion.note_id == note.id)
                .order_by(NoteVersion.id.desc())
            )
        )

    def restore_version(self, note: Note, version_id: int, source: str = "web") -> Note:
        version = self.session.get(NoteVersion, version_id)
        if version is None or version.note_id != note.id:
            raise VaultError("Versie niet gevonden.")
        return self.save(note, version.content, source=source)

    def move_note(
        self, note: Note, folder: Folder | None, position: int | None = None, source: str = "web"
    ) -> Note:
        with self.lock:
            if note.is_inbox:
                raise VaultError("De Inbox kan niet verplaatst worden.")
            if note.folder_id != (folder.id if folder else None):
                old = self.file_path(note)
                note.folder_id = folder.id if folder else None
                note.folder = folder
                note.path = self._unique_path(folder, note.title, exclude=None)
                if old.exists():
                    self.file_path(note).parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old, self.file_path(note))
                note.updated_at, note.updated_by = utcnow(), source
                self._touch_tasks(note)
            cond = Note.folder_id == note.folder_id if note.folder_id else Note.folder_id.is_(None)
            siblings = [
                n
                for n in self.session.scalars(
                    select(Note)
                    .where(Note.user_id == self.user.id, Note.deleted_at.is_(None), cond)
                    .where(~Note.is_inbox)
                    .order_by(Note.position, Note.title)
                )
                if n.id != note.id
            ]
            pos = 0 if position is None else max(0, min(position, len(siblings)))
            siblings.insert(pos, note)
            for i, n in enumerate(siblings):
                n.position = i
            self.session.flush()
            return note

    def _trash_path(self, note: Note) -> Path:
        return self.trash_dir / f"{note.id}.md"

    def delete_note(self, note: Note, source: str = "web") -> None:
        with self.lock:
            if note.is_inbox:
                raise VaultError("De Inbox kan niet verwijderd worden.")
            path = self.file_path(note)
            if path.exists():
                os.replace(path, self._trash_path(note))
            note.deleted_at = utcnow()
            note.updated_by = source
            self._drop_index(note)
            self.session.flush()

    def restore_note(self, note: Note, source: str = "web") -> Note:
        with self.lock:
            if note.deleted_at is None:
                return note
            body = self.read(note)[1]
            folder = note.folder if note.folder_id else None
            note.deleted_at = None
            note.path = self._unique_path(folder, note.title)
            note.position = self._shift_positions(note.folder_id)
            trash = self._trash_path(note)
            self._store(note, body, source)
            trash.unlink(missing_ok=True)
            return note

    def purge_note(self, note: Note) -> None:
        with self.lock:
            self._trash_path(note).unlink(missing_ok=True)
            self._drop_index(note)
            self.session.delete(note)
            self.session.flush()

    def trash(self) -> list[Note]:
        return list(
            self.session.scalars(
                select(Note)
                .where(Note.user_id == self.user.id, Note.deleted_at.is_not(None))
                .order_by(Note.deleted_at.desc())
            )
        )

    def inbox(self) -> Note:
        note = self.session.scalar(
            select(Note).where(
                Note.user_id == self.user.id, Note.is_inbox, Note.deleted_at.is_(None)
            )
        )
        if note is None:
            note = self.create_note(
                INBOX_TITLE, f"# {INBOX_TITLE}\n", is_inbox=True, source="system"
            )
        return note

    def recent(self, limit: int = 10) -> list[Note]:
        return list(
            self.session.scalars(
                select(Note)
                .where(Note.user_id == self.user.id, Note.deleted_at.is_(None))
                .order_by(Note.updated_at.desc())
                .limit(limit)
            )
        )

    # --- indexing -----------------------------------------------------------------------

    def _drop_index(self, note: Note) -> None:
        self.session.execute(text("DELETE FROM notes_fts WHERE note_id = :id"), {"id": note.id})
        self.session.execute(delete(Link).where(Link.source_id == note.id))
        for task in list(self.session.scalars(select(Task).where(Task.note_id == note.id))):
            self._log_change(task.caldav_name, deleted=True)
            self.session.delete(task)

    def _reindex(self, note: Note, body: str) -> None:
        # links
        self.session.execute(delete(Link).where(Link.source_id == note.id))
        for target in md.parse_wikilinks(body):
            self.session.add(Link(source_id=note.id, target_title=target.lower()))
        # attachments referenced from this note
        ids = md.attachment_ids(body)
        if ids:
            self.session.execute(
                update(Attachment)
                .where(Attachment.user_id == self.user.id, Attachment.id.in_(ids))
                .values(note_id=note.id)
            )
        # tasks
        self._sync_tasks(note, body)
        # full-text
        attachment_text = " ".join(
            t
            for t in self.session.scalars(
                select(Attachment.text).where(
                    Attachment.note_id == note.id, Attachment.text.is_not(None)
                )
            )
            if t
        )
        self.session.execute(text("DELETE FROM notes_fts WHERE note_id = :id"), {"id": note.id})
        self.session.execute(
            text(
                "INSERT INTO notes_fts (note_id, user_id, title, body, tags, attachments) "
                "VALUES (:id, :uid, :title, :body, :tags, :att)"
            ),
            {
                "id": note.id,
                "uid": self.user.id,
                "title": note.title,
                "body": md.plain_text(body),
                "tags": " ".join(note.tags or []),
                "att": attachment_text[:200_000],
            },
        )

    def _log_change(self, caldav_name: str, deleted: bool = False) -> None:
        self.session.add(TaskChange(user_id=self.user.id, caldav_name=caldav_name, deleted=deleted))

    def _touch_tasks(self, note: Note) -> None:
        """The CalDAV representation depends on the folder; flag the tasks as changed."""
        for task in self.session.scalars(select(Task).where(Task.note_id == note.id)):
            task.updated_at = utcnow()
            self._log_change(task.caldav_name)

    def _sync_tasks(self, note: Note, body: str) -> None:
        parsed = {t.short_id: t for t in md.parse_tasks(body) if t.short_id}
        existing = {
            t.short_id: t
            for t in self.session.scalars(
                select(Task).where(Task.user_id == self.user.id, Task.short_id.in_(list(parsed)))
            )
        }
        for task in list(self.session.scalars(select(Task).where(Task.note_id == note.id))):
            if task.short_id not in parsed:
                self._log_change(task.caldav_name, deleted=True)
                self.session.delete(task)
        for short_id, p in parsed.items():
            task = existing.get(short_id)
            if task is None:
                task = Task(
                    user_id=self.user.id,
                    note_id=note.id,
                    short_id=short_id,
                    title=p.title,
                    due=p.due,
                    done=p.done,
                    done_on=p.done_on,
                    line=p.line,
                    caldav_name=f"{short_id}.ics",
                    caldav_uid=f"{short_id}@schrift",
                )
                self.session.add(task)
                self._log_change(task.caldav_name)
                continue
            fields = (p.title, p.due, p.done, p.done_on if p.done else None, note.id)
            current = (
                task.title,
                task.due,
                task.done,
                task.done_on if task.done else None,
                task.note_id,
            )
            task.line = p.line
            if fields != current:
                task.title, task.due, task.done, task.done_on = p.title, p.due, p.done, p.done_on
                task.note_id = note.id
                task.updated_at = utcnow()
                self._log_change(task.caldav_name)
        self.session.flush()

    # --- tasks ----------------------------------------------------------------------------

    def get_task(self, short_id: str) -> Task:
        task = self.session.scalar(
            select(Task).where(Task.user_id == self.user.id, Task.short_id == short_id)
        )
        if task is None:
            raise VaultError(f"Taak '{short_id}' niet gevonden.")
        return task

    def tasks(self, include_done: bool = False) -> list[Task]:
        query = (
            select(Task)
            .join(Note, Task.note_id == Note.id)
            .where(Task.user_id == self.user.id, Note.deleted_at.is_(None))
        )
        if not include_done:
            query = query.where(~Task.done)
        return list(self.session.scalars(query.order_by(Task.due.is_(None), Task.due, Task.id)))

    def _edit_task(self, task: Task, source: str, edit) -> Task:
        with self.lock:
            note = task.note
            body = self.body(note)
            parsed = md.find_task(body, task.short_id)
            if parsed is None:
                raise VaultError("De taak staat niet meer in de notitie.")
            result = edit(body, parsed)
            self.save(note, result, source=source)
            return task

    def update_task(
        self,
        task: Task,
        *,
        title: str | None = None,
        due: str | None | type(...) = ...,
        done: bool | None = None,
        source: str = "web",
    ) -> Task:
        def edit(body, parsed):
            if title is not None and title.strip():
                parsed.title = title.strip()
            if due is not ...:
                parsed.due = due or None
            if done is not None and done != parsed.done:
                parsed.done = done
                parsed.done_on = local_today(self.user) if done else None
            return md.replace_task(body, parsed)

        return self._edit_task(task, source, edit)

    def delete_task(self, task: Task, source: str = "web") -> None:
        self._edit_task(task, source, lambda body, parsed: md.remove_task(body, parsed))

    def create_task(
        self,
        title: str,
        due: str | None = None,
        note: Note | None = None,
        section: str | None = None,
        source: str = "web",
        short_id: str | None = None,
    ) -> Task:
        with self.lock:
            note = note or self.inbox()
            taken = set(
                self.session.scalars(select(Task.short_id).where(Task.user_id == self.user.id))
            )
            if short_id is None or short_id in taken:
                short_id = md.new_short_id()
                while short_id in taken:
                    short_id = md.new_short_id()
            parsed = md.ParsedTask(
                line=0,
                indent="",
                bullet="-",
                done=False,
                title=title.strip(),
                due=due or None,
                done_on=None,
                short_id=short_id,
            )
            body = md.append_task(self.body(note), parsed, under_heading=section)
            self.save(note, body, source=source)
            return self.get_task(short_id)

    # --- maintenance ------------------------------------------------------------------------

    def scan(self) -> int:
        """Index Markdown files that were added or changed outside Schrift."""
        changed = 0
        with self.lock:
            known = {
                n.path.lower(): n
                for n in self.session.scalars(
                    select(Note).where(Note.user_id == self.user.id, Note.deleted_at.is_(None))
                )
            }
            for path in sorted(self.notes_dir.rglob("*.md")):
                if any(part.startswith(".") for part in path.relative_to(self.notes_dir).parts):
                    continue
                rel = path.relative_to(self.notes_dir).as_posix()
                meta, body = md.split_frontmatter(path.read_text(encoding="utf-8"))
                note = known.get(rel.lower())
                if note is None:
                    folder_rel = str(Path(rel).parent)
                    folder = (
                        self.folder_by_path(folder_rel, create=True) if folder_rel != "." else None
                    )
                    is_inbox = rel.lower() == "inbox.md"
                    note = Note(
                        id=str(meta.get("id") or md.new_id())[:26],
                        user_id=self.user.id,
                        folder_id=folder.id if folder else None,
                        folder=folder,
                        title=md.extract_title(body) or Path(rel).stem,
                        path=rel,
                        is_inbox=is_inbox,
                        position=self._shift_positions(folder.id if folder else None),
                        tags=md.normalize_tags(meta.get("tags", [])),
                    )
                    if self.session.get(Note, note.id) is not None:
                        note.id = md.new_id()
                    self.session.add(note)
                    self.session.flush()
                    self._store(note, body, "import")
                    changed += 1
                elif content_hash(body) != note.content_hash:
                    note.tags = md.normalize_tags(meta.get("tags", note.tags))
                    self._store(note, body, "import")
                    changed += 1
        return changed


def tags_changed(note: Note) -> bool:
    from sqlalchemy import inspect

    state = inspect(note)
    return bool(state.attrs.tags.history.has_changes()) if state.persistent else False


def local_today(user: User) -> str:
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(user.timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date().isoformat()
