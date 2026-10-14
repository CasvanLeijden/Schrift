"""MCP server: everything a user can do in Schrift, available to AI assistants.

Every change is stored as a version with source "mcp", so it shows up as
"Gewijzigd door Claude" in the web app and can be rolled back.
"""

import base64
import re
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from sqlalchemy import select

from schrift import __version__
from schrift import markdown as md
from schrift.context import AppContext
from schrift.models import Link, Note, Task, User
from schrift.oauth import SCOPE, SchriftOAuthProvider
from schrift.services import attachments as att
from schrift.services.search import MODES, hybrid
from schrift.services.vault import Vault, VaultError

SOURCE = "mcp"

INSTRUCTIONS = """\
Schrift is the user's personal notes, wiki and task app. Notes are Markdown files in
folders. The top-level folder is the context (for example Prive, Forfacts, ZOKA).

Conventions:
- The first line of a note is its title as an H1: `# Title`.
- A task is a Markdown checkbox line: `- [ ] Call the client`. An optional due date
  follows the text as `📅 2026-10-20` or `📅 2026-10-20 14:00` (24h, user's local time).
  Schrift adds a `🆔 abc12` id to each task line and `✅ date` when it is done; keep
  those tokens when you edit a line, never invent them.
- Only the user's own actions become checkboxes. Actions for other people are plain
  bullets: `- Jan: send the contract`.
- Link notes with [[Note title]]. Tags live in the note's tags list, not in the text.
- Meeting reports go in `<Context>/Verslagen` and are created with
  create_meeting_report.
- Open tasks sync to the user's reminders app; ticking one there ticks it here.

Prefer edit_note (exact text replacement) or update_note with mode "append" or
"replace_section" over rewriting whole notes. Deleting moves a note to the trash.
Every change is versioned and can be undone with restore_version.
"""


@contextmanager
def user_vault(ctx: AppContext):
    """The vault of the user behind the bearer token. Errors become readable tool errors."""
    token = get_access_token()
    if token is None or not token.subject:
        raise ToolError("Not authenticated.")
    with ctx.db.session() as session:
        user = session.get(User, int(token.subject))
        if user is None:
            raise ToolError("Unknown user.")
        try:
            yield ctx.vault(session, user)
        except (VaultError, ValueError, PermissionError, KeyError) as e:
            raise ToolError(str(e)) from e


def note_summary(vault: Vault, note: Note) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "path": note.path,
        "tags": note.tags or [],
        "updated": note.updated_at.isoformat(timespec="minutes"),
    }


def task_dict(vault: Vault, task: Task) -> dict:
    note = task.note
    top = vault.top_folder(note.folder) if not note.is_inbox else None
    return {
        "id": task.short_id,
        "title": task.title,
        "due": task.due,
        "done": task.done,
        "done_on": task.done_on,
        "context": "Inbox" if note.is_inbox else (top.name if top else None),
        "note": {"id": note.id, "title": note.title, "path": note.path},
    }


def resolve_folder(vault: Vault, path: str | None, create: bool = True):
    if not path or not path.strip("/ "):
        return None
    return vault.folder_by_path(path, create=create)


def section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    """Line range (start, end) of the body under a heading, excluding the heading."""
    pattern = re.compile(r"^(#{1,6})\s+" + re.escape(heading.strip().lstrip("# ")) + r"\s*$", re.I)
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            level = len(m.group(1))
            end = i + 1
            while end < len(lines):
                h = re.match(r"^(#{1,6})\s", lines[end])
                if h and len(h.group(1)) <= level:
                    break
                end += 1
            return i + 1, end
    return None


class ActionItem(BaseModel):
    task: str = Field(description="What the user has to do.")
    due: str | None = Field(default=None, description="YYYY-MM-DD or 'YYYY-MM-DD HH:MM'.")


class OtherAction(BaseModel):
    who: str
    action: str


class ImportNote(BaseModel):
    path: str = Field(description="Folder path plus title, e.g. 'ZOKA/Klanten/Klant X'.")
    content: str = Field(description="Markdown body. A leading '# Title' is optional.")
    tags: list[str] = Field(default_factory=list)


