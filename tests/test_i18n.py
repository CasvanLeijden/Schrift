import re
from pathlib import Path

from schrift.i18n import translate
from schrift.i18n_en import EN

ROOT = Path(__file__).resolve().parent.parent / "schrift"


def ui_strings() -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*.html"):
        text = path.read_text()
        found |= set(re.findall(r"""\bt\(\s*"((?:[^"\\]|\\.)*)"\s*[,)]""", text))
        found |= set(re.findall(r"""\bt\(\s*'((?:[^'\\]|\\.)*)'\s*[,)]""", text))
    for path in ROOT.rglob("*.py"):
        text = path.read_text()
        found |= set(re.findall(r"""translate\([^,()]+,\s*"((?:[^"\\]|\\.)*)"\s*[,)]""", text))
        found |= set(re.findall(r"""\bt\("((?:[^"\\]|\\.)*)"\)""", text))
    return found


def test_every_ui_string_has_an_english_translation():
    missing = sorted(s for s in ui_strings() if s not in EN)
    assert not missing, f"Missing English translations: {missing}"


def test_translate_with_arguments():
    assert translate("en", "Nieuwe notitie in {name}", name="ZOKA") == "New note in ZOKA"
    assert translate("nl", "Nieuwe notitie in {name}", name="ZOKA") == "Nieuwe notitie in ZOKA"
    assert translate("en", "onbekend") == "onbekend"
