import io
import mimetypes
import re
import shutil
from pathlib import Path

from sqlalchemy import select

from schrift import markdown as md
from schrift.models import Attachment, Note
from schrift.services.vault import Vault, VaultError

TEXT_TYPES = ("text/plain", "text/markdown", "text/csv")
# Images shown inline in notes. SVG is excluded: it can carry scripts, so it is
# always served as a download.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def is_image(attachment: Attachment) -> bool:
    return attachment.content_type in IMAGE_TYPES


def _clean_name(name: str) -> str:
    name = Path(name or "bestand").name
    name = re.sub(r"[^\w.\- ()]+", "_", name, flags=re.UNICODE).strip() or "bestand"
    return name[:150]


def extract_text(data: bytes, content_type: str) -> str | None:
    if content_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages[:500]]
            return "\n".join(pages).strip() or None
        except Exception:
            return None
    if content_type in TEXT_TYPES:
        return data.decode("utf-8", errors="replace")[:500_000]
    return None


def save_attachment(
    vault: Vault, filename: str, data: bytes, note_id: str | None, max_bytes: int
) -> Attachment:
    if len(data) > max_bytes:
        raise VaultError(f"Bestand is te groot (max {max_bytes // (1024 * 1024)} MB).")
    name = _clean_name(filename)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    attachment = Attachment(
        id=md.new_id(),
        user_id=vault.user.id,
        note_id=note_id,
        filename=name,
        content_type=content_type,
        size=len(data),
        text=extract_text(data, content_type),
    )
    folder = vault.attachments_dir / attachment.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    vault.session.add(attachment)
    vault.session.flush()
    return attachment


def attachment_url(attachment: Attachment) -> str:
    return f"/attachments/{attachment.id}/{attachment.filename}"


def attachment_path(vault: Vault, attachment: Attachment) -> Path:
    return vault.attachments_dir / attachment.id / attachment.filename


def get_attachment(vault: Vault, attachment_id: str) -> Attachment:
    attachment = vault.session.get(Attachment, attachment_id)
    if attachment is None or attachment.user_id != vault.user.id:
        raise VaultError("Bijlage niet gevonden.")
    return attachment


def note_attachments(vault: Vault, note_id: str) -> list[Attachment]:
    return list(
        vault.session.scalars(
            select(Attachment).where(Attachment.note_id == note_id).order_by(Attachment.created_at)
        )
    )


def referenced_ids(body: str) -> list[str]:
    """Ids of the attachments a note body links to, in order."""
    return list(dict.fromkeys(re.findall(r"\(/attachments/([0-9A-Za-z]+)/", body)))


def note_images(vault: Vault, note: Note) -> list[Attachment]:
    """The images shown in a note, plus images attached to it but not shown."""
    ids = referenced_ids(vault.body(note))
    found = {
        a.id: a
        for a in vault.session.scalars(
            select(Attachment).where(Attachment.user_id == vault.user.id, Attachment.id.in_(ids))
        )
    }
    images = [found[i] for i in ids if i in found and is_image(found[i])]
    images += [a for a in note_attachments(vault, note.id) if is_image(a) and a not in images]
    return images


MODEL_IMAGE_SIDE = 1568  # larger images are scaled down by the model anyway


def image_for_model(vault: Vault, attachment: Attachment) -> tuple[bytes, str]:
    """An image's bytes and format, scaled down for a model client."""
    max_side = MODEL_IMAGE_SIDE
    data = attachment_path(vault, attachment).read_bytes()
    fmt = attachment.content_type.split("/")[1]
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if max(img.size) <= max_side and fmt != "gif":
                return data, fmt
            img.thumbnail((max_side, max_side))
            out = io.BytesIO()
            if img.mode in ("RGBA", "LA", "P"):
                img.save(out, "PNG", optimize=True)
                return out.getvalue(), "png"
            img.convert("RGB").save(out, "JPEG", quality=85)
            return out.getvalue(), "jpeg"
    except Exception:
        return data, fmt


def markdown_for(attachment: Attachment) -> str:
    url = attachment_url(attachment).replace(" ", "%20")
    if is_image(attachment):
        return f"![{attachment.filename}]({url})"
    return f"[{attachment.filename}]({url})"


def remove_references(body: str, attachment_id: str) -> str:
    """Drop the images and links that point at an attachment, and lines left empty."""
    ref = re.compile(
        r"!?\[[^\]\n]*\]\(/attachments/" + re.escape(attachment_id) + r"/[^)\n]*\)[ \t]*"
    )
    lines = []
    for line in body.split("\n"):
        if ref.search(line):
            line = ref.sub("", line).rstrip()
            if line.strip() in ("", "-", "*", "+"):
                continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def delete_attachment(vault: Vault, attachment: Attachment, source: str = "web") -> Note | None:
    """Delete the file and take it out of the note it belongs to (as a new version)."""
    note = vault.session.get(Note, attachment.note_id) if attachment.note_id else None
    if note is not None and (note.user_id != vault.user.id or note.deleted_at is not None):
        note = None
    attachment_id = attachment.id
    shutil.rmtree(vault.attachments_dir / attachment_id, ignore_errors=True)
    vault.session.delete(attachment)
    vault.session.flush()
    if note is None:
        return None
    body = vault.body(note)
    cleaned = remove_references(body, attachment_id)
    if cleaned != body:
        vault.save(note, cleaned, source=source)
    else:
        vault._reindex(note, body)  # drop the attachment's text from search
    return note
