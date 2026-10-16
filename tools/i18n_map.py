"""Pure mapping from universal-till's English strings + key metadata to the
files this repo holds (ADR-0160 §2, §3, §5). No I/O here: sync_english.py
reads the till, provision_weblate.py talks to Weblate.

Metadata fields (universal-till web/i18n-meta/en.meta.json, guarded by
scripts/ci/guard-i18n-meta.sh there):
  description  -> Weblate explanation
  placeholders -> appended to the explanation, one meaning per argument
  max_length   -> flag max-length:N
  review       -> label `legal` (also forced for legal namespaces, deny by
                  default, unless the key carries a reviewed review_exception)
  plural       -> flag icu-message-format (also when the English value holds
                  ICU plural/select/number syntax)
  area         -> which component the key lands in (else areas.json namespace
                  map, else default_area)
Placeholder tokens found in the English text -> flag placeholders:"..":"..".
"""

import json
import re
from dataclasses import dataclass, field

LEGAL_LABEL = "legal"

# The till's verb regex (guard-i18n.sh check 8 / guard-i18n-meta.sh): printf
# verbs incl. Go's indexed %[1]s, {{template}} and {0}. `%%` is a literal.
_VERB = (
    r"%%"
    r"|%\[(\d+)\]\d*(?:\.\d+)?[vTtbcdoOqxXUeEfFgGspw](?![A-Za-z])"
    r"|%\d*(?:\.\d+)?[vTtbcdoOqxXUeEfFgGspw](?![A-Za-z])"
    r"|\{\{[^{}]*\}\}"
    r"|\{\d+\}"
)
VERB_RE = re.compile(_VERB)
# Plus the MessageFormat simple argument {name}, which TM renders.
TOKEN_RE = re.compile(_VERB + r"|\{[A-Za-z_][A-Za-z0-9_]*\}")
ICU_RE = re.compile(r"\{\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*(?:plural|select|selectordinal|number)\b")

AREA_NAMES = {"selforder": "Self-order"}

# An area id becomes a folder in this repo and a Weblate component slug, and
# comes from another repo (universal-till's areas.json): lowercase words
# joined by hyphens, and never one of this repo's own top-level folders.
AREA_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RESERVED_AREAS = frozenset({"meta", "tools", "tests", "config", "scripts", ".github"})


def validate_area_id(area):
    """Raise ValueError unless `area` is safe as a path segment and a slug."""
    if not isinstance(area, str) or not AREA_ID_RE.fullmatch(area):
        raise ValueError(f"area id {area!r} must match ^[a-z0-9-]+$ (lowercase words joined by '-')")
    if area in RESERVED_AREAS:
        raise ValueError(f"area id {area!r} is reserved (a top-level folder of this repo)")
    return area


def is_icu(text, entry):
    """A MessageFormat key with plural/select/number, by metadata or by text."""
    return bool((entry or {}).get("plural")) or bool(ICU_RE.search(text))


def placeholder_tokens(text, icu=False):
    """Distinct placeholder tokens in order of first use. For an ICU key the
    brace syntax is checked by Weblate's ICU check instead, so only printf
    verbs are listed."""
    out = []
    for m in TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok == "%%" or (icu and tok.startswith("{")):
            continue
        if tok not in out:
            out.append(tok)
    return out


def argument_tokens(text):
    """One token per argument, the way guard-i18n-meta.sh counts them, so the
    metadata's `placeholders` meanings pair up with them in order."""
    out, seen = [], set()
    for m in VERB_RE.finditer(text):
        tok = m.group(0)
        if tok == "%%":
            continue
        if m.group(1):
            if m.group(1) in seen:
                continue
            seen.add(m.group(1))
        out.append(tok)
    return out


def _quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_flags(flags):
    """[(name, [values])] -> Weblate's flag string. Numbers stay bare,
    everything else is quoted so `:` or `,` inside a token can't split it."""
    parts = []
    for name, values in flags:
        vals = [v if re.fullmatch(r"\d+", v) else _quote(v) for v in values]
        parts.append(":".join([name, *vals]))
    return ", ".join(parts)