def build_server(ctx: AppContext) -> MCPServer:
    base = ctx.settings.base_url.rstrip("/")
    provider = SchriftOAuthProvider(ctx)
    mcp = MCPServer(
        name="schrift",
        title="Schrift",
        description="Notes, wiki and tasks in Markdown",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=base,
            resource_server_url=f"{base}/mcp",
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    # --- notes ----------------------------------------------------------------------------

    @mcp.tool()
    def search_notes(query: str, limit: int = 10, mode: str = "all") -> list[dict]:
        """Search notes by words (titles, text, tags, attachment text) and by meaning.

        mode: "all" (words and meaning), "text" (exact words only) or "meaning" (only
        semantically similar notes). Returns the best matches with a short snippet.
        Use get_note to read one.
        """
        if mode not in MODES:
            raise ValueError("mode must be 'all', 'text' or 'meaning'.")
        with user_vault(ctx) as vault:
            results = hybrid(
                vault.session, vault.user.id, query, ctx.semantic, limit=limit, mode=mode
            )
            return [
                {
                    **note_summary(vault, r.note),
                    "snippet": re.sub(r"</?mark>", "", r.snippet),
                    "match": "meaning" if r.via == "meaning" else "words",
                }
                for r in results
            ]

    @mcp.tool()
    def list_notes(folder: str | None = None) -> dict:
        """List folders and notes, optionally only below one folder (e.g. 'ZOKA')."""
        with user_vault(ctx) as vault:
            roots, root_notes = vault.tree()

            def walk(node) -> dict:
                return {
                    "folder": node.path,
                    "notes": [{"id": n.id, "title": n.title} for n in node.notes],
                    "subfolders": [walk(c) for c in node.folders],
                }

            if folder:
                target = folder.strip("/").lower()

                def find(nodes):
                    for node in nodes:
                        if node.path.lower() == target:
                            return node
                        hit = find(node.folders)
                        if hit:
                            return hit
                    return None

                node = find(roots)
                if node is None:
                    raise ValueError(f"Folder '{folder}' does not exist.")
                return walk(node)
            return {
                "folders": [walk(n) for n in roots],
                "notes_without_folder": [{"id": n.id, "title": n.title} for n in root_notes],
                "inbox": vault.inbox().id,
            }

    @mcp.tool()
    def get_note(note: str) -> dict:
        """Read a note. `note` is its id, its path ('ZOKA/Verslagen/Overleg') or its title."""
        with user_vault(ctx) as vault:
            n = vault.find_note(note)
            backlinks = vault.session.scalars(
                select(Note.title)
                .join(Link, Link.source_id == Note.id)
                .where(
                    Link.target_title == n.title.lower(),
                    Note.user_id == vault.user.id,
                    Note.deleted_at.is_(None),
                )
                .distinct()
            )
            return {
                **note_summary(vault, n),
                "updated_by": "Claude" if n.updated_by == SOURCE else n.updated_by,
                "content": vault.body(n),
                "backlinks": list(backlinks),
                "attachments": [
                    {"id": a.id, "filename": a.filename, "url": att.attachment_url(a)}
                    for a in att.note_attachments(vault, n.id)
                ],
            }

    @mcp.tool()
    def create_note(
        title: str, content: str = "", folder: str | None = None, tags: list[str] | None = None
    ) -> dict:
        """Create a note. `folder` is a path like 'ZOKA/Klanten'; missing folders are created.

        `content` is Markdown without the title; the title becomes the first-line H1.
        """
        with user_vault(ctx) as vault:
            f = resolve_folder(vault, folder)
            title_in_body = md.extract_title(content)
            body = content if title_in_body else md.join_title(title, content)
            n = vault.create_note(title, body, folder=f, tags=tags or [], source=SOURCE)
            return note_summary(vault, n)

    @mcp.tool()
    def update_note(
        note: str,
        content: str | None = None,
        mode: str = "replace",
        section: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Change a note.

        mode:
        - "replace": `content` becomes the whole note (Markdown, may start with '# Title').
        - "append": add `content` at the end.
        - "prepend": add `content` right below the title.
        - "replace_section": replace the text under the heading `section`
          (the section is added at the end when it does not exist).
        `title` renames the note; `tags` replaces its tags.
        """
        with user_vault(ctx) as vault:
            n = vault.find_note(note)
            body = vault.body(n)
            if content is not None:
                if mode == "replace":
                    body = content if md.extract_title(content) else md.join_title(n.title, content)
                elif mode == "append":
                    body = body.rstrip("\n") + "\n\n" + content.strip("\n") + "\n"
                elif mode == "prepend":
                    t, rest = md.split_title(body)
                    body = md.join_title(t or n.title, content.strip("\n") + "\n\n" + rest)
                elif mode == "replace_section":
                    if not section:
                        raise ValueError("replace_section needs `section`.")
                    lines = body.rstrip("\n").split("\n")
                    bounds = section_bounds(lines, section)
                    new = ["", *content.strip("\n").split("\n"), ""]
                    if bounds is None:
                        lines += ["", f"## {section.strip().lstrip('# ')}", *new]
                    else:
                        lines[bounds[0] : bounds[1]] = new
                    body = "\n".join(lines).rstrip("\n") + "\n"
                else:
                    raise ValueError("mode must be replace, append, prepend or replace_section.")
            if title:
                body = md.set_title(body, title)
            vault.save(n, body, source=SOURCE, tags=tags)
            return note_summary(vault, n)

    @mcp.tool()
    def edit_note(note: str, old_text: str, new_text: str) -> dict:
        """Replace an exact piece of text in a note (must occur exactly once)."""
        with user_vault(ctx) as vault:
            n = vault.find_note(note)
            body = vault.body(n)
            count = body.count(old_text)
            if count != 1:
                raise ValueError(
                    f"old_text occurs {count} times; it must occur exactly once. "
                    "Include more surrounding text."
                )
            vault.save(n, body.replace(old_text, new_text), source=SOURCE)
            return note_summary(vault, n)

    @mcp.tool()
    def move_note(note: str, folder: str | None = None) -> dict:
        """Move a note to another folder (path like 'Prive/Recepten'; empty for no folder)."""
        with user_vault(ctx) as vault:
            n = vault.move_note(vault.find_note(note), resolve_folder(vault, folder), source=SOURCE)
            return note_summary(vault, n)

    @mcp.tool()
    def delete_note(note: str) -> str:
        """Move a note to the trash. It can be restored with restore_note."""
        with user_vault(ctx) as vault:
            n = vault.find_note(note)
            vault.delete_note(n, source=SOURCE)
            return f"'{n.title}' is in the trash (id {n.id})."

    @mcp.tool()
    def restore_note(note_id: str) -> dict:
        """Restore a note from the trash by id."""
        with user_vault(ctx) as vault:
            n = vault.restore_note(vault.get_note(note_id, include_deleted=True), source=SOURCE)
            return note_summary(vault, n)

    @mcp.tool()
    def list_versions(note: str) -> list[dict]:
        """Earlier versions of a note, newest first. Source 'mcp' means an AI made the change."""
        with user_vault(ctx) as vault:
            n = vault.find_note(note)
            return [
                {
                    "id": v.id,
                    "source": v.source,
                    "saved": v.updated_at.isoformat(timespec="minutes"),
                }
                for v in vault.versions(n)
            ]

    @mcp.tool()
    def get_version(note: str, version_id: int) -> dict:
        """The content of one earlier version of a note."""
        with user_vault(ctx) as vault:
            n = vault.find_note(note)
            for v in vault.versions(n):
                if v.id == version_id:
                    return {"id": v.id, "source": v.source, "content": v.content}
            raise ValueError("Version not found.")

    @mcp.tool()
    def restore_version(note: str, version_id: int) -> dict:
        """Put an earlier version of a note back (the current text stays in the history)."""
        with user_vault(ctx) as vault:
            n = vault.restore_version(vault.find_note(note), version_id, source=SOURCE)
            return note_summary(vault, n)

    # --- folders and tags -----------------------------------------------------------------

    @mcp.tool()
    def list_folders() -> list[str]:
        """All folder paths, e.g. ['Prive', 'ZOKA', 'ZOKA/Verslagen']."""
        with user_vault(ctx) as vault:
            return sorted(vault.folder_path(f) for f in vault.folders())

    @mcp.tool()
    def create_folder(path: str) -> str:
        """Create a folder (and missing parents), e.g. 'ZOKA/Klanten'."""
        with user_vault(ctx) as vault:
            return vault.folder_path(vault.folder_by_path(path, create=True))

    @mcp.tool()
    def rename_folder(path: str, new_name: str) -> str:
        """Rename a folder. Notes inside move along."""
        with user_vault(ctx) as vault:
            folder = vault.folder_by_path(path, create=False)
            return vault.folder_path(vault.rename_folder(folder, new_name))

    @mcp.tool()
    def delete_folder(path: str) -> str:
        """Delete a folder; the notes in it go to the trash. Only do this when asked."""
        with user_vault(ctx) as vault:
            folder = vault.folder_by_path(path, create=False)
            vault.delete_folder(folder, source=SOURCE)
            return f"Folder '{path}' deleted; its notes are in the trash."

    @mcp.tool()
    def list_tags() -> list[str]:
        """All tags in use."""
        with user_vault(ctx) as vault:
            tags = {
                t
                for tags in vault.session.scalars(
                    select(Note.tags).where(
                        Note.user_id == vault.user.id, Note.deleted_at.is_(None)
                    )
                )
                for t in tags or []
            }
            return sorted(tags, key=str.lower)

    # --- tasks ----------------------------------------------------------------------------

    @mcp.tool()
    def list_tasks(
        status: str = "open",
        context: str | None = None,
        due_before: str | None = None,
        query: str | None = None,
    ) -> list[dict]:
        """List tasks. status: open, done or all. context: top folder name or 'Inbox'.

        due_before: YYYY-MM-DD, e.g. today's date for everything due today or overdue.
        """
        with user_vault(ctx) as vault:
            tasks = vault.tasks(include_done=status != "open")
            out = []
            for task in tasks:
                if status == "done" and not task.done:
                    continue
                d = task_dict(vault, task)
                if context and (d["context"] or "").lower() != context.lower():
                    continue
                if due_before and (not task.due or task.due[:10] > due_before):
                    continue
                if query and query.lower() not in task.title.lower():
                    continue
                out.append(d)
            return out

    @mcp.tool()
    def create_task(
        title: str, due: str | None = None, note: str | None = None, section: str | None = None
    ) -> dict:
        """Add a task. Without `note` it goes to the Inbox.

        due: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'. section: heading in the note to add it under.
        """
        with user_vault(ctx) as vault:
            target = vault.find_note(note) if note else None
            task = vault.create_task(title, due=due, note=target, section=section, source=SOURCE)
            return task_dict(vault, task)

    @mcp.tool()
    def update_task(
        task_id: str,
        title: str | None = None,
        due: str | None = None,
        clear_due: bool = False,
        done: bool | None = None,
    ) -> dict:
        """Change a task by its id (the 🆔 value): title, due date, or done/not done."""
        with user_vault(ctx) as vault:
            task = vault.get_task(task_id)
            new_due: Any = None if clear_due else (due if due is not None else ...)
            vault.update_task(task, title=title, due=new_due, done=done, source=SOURCE)
            return task_dict(vault, task)

    @mcp.tool()
    def complete_task(task_id: str) -> dict:
        """Mark a task as done."""
        with user_vault(ctx) as vault:
            task = vault.get_task(task_id)
            vault.update_task(task, done=True, source=SOURCE)
            return task_dict(vault, task)

    @mcp.tool()
    def delete_task(task_id: str) -> str:
        """Remove a task line from its note."""
        with user_vault(ctx) as vault:
            task = vault.get_task(task_id)
            title = task.title
            vault.delete_task(task, source=SOURCE)
            return f"Deleted task '{title}'."

    @mcp.tool()
    def overview() -> dict:
        """Today's overview: overdue and today's tasks, the next 7 days, and recent notes."""
        from schrift.services.vault import local_today

        with user_vault(ctx) as vault:
            today = local_today(vault.user)
            week = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
            tasks = [task_dict(vault, t) for t in vault.tasks()]
            return {
                "today": today,
                "overdue": [t for t in tasks if t["due"] and t["due"][:10] < today],
                "due_today": [t for t in tasks if t["due"] and t["due"][:10] == today],
                "next_7_days": [t for t in tasks if t["due"] and today < t["due"][:10] <= week],
                "without_date": len([t for t in tasks if not t["due"]]),
                "recent_notes": [note_summary(vault, n) for n in vault.recent(8)],
            }

    # --- attachments ----------------------------------------------------------------------

    @mcp.tool()
    def add_attachment(
        note: str,
        filename: str,
        content_base64: str | None = None,
        text: str | None = None,
        insert_link: bool = True,
    ) -> dict:
        """Attach a file to a note. Pass the file as base64, or plain text for text files.

        With insert_link the Markdown link (or image) is appended to the note.
        """
        with user_vault(ctx) as vault:
            n = vault.find_note(note)
            if content_base64 is not None:
                data = base64.b64decode(content_base64)
            elif text is not None:
                data = text.encode()
            else:
                raise ValueError("Pass content_base64 or text.")
            item = att.save_attachment(
                vault, filename, data, n.id, ctx.settings.max_upload_mb * 1024 * 1024
            )
            if insert_link:
                body = vault.body(n).rstrip("\n") + "\n\n" + att.markdown_for(item) + "\n"
                vault.save(n, body, source=SOURCE)
            return {
                "id": item.id,
                "filename": item.filename,
                "markdown": att.markdown_for(item),
                "has_text": bool(item.text),
            }

    @mcp.tool()
    def get_attachment(attachment_id: str) -> dict:
        """Metadata and extracted text (PDF and text files) of an attachment."""
        with user_vault(ctx) as vault:
            item = att.get_attachment(vault, attachment_id)
            return {
                "id": item.id,
                "filename": item.filename,
                "content_type": item.content_type,
                "size": item.size,
                "note_id": item.note_id,
                "text": item.text,
            }

    @mcp.tool()
    def delete_attachment(attachment_id: str) -> dict:
        """Delete an attachment for good, and remove its image or link from the note."""
        with user_vault(ctx) as vault:
            note = att.delete_attachment(vault, att.get_attachment(vault, attachment_id), SOURCE)
            return {"deleted": attachment_id, "note_id": note.id if note else None}

    # --- meeting reports and import -------------------------------------------------------

    @mcp.tool()
    def create_meeting_report(
        context: str,
        title: str,
        summary: str,
        meeting_date: str | None = None,
        participants: list[str] | None = None,
        discussed: str | None = None,
        decisions: list[str] | None = None,
        my_actions: list[ActionItem] | None = None,
        others_actions: list[OtherAction] | None = None,
        follow_up: str | None = None,
        raw_notes: str | None = None,
        related_note: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Store a meeting report in '<context>/Verslagen' with the standard structure.

        context: top folder (e.g. 'ZOKA'). my_actions become tasks that sync to the
        user's reminders; others_actions stay plain bullets. raw_notes keeps the user's
        original notes in a collapsible block. related_note becomes a [[link]].
        """
        with user_vault(ctx) as vault:
            from schrift.services.vault import local_today

            day = meeting_date or local_today(vault.user)
            meta = [f"**Datum:** {day}"]
            if participants:
                meta.append(f"**Deelnemers:** {', '.join(participants)}")
            if related_note:
                meta.append(f"**Over:** [[{related_note}]]")
            parts = [" · ".join(meta), "## Samenvatting", summary.strip()]
            if discussed:
                parts += ["## Besproken", discussed.strip()]
            if decisions:
                parts += ["## Besluiten", "\n".join(f"- {d}" for d in decisions)]
            if my_actions or others_actions:
                lines = []
                for a in my_actions or []:
                    lines.append(f"- [ ] {a.task}" + (f" {md.DUE} {a.due}" if a.due else ""))
                for o in others_actions or []:
                    lines.append(f"- {o.who}: {o.action}")
                parts += ["## Actiepunten", "\n".join(lines)]
            if follow_up:
                parts += ["## Vervolg", follow_up.strip()]
            if raw_notes:
                parts += [
                    "<details>\n<summary>Ruwe aantekeningen</summary>\n\n"
                    + raw_notes.strip()
                    + "\n\n</details>"
                ]
            folder = vault.folder_by_path(f"{context.strip('/')}/Verslagen", create=True)
            full_title = f"{day} {title.strip()}"
            body = md.join_title(full_title, "\n\n".join(parts))
            n = vault.create_note(full_title, body, folder=folder, tags=tags or [], source=SOURCE)
            created = [task_dict(vault, t) for t in vault.tasks() if t.note_id == n.id]
            return {**note_summary(vault, n), "tasks": created}

    @mcp.tool()
    def import_notes(notes: list[ImportNote]) -> list[dict]:
        """Create many notes at once, e.g. when moving from another app.

        Each note's `path` is 'Folder/Subfolder/Title'. Tasks must use '- [ ]' / '- [x]'
        with optional '📅 YYYY-MM-DD'; links between notes use [[Title]].
        """
        out = []
        with user_vault(ctx) as vault:
            for item in notes:
                parts = [p.strip() for p in item.path.strip("/").split("/") if p.strip()]
                if not parts:
                    out.append({"path": item.path, "error": "empty path"})
                    continue
                title, folder_path = parts[-1].removesuffix(".md"), "/".join(parts[:-1])
                try:
                    f = resolve_folder(vault, folder_path)
                    body = item.content
                    if md.extract_title(body) is None:
                        body = md.join_title(title, body)
                    n = vault.create_note(title, body, folder=f, tags=item.tags, source="import")
                    out.append({"path": n.path, "id": n.id})
                except VaultError as e:
                    out.append({"path": item.path, "error": str(e)})
        return out

    # --- prompts --------------------------------------------------------------------------

    @mcp.prompt(name="gespreksverslag", title="Gespreksverslag maken")
    def meeting_prompt(aantekeningen: str, context: str = "") -> str:
        """Turn rough meeting notes into a structured report in Schrift."""
        return f"""Maak van deze aantekeningen een helder gespreksverslag in Schrift.

1. Zoek eerst met search_notes naar relevante context (klant, project, eerdere verslagen)
   en lees die met get_note.
2. Schrijf een korte samenvatting, wat besproken is, de besluiten, en de actiepunten.
3. Alleen mijn eigen actiepunten worden taken (my_actions, met een datum als die genoemd
   is). Actiepunten van anderen gaan in others_actions.
4. Sla het op met create_meeting_report{f" in de context '{context}'" if context else ""},
   met raw_notes = de originele aantekeningen hieronder en related_note = de belangrijkste
   gerelateerde notitie als die bestaat.
5. Laat daarna kort zien wat je hebt opgeslagen en welke taken er zijn gemaakt.

Aantekeningen:
{aantekeningen}"""

    @mcp.prompt(name="importeren", title="Notities importeren")
    def import_prompt(bron: str) -> str:
        """Instructions for moving notes from another app into Schrift."""
        return f"""Importeer notities uit {bron} in Schrift met import_notes.

Regels voor het formaat:
- Elke notitie wordt Markdown. De mapstructuur gaat in `path` ('Map/Submap/Titel').
- Bovenste mappen zijn contexten (bijvoorbeeld Prive, Forfacts, ZOKA). Vraag bij twijfel.
- Taken: '- [ ] tekst' (open) of '- [x] tekst' (klaar), met optioneel ' 📅 JJJJ-MM-DD' of
  ' 📅 JJJJ-MM-DD UU:MM'. Voeg geen 🆔 toe, dat doet Schrift zelf.
- Links tussen notities: [[Titel]]. Tags in `tags`, niet in de tekst.
- Afbeeldingen en bijlagen: voeg ze daarna toe met add_attachment.
- Importeer in porties van maximaal 25 notities en meld wat er is gelukt."""

    return mcp


def mcp_http_app(mcp: MCPServer):
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="0.0.0.0",
    )
