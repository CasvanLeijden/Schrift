"""Optional semantic search with a small local multilingual model (fastembed).

Vectors are stored per text chunk in SQLite and searched with numpy; for a personal
knowledge base (thousands of chunks) a brute-force cosine search is fast enough and
avoids a native SQLite extension.
"""

import html
import logging
import queue
import re
import threading
import time

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from schrift import markdown as md
from schrift.db import Database
from schrift.models import Embedding, Note
from schrift.services.search import SearchResult

log = logging.getLogger("schrift.semantic")

CHUNK_CHARS = 900
MIN_SCORE = 0.3
RETRY_AFTER = 600  # seconds before trying to load the model again


def chunks(title: str, body: str) -> list[str]:
    text = md.plain_text(body)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) > CHUNK_CHARS:
            out.append(current)
            current = ""
        current = f"{current}\n{p}" if current else p
        while len(current) > CHUNK_CHARS * 2:
            out.append(current[:CHUNK_CHARS])
            current = current[CHUNK_CHARS:]
    if current:
        out.append(current)
    return [f"{title}\n{c}" for c in out] or [title]


class SemanticIndex:
    def __init__(self, db: Database, data_dir, model_name: str):
        self.db = db
        self.data_dir = data_dir
        self.cache_dir = data_dir / "models"
        self.model_name = model_name
        self._model = None
        self._failed_at: float | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._missed: set[str] = set()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- model ---------------------------------------------------------------------

    def model(self):
        with self._lock:
            recently_failed = self._failed_at and time.monotonic() - self._failed_at < RETRY_AFTER
            if self._model is None and not recently_failed:
                try:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))
                    self._failed_at = None
                    if self._missed:
                        for note_id in self._missed:
                            self._queue.put(note_id)
                        self._missed.clear()
                except Exception:
                    log.exception("Semantic search unavailable: could not load the model")
                    self._failed_at = time.monotonic()
            return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self.model()
        if model is None:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = np.array(list(model.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-9)

    # --- background indexing ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="semantic", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=5)
            self._thread = None

    def schedule(self, note_id: str) -> None:
        self._queue.put(note_id)

    def schedule_stale(self) -> None:
        with self.db.session() as session:
            for note_id in session.scalars(
                select(Note.id).where(
                    Note.deleted_at.is_(None), Note.embedded_hash != Note.content_hash
                )
            ):
                self._queue.put(note_id)

    def _worker(self) -> None:
        while True:
            note_id = self._queue.get()
            if note_id is None:
                return
            try:
                self.index_note(note_id)
            except Exception:
                log.exception("Embedding note %s failed", note_id)

    def index_note(self, note_id: str) -> None:
        from schrift.models import User
        from schrift.services.vault import Vault

        with self.db.session() as session:
            note = session.get(Note, note_id)
            if note is None:
                return
            session.execute(delete(Embedding).where(Embedding.note_id == note_id))
            if note.deleted_at is not None:
                return
            user = session.get(User, note.user_id)
            body = Vault(session, user, self.data_dir).body(note)
            texts = chunks(note.title, body)
            vectors = self.embed(texts)
            if vectors.size == 0:
                self._missed.add(note_id)
                return
            for i, (t, v) in enumerate(zip(texts, vectors, strict=True)):
                session.add(
                    Embedding(
                        note_id=note.id, user_id=note.user_id, chunk=i, text=t, vector=v.tobytes()
                    )
                )
            note.embedded_hash = note.content_hash

    # --- search -----------------------------------------------------------------------

    def search(self, session: Session, user_id: int, query: str, limit: int = 20):
        rows = session.execute(
            select(Embedding.note_id, Embedding.text, Embedding.vector).where(
                Embedding.user_id == user_id
            )
        ).all()
        if not rows or not query.strip():
            return []
        q = self.embed([query])
        if q.size == 0:
            return []
        matrix = np.frombuffer(b"".join(r.vector for r in rows), dtype=np.float32).reshape(
            len(rows), -1
        )
        scores = matrix @ q[0]
        best: dict[str, tuple[float, str]] = {}
        for (note_id, text, _), score in zip(rows, scores, strict=True):
            if score >= MIN_SCORE and score > best.get(note_id, (-1, ""))[0]:
                best[note_id] = (float(score), text)
        ordered = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
        notes = {
            n.id: n
            for n in session.scalars(
                select(Note).where(Note.id.in_([k for k, _ in ordered]), Note.deleted_at.is_(None))
            )
        }
        return [
            SearchResult(notes[k], html.escape(_excerpt(text)), score, "meaning")
            for k, (score, text) in ordered
            if k in notes
        ]


def _excerpt(text: str, length: int = 180) -> str:
    text = text.split("\n", 1)[-1].replace("\n", " ")
    return text[:length] + ("…" if len(text) > length else "")
