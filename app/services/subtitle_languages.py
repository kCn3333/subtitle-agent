"""Normalize common subtitle language tags without guessing from dialogue."""
ALIASES = {
    "eng": ("en", "eng", "english"), "fra": ("fr", "fra", "fre", "french", "francais"),
    "deu": ("de", "deu", "ger", "german"), "spa": ("es", "spa", "spanish"),
    "ita": ("it", "ita", "italian"), "por": ("pt", "por", "portuguese"),
    "pol": ("pl", "pol", "polish"), "rus": ("ru", "rus", "russian"),
    "ukr": ("uk", "ukr", "ukrainian"), "ces": ("cs", "ces", "cze", "czech"),
    "nld": ("nl", "nld", "dut", "dutch"), "swe": ("sv", "swe", "swedish"),
    "dan": ("da", "dan", "danish"), "nor": ("no", "nor", "norwegian"),
    "fin": ("fi", "fin", "finnish"), "jpn": ("ja", "jpn", "japanese"),
    "kor": ("ko", "kor", "korean"), "zho": ("zh", "zho", "chi", "chinese"),
    "ara": ("ar", "ara", "arabic"), "tur": ("tr", "tur", "turkish"),
    "ell": ("el", "ell", "gre", "greek"), "hun": ("hu", "hun", "hungarian"),
    "ron": ("ro", "ron", "rum", "romanian"),
}


def normalize_language(value):
    value = str(value or "und").casefold().strip()
    return next((code for code, aliases in ALIASES.items() if value in aliases), value)


def reference_prefix(reference):
    # Keep existing English workpacks compatible; other languages use a neutral name.
    return "selected.eng" if normalize_language((reference or {}).get("language", "eng")) == "eng" else "selected.ref"
