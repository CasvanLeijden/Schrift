import html
import re
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from schrift.models import Note

_OPEN, _CLOSE = "\x02", "\x03"


@dataclass
class SearchResult:
    note: Note
    snippet: str  # HTML-safe, matches wrapped in <mark>
    score: float
    via: str  # "text" or "meaning"


def fts_query(query: str) -> str | None:
    words = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not words:
        return None
    return " ".join(f'"{w}"*' for w in words[:12])


def _safe_snippet(raw: str) -> str:
    escaped = html.escape(raw or "")
    return escaped.replace(_OPEN, "<mark>").replace(_CLOSE, "</mark>")


def fulltext(
    session: Session, user_id: int, query: str, limit: int = 30, tag: str | None = None
) -> list[SearchResult]:
    match = fts_query(query)
    if match is None:
        return []
    rows = session.execute(
        text(
            "SELECT note_id, snippet(notes_fts, -1, :o, :c, '…', 14) AS snip, bm25(notes_fts, "
            "0, 0, 8.0, 1.0, 4.0, 0.5) AS rank FROM notes_fts "
            "WHERE notes_fts MATCH :q AND user_id = :uid ORDER BY rank LIMIT :limit"
        ),
        {"o": _OPEN, "c": _CLOSE, "q": match, "uid": user_id, "limit": limit * 2},
    ).all()
    notes = {
        n.id: n
        for n in session.scalars(
            select(Note).where(Note.id.in_([r.note_id for r in rows]), Note.deleted_at.is_(None))
        )
    }
    results = []
    for r in rows:
        note = notes.get(r.note_id)
        if note is None:
            continue
        if tag and tag.lower() not in (t.lower() for t in note.tags or []):
            continue
        results.append(SearchResult(note, _safe_snippet(r.snip), -float(r.rank), "text"))
    return results[:limit]


MODES = ("all", "text", "meaning")


def hybrid(
    session: Session,
    user_id: int,
    query: str,
    semantic=None,
    limit: int = 20,
    mode: str = "all",
) -> list[SearchResult]:
    """Reciprocal-rank fusion of full-text and (when available) semantic results.

    mode "text" only matches words, "meaning" only searches semantically (and falls back
    to words when semantic search is not available).
    """
    if semantic is None and mode == "meaning":
        mode = "text"
    text_results = fulltext(session, user_id, query, limit=limit) if mode != "meaning" else []
    use_meaning = semantic is not None and mode != "text"
    meaning = semantic.search(session, user_id, query, limit=limit) if use_meaning else []
    scores: dict[str, float] = {}
    best: dict[str, SearchResult] = {}
    for rank, r in enumerate(text_results):
        scores[r.note.id] = scores.get(r.note.id, 0) + 1 / (60 + rank)
        best[r.note.id] = r
    for rank, r in enumerate(meaning):
        scores[r.note.id] = scores.get(r.note.id, 0) + 1 / (60 + rank)
        best.setdefault(r.note.id, r)
    ordered = sorted(scores, key=lambda nid: scores[nid], reverse=True)[:limit]
    return [SearchResult(best[n].note, best[n].snippet, scores[n], best[n].via) for n in ordered]
