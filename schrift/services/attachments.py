import io
import mimetypes
import re
from pathlib import Path

from sqlalchemy import select

from schrift import markdown as md
from schrift.models import Attachment
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


def markdown_for(attachment: Attachment) -> str:
    url = attachment_url(attachment).replace(" ", "%20")
    if is_image(attachment):
        return f"![{attachment.filename}]({url})"
    return f"[{attachment.filename}]({url})"
