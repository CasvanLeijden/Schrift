import pytest

from schrift.models import Task, TaskChange
from schrift.services.vault import ConflictError, VaultError


def test_create_note_writes_markdown_with_frontmatter(vault):
    zoka = vault.create_folder("ZOKA")
    note = vault.create_note("Overleg", "# Overleg\n\n- [ ] Offerte 📅 2026-10-20", folder=zoka)
    text = vault.file_path(note).read_text()
    assert note.path == "ZOKA/Overleg.md"
    assert text.startswith(f"---\nid: {note.id}\n")
    assert "- [ ] Offerte 📅 2026-10-20 🆔 " in text
    task = vault.tasks()[0]
    assert task.title == "Offerte" and task.due == "2026-10-20"


def test_title_change_renames_file(vault):
    note = vault.create_note("Eerst")
    old = vault.file_path(note)
    vault.save(note, "# Daarna\n\ntekst")
    assert note.title == "Daarna" and note.path == "Daarna.md"
    assert not old.exists() and vault.file_path(note).exists()


def test_duplicate_titles_get_unique_files(vault):
    a = vault.create_note("Zelfde")
    b = vault.create_note("Zelfde")
    assert a.path != b.path


def test_conflict_detection(vault):
    note = vault.create_note("N", "# N\n")
    base = note.content_hash
    vault.save(note, "# N\n\nandere wijziging", source="mcp")
    with pytest.raises(ConflictError):
        vault.save(note, "# N\n\nmijn wijziging", base_hash=base)


def test_tasks_sync_and_change_log(vault, session):
    note = vault.create_note("T", "# T\n\n- [ ] a\n- [ ] b")
    tasks = vault.tasks()
    assert {t.title for t in tasks} == {"a", "b"}
    a = next(t for t in tasks if t.title == "a")
    before = session.query(TaskChange).count()
    vault.update_task(a, done=True)
    assert a.done and a.done_on
    assert "- [x] a ✅" in vault.body(note)
    assert session.query(TaskChange).count() == before + 1
    vault.delete_task(a)
    assert session.query(Task).filter_by(short_id=a.short_id).first() is None
    assert session.query(TaskChange).order_by(TaskChange.id.desc()).first().deleted


def test_create_task_goes_to_inbox(vault):
    task = vault.create_task("Bellen", due="2026-11-01")
    assert task.note.is_inbox
    assert "- [ ] Bellen 📅 2026-11-01 🆔" in vault.body(task.note)


def test_folder_rename_and_move(vault):
    zoka = vault.create_folder("ZOKA")
    verslagen = vault.create_folder("Verslagen", zoka.id)
    note = vault.create_note("Verslag", folder=verslagen)
    vault.rename_folder(zoka, "Werk")
    assert note.path == "Werk/Verslagen/Verslag.md" and vault.file_path(note).exists()
    vault.move_folder(verslagen, None)
    assert note.path == "Verslagen/Verslag.md" and vault.file_path(note).exists()
    with pytest.raises(VaultError):
        vault.create_folder("Verslagen")


def test_move_note_and_ordering(vault):
    a_folder = vault.create_folder("A")
    n1 = vault.create_note("Een", folder=a_folder)
    n2 = vault.create_note("Twee", folder=a_folder)
    roots, _ = vault.tree()
    assert [n.title for n in roots[0].notes] == ["Twee", "Een"]  # newest on top
    vault.move_note(n1, a_folder, position=0)
    roots, _ = vault.tree()
    assert [n.title for n in roots[0].notes] == ["Een", "Twee"]
    vault.move_note(n2, None)
    assert n2.path == "Twee.md"


def test_trash_and_restore(vault):
    note = vault.create_note("Weg", "# Weg\n\n- [ ] taak")
    vault.delete_note(note)
    assert not vault.tasks() and vault.trash() == [note]
    vault.restore_note(note)
    assert vault.tasks() and vault.file_path(note).exists()


def test_versions_and_restore(vault):
    note = vault.create_note("V", "# V\n\neen", source="mcp")
    vault.save(note, "# V\n\ntwee", source="mcp")
    versions = vault.versions(note)
    assert [v.source for v in versions] == ["mcp", "mcp"]
    vault.restore_version(note, versions[-1].id)
    assert vault.body(note).endswith("een")


def test_web_versions_coalesce(vault):
    note = vault.create_note("W", "# W\n")
    vault.save(note, "# W\n\na")
    vault.save(note, "# W\n\nab")
    assert len(vault.versions(note)) == 1


def test_find_note_by_path_and_title(vault):
    f = vault.create_folder("Prive")
    note = vault.create_note("Boodschappen", folder=f)
    assert vault.find_note("Prive/Boodschappen") is note
    assert vault.find_note("boodschappen") is note
    assert vault.find_note(note.id) is note


def test_scan_imports_external_files(vault):
    (vault.notes_dir / "Extern").mkdir()
    (vault.notes_dir / "Extern" / "Los.md").write_text("# Los bestand\n\n- [ ] doe iets\n")
    assert vault.scan() == 1
    note = vault.find_note("Los bestand")
    assert note.folder.name == "Extern" and vault.tasks()[0].title == "doe iets"
    assert vault.scan() == 0


def test_renaming_updates_wikilinks(vault):
    target = vault.create_note("Klant X")
    source = vault.create_note("Overleg", "# Overleg\n\nZie [[Klant X]] en [[klant x|de klant]].")
    vault.save(target, "# Klant Xenia\n")
    assert vault.body(source) == "# Overleg\n\nZie [[Klant Xenia]] en [[Klant Xenia|de klant]]."
