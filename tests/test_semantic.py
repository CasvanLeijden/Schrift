import numpy as np

from schrift.services.semantic import SemanticIndex


class FakeModel:
    """Maps each text to a fixed vector by its last line, so scores are predictable."""

    vectors = {
        "brood": [1.0, 0.0, 0.0],
        "Brood bakken": [0.95, 0.31, 0.0],
        "Italiaans brood": [0.9, 0.0, 0.43],
        "Recept": [0.55, 0.0, 0.83],
    }

    def embed(self, texts):
        return [np.array(self.vectors[t.split("\n")[-1]], dtype=np.float32) for t in texts]


def test_meaning_search_keeps_only_close_matches(ctx, vault):
    index = SemanticIndex(ctx.db, ctx.settings.data_dir, "fake")
    index._model = FakeModel()
    for title, text in (
        ("Brood", "Brood bakken"),
        ("Ciabatta", "Italiaans brood"),
        ("Pasta", "Recept"),
    ):
        note = vault.create_note(title, f"# {title}\n\n{text}")
        vault.session.commit()
        index.index_note(note.id)
    results = index.search(vault.session, vault.user.id, "brood")
    assert [r.note.title for r in results] == ["Brood", "Ciabatta"]
