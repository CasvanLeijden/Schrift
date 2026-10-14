"""UI translations. Dutch strings are the keys; English lives in `EN`."""

from schrift.i18n_en import EN

LANGUAGES = {"nl": "Nederlands", "en": "English"}


def translate(lang: str, text: str, **kwargs) -> str:
    out = EN.get(text, text) if lang == "en" else text
    return out.format(**kwargs) if kwargs else out


def translator(lang: str):
    def t(text: str, **kwargs) -> str:
        return translate(lang, text, **kwargs)

    return t
