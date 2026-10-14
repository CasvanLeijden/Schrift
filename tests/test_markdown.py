from schrift import markdown as md


def test_frontmatter_roundtrip():
    text = md.join_frontmatter({"id": "X", "tags": ["a", "b"]}, "# Titel\n\nTekst\n")
    meta, body = md.split_frontmatter(text)
    assert meta == {"id": "X", "tags": ["a", "b"]}
    assert body == "# Titel\n\nTekst\n"


def test_invalid_frontmatter_is_body():
    meta, body = md.split_frontmatter("---\n: [\n---\nhoi")
    assert meta == {}
    assert body.endswith("hoi")


def test_parse_task_with_all_tokens():
    task = md.parse_task_line("  - [ ] Offerte sturen 📅 2026-10-20 9:00 🆔 k3f9x")
    assert task.title == "Offerte sturen"
    assert task.due == "2026-10-20 09:00"
    assert task.short_id == "k3f9x"
    assert not task.done
    assert task.indent == "  "


def test_parse_done_task_and_format_roundtrip():
    line = "- [x] Agenda delen ✅ 2026-10-14 🆔 p2m7q"
    task = md.parse_task_line(line)
    assert task.done and task.done_on == "2026-10-14"
    assert md.format_task_line(task) == line


def test_task_without_date():
    task = md.parse_task_line("- [ ] Klant X terugbellen")
    assert task.due is None and task.title == "Klant X terugbellen"


def test_plain_bullets_are_not_tasks():
    assert md.parse_tasks("- Jan: contract nalezen\n* gewoon") == []


def test_tasks_in_code_blocks_are_ignored():
    body = "```\n- [ ] niet echt\n```\n- [ ] echt"
    assert [t.title for t in md.parse_tasks(body)] == ["echt"]


def test_assign_ids_and_fix_duplicates():
    body = "- [ ] een 🆔 aaaaa\n- [ ] twee 🆔 aaaaa\n- [ ] drie"
    out = md.assign_task_ids(body, taken={"zzzzz"})
    ids = [t.short_id for t in md.parse_tasks(out)]
    assert ids[0] == "aaaaa"
    assert len(set(ids)) == 3 and all(ids)


def test_append_task_under_heading():
    body = "# T\n\n## Actiepunten\n\n- [ ] a 🆔 aaaaa\n\n## Vervolg\n\nx\n"
    task = md.ParsedTask(0, "", "-", False, "b", None, None, "bbbbb")
    out = md.append_task(body, task, under_heading="Actiepunten")
    assert "- [ ] a 🆔 aaaaa\n- [ ] b 🆔 bbbbb\n\n## Vervolg" in out


def test_append_task_to_new_section():
    task = md.ParsedTask(0, "", "-", False, "b", "2026-01-02", None, "bbbbb")
    out = md.append_task("# T\n", task, under_heading="Taken")
    assert out.endswith("## Taken\n\n- [ ] b 📅 2026-01-02 🆔 bbbbb\n")


def test_title_and_set_title():
    assert md.extract_title("```\n# nee\n```\n# Ja\n") == "Ja"
    assert md.set_title("tekst", "Nieuw").startswith("# Nieuw\n\ntekst")
    assert md.set_title("# Oud\nx", "Nieuw") == "# Nieuw\nx"


def test_wikilinks():
    assert md.parse_wikilinks("Zie [[Klant X]] en [[klant x|alias]] en [[Plan#kop]]") == [
        "Klant X",
        "Plan",
    ]


def test_plain_text_strips_markup():
    text = md.plain_text("# Kop\n- [ ] taak 📅 2026-10-20 🆔 abcde\n[[Link]] **vet**")
    assert "abcde" not in text and "Link" in text and "2026-10-20" in text


def test_ids():
    assert len(md.new_id()) == 26
    assert md.new_id() != md.new_id()
    assert md.safe_filename("a/b:c?") == "a-b-c-"
