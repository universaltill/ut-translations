"""Per-string publish checks for a language bundle (ADR-0160 §4, §5;
ut-docs#4367). Pure functions, no I/O.

A string that fails one of these is omitted from the bundle, never written
as English: the till then falls through to its built-in string or English.

  placeholders  the till's own load-time rule (internal/config/i18n.go
                samePlaceholders: the same multiset of printf verbs, `%%`
                stripped) AND the same multiset of every placeholder token
                i18n_map knows ({{.X}}, {0}, {name}; ICU argument names for
                an ICU key)
  max_length    the key's `max-length:N` flag, counted in characters
  script        the target is written in the locale's script
  dropped       markup tags, link targets and URLs in the English are kept
  empty         a blank translation would render blank on the till
"""

import re
import unicodedata
from collections import Counter

import i18n_map

# universal-till internal/config/i18n.go printfVerb, verbatim. The space flag
# is left out there on purpose ("19%, 7% or 0% of" is prose).
TILL_PRINTF_VERB = re.compile(r"%(\[\d+\])?[-+#0]*(\d+|\*)?(\.(\d+|\*))?[a-zA-Z]")
ICU_ARG_RE = re.compile(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*[,}]")
TAG_RE = re.compile(r"</?\s*([A-Za-z][A-Za-z0-9-]*)[^<>]*?/?>")
LINK_ATTR_RE = re.compile(r"""\b(?:href|src|action)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
# A URL needs a host after the scheme ("http:// or https://," in prose is not
# one) and ends before trailing punctuation.
URL_RE = re.compile(r"\b(?:https?://|mailto:)[A-Za-z0-9][^\s\"'<>)]*(?<![.,;:!?])", re.I)
MAX_LENGTH_RE = re.compile(r"(?:^|,)\s*max-length:(\d+)\s*(?:,|$)")

# Unicode script of a letter, by the first word of its character name — the
# standard library has no script property. Good enough to tell the scripts
# the till ships apart; CJK ideographs and kana are grouped by locale below.
_SCRIPT_WORDS = {
    "LATIN": "Latn", "CYRILLIC": "Cyrl", "GREEK": "Grek", "ARABIC": "Arab",
    "HEBREW": "Hebr", "DEVANAGARI": "Deva", "BENGALI": "Beng", "THAI": "Thai",
    "HANGUL": "Hang", "CJK": "Hani", "HIRAGANA": "Hira", "KATAKANA": "Kana",
    "GEORGIAN": "Geor", "ARMENIAN": "Armn", "TAMIL": "Taml", "GUJARATI": "Gujr",
    "GURMUKHI": "Guru", "KANNADA": "Knda", "MALAYALAM": "Mlym", "TELUGU": "Telu",
    "SINHALA": "Sinh", "KHMER": "Khmr", "LAO": "Laoo", "MYANMAR": "Mymr",
    "ETHIOPIC": "Ethi",
}
# A locale's script may legitimately be written with these too.
SCRIPT_FAMILIES = {
    "Jpan": {"Hani", "Hira", "Kana"},
    "Kore": {"Hang", "Hani"},
    "Hans": {"Hani"},
    "Hant": {"Hani"},
}


def letter_script(ch):
    if not ch.isalpha():
        return None
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    first = name.split(" ", 1)[0]
    if first == "FULLWIDTH" and "LATIN" in name:
        return "Latn"
    return _SCRIPT_WORDS.get(first, "other")


def strip_placeholders(text):
    """Text without placeholder tokens, tags, URLs: what a reader sees."""
    text = URL_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = i18n_map.TOKEN_RE.sub(" ", text)
    text = re.sub(r"\{[^{}]*\}", " ", text) if "{" in text else text
    return text


def till_verbs(text):
    return sorted(m.group(0) for m in TILL_PRINTF_VERB.finditer(text.replace("%%", "")))


def tokens(text, icu):
    out = Counter()
    for m in i18n_map.TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok == "%%" or (icu and tok.startswith("{")):
            continue
        out[tok] += 1
    if icu:
        out.update("icu:" + n for n in set(ICU_ARG_RE.findall(text)))
    return out


def max_length(flags):
    m = MAX_LENGTH_RE.search(flags or "")
    return int(m.group(1)) if m else None


def kept_items(text):
    """Markup and link targets a translation must keep."""
    items = Counter("tag:" + t.lower() + (":/" if s.startswith("</") else "")
                    for s, t in ((m.group(0), m.group(1)) for m in TAG_RE.finditer(text)))
    for m in LINK_ATTR_RE.finditer(text):
        items["link:" + (m.group(1) if m.group(1) is not None else m.group(2))] += 1
    for m in URL_RE.finditer(text):
        items["url:" + m.group(0)] += 1
    return items


def scripts_in(text):
    seen = Counter()
    for ch in strip_placeholders(text):
        s = letter_script(ch)
        if s:
            seen[s] += 1
    return seen


def check_script(target, script, english=""):
    """None, or why the target is not written in `script`. Letters of a
    script the English itself uses are fine ("Greek (Windows-1253 — α/β)")."""
    allowed = SCRIPT_FAMILIES.get(script, {script}) | (set(scripts_in(english)) - {"Latn"})
    seen = scripts_in(target)
    if not seen:
        return None  # digits/punctuation only: nothing to check
    # "other": a letter of no script we know (º, ª…) is neutral.
    foreign = {s for s in seen if s not in allowed and s not in ("Latn", "other")}
    if foreign:
        return f"letters in {', '.join(sorted(foreign))}, expected {script}"
    if "Latn" not in allowed and not any(s in allowed for s in seen):
        return f"no letters in {script} (looks untranslated)"
    return None


def check_string(english, target, meta, script):
    """-> None when the string may ship, else the omission reason
    ('empty', 'placeholders', 'max_length', 'script', 'dropped') and detail."""
    if not isinstance(target, str) or not target.strip():
        return "empty", "blank translation"
    icu = i18n_map.is_icu(english, {"plural": "icu-message-format" in (meta.get("flags") or "")})
    if till_verbs(english) != till_verbs(target):
        return "placeholders", f"printf verbs {till_verbs(target)} != English {till_verbs(english)}"
    if tokens(english, icu) != tokens(target, icu):
        return "placeholders", f"placeholders {sorted(tokens(target, icu).elements())} != English {sorted(tokens(english, icu).elements())}"
    limit = max_length(meta.get("flags"))
    if limit is not None and len(target) > limit:
        return "max_length", f"{len(target)} characters > max-length {limit}"
    # A string kept exactly as English (EAN-13, a product name) was a
    # reviewer's deliberate choice and renders as the fallback would.
    why = None if target == english else check_script(target, script, english)
    if why:
        return "script", why
    missing = kept_items(english) - kept_items(target)
    if missing:
        return "dropped", f"missing {sorted(missing)}"
    return None


def scan_line(text):
    """One-line normalized view of a string for the compliance and naming
    guards: they match literal substrings line by line, so newlines, tags,
    non-breaking or doubled spaces and Unicode dashes would hide a term
    (their own documented gaps). Folded here, so a bundle closes them."""
    text = unicodedata.normalize("NFKC", text)
    text = TAG_RE.sub("", text)
    text = re.sub(r"[‐-―−­]", "-", text)
    return re.sub(r"\s+", " ", text).strip()