def unit_metadata(key, english, entry, legal_namespaces, lines=None):
    entry = entry or {}
    icu = is_icu(english, entry)
    explanation = (entry.get("description") or "").strip()
    meanings = entry.get("placeholders") or []
    if meanings:
        args = argument_tokens(english)
        rows = []
        for i, meaning in enumerate(meanings):
            tok = args[i] if len(args) == len(meanings) else str(i + 1)
            rows.append(f"- {tok}: {meaning}")
        explanation += "\n\nPlaceholders:\n" + "\n".join(rows)
    if lines and "main" not in lines:
        note = (f"Only used by till line {', '.join(lines)}; kept while that line is supported "
                "(ADR-0160 §5).")
        explanation = f"{explanation}\n\n{note}" if explanation else note

    flags = []
    if isinstance(entry.get("max_length"), int):
        flags.append(("max-length", [str(entry["max_length"])]))
    toks = placeholder_tokens(english, icu=icu)
    if toks:
        flags.append(("placeholders", toks))
    if icu:
        flags.append(("icu-message-format", []))

    ns = key.split(".")[0]
    review = entry.get("review")
    legal = review == "legal" or (
        ns in legal_namespaces and not (review == "standard" and entry.get("review_exception"))
    )
    return {
        "explanation": explanation.strip(),
        "flags": format_flags(flags),
        "labels": [LEGAL_LABEL] if legal else [],
    }


@dataclass
class Line:
    name: str  # "main" or a release line such as "0.34"
    en: dict
    meta: dict = field(default_factory=dict)


@dataclass
class Entry:
    english: str
    meta: dict
    lines: list


def build_union(lines):
    """Union of keys over `lines` (main first, then newest line first). The
    first line holding a key gives its English and its metadata."""
    union = {}
    for line in lines:
        for key, text in line.en.items():
            if not isinstance(text, str):
                continue
            e = union.get(key)
            if e is None:
                e = union[key] = Entry(text, None, [])
            e.lines.append(line.name)
            if e.meta is None and key in line.meta:
                e.meta = line.meta[key]
    for e in union.values():
        if e.meta is None:
            e.meta = {}
    return union


def namespace_index(areas_doc):
    idx = {}
    for area, spec in areas_doc.get("areas", {}).items():
        for ns in spec.get("namespaces", []):
            idx.setdefault(ns, area)
    return idx


def area_for(key, entry, ns_index, default_area, known_areas=None):
    explicit = (entry or {}).get("area")
    if explicit and (known_areas is None or explicit in known_areas):
        return explicit
    return ns_index.get(key.split(".")[0], default_area)


def area_name(area):
    return AREA_NAMES.get(area) or area.replace("-", " ").capitalize()


def dumps(obj, sort=True):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=sort) + "\n"


def render(union, areas_doc):
    """-> {relative path: file text}. Every defined area gets its en.json and
    meta file, even when empty, so its component always has a template."""
    areas = areas_doc.get("areas", {})
    default = areas_doc.get("default_area", "common")
    for area in [*areas, default]:
        validate_area_id(area)
    legal_ns = areas_doc.get("legal_namespaces", [])
    idx = namespace_index(areas_doc)
    en_by_area = {a: {} for a in areas}
    meta_by_area = {a: {} for a in areas}
    for key, e in union.items():
        area = area_for(key, e.meta, idx, default, areas)
        en_by_area.setdefault(area, {})[key] = e.english
        meta_by_area.setdefault(area, {})[key] = {
            **unit_metadata(key, e.english, e.meta, legal_ns, e.lines),
            "lines": e.lines,
        }
    files = {}
    summary = {}
    for area in en_by_area:
        files[f"{area}/en.json"] = dumps(en_by_area[area])
        files[f"meta/{area}.json"] = dumps(meta_by_area[area])
        summary[area] = {
            "name": area_name(area),
            "description": areas.get(area, {}).get("description", ""),
            "keys": len(en_by_area[area]),
        }
    files["meta/areas.json"] = dumps(summary, sort=False)
    return files
