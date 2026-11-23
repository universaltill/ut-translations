"""Deterministic pre-checks for the AI reviewer runner (ADR-0160 §4, §10;
ut-docs#5420). Pure functions except run_scans, which runs universal-till's
real compliance and naming guards.

A unit that fails any of these is `needs-fix` before a model is asked; a
model can never approve it. Each check returns None (pass) or a detail.

  placeholders, max_length, script, dropped, empty
                bundle_checks.check_string, per form (the publish gate's own
                rules, so a reviewed string also ships)
  icu_forms     ICU plural/select: same arguments and kinds as the English,
                select branches equal, one plural branch per CLDR category of
                the target language (=N exact branches allowed), no empty
                branch, balanced braces
  plural_forms  a Weblate plural unit: the language's number of forms, none
                empty, each form's placeholders match an English form
  rtl           ar/fa: no left-to-right control marks, no Latin words except
                brand terms, placeholders and names copied from the English
  length_ratio  far longer than the English (over 2.5x and 15 characters)
  brand         brand and technical terms stay as written (Universal Till,
                Bluetooth, Wi-Fi, PIN, USB, QR, SKU, EAN, Code 128, Local
                Plus...)
  untranslated  the English copied, unless the string is only brand terms or
                codes, or the glossary lists it as the same in that language
  glossary      config/review-glossary.json per-language terms: a source
                term needs one of its accepted targets and none of the
                forbidden ones
  scans         ADR-0040 compliance wording, ADR-0106 competitor naming

needs_human() decides `needs-human` (never approved by an AI): legal keys,
the receipts-and-legal component, and tax/fiscal/receipt/invoice/money
wording by key segment or English word.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_bundle  # noqa: E402
import bulk_locale  # noqa: E402
import bundle_checks  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLOSSARY_PATH = os.path.join(REPO, "config", "review-glossary.json")

BRAND_TERMS = tuple(sorted({"Universal Till", "Bluetooth", "Wi-Fi", "PIN", "USB", "QR", "SKU", "EAN",
                            "Code 128", "Local Plus"} | set(bulk_locale.KEEP_NAMES), key=len, reverse=True))
RTL_LANGS = {"ar", "fa", "he", "ur"}
LTR_MARKS = re.compile("[‎‪‭⁦]")
SCRIPTS = {"ar": "Arab", "fa": "Arab", "el": "Grek", "zh_Hans": "Hans", "zh_Hant": "Hant", "ja": "Jpan", "ko": "Kore"}

# CLDR cardinal plural categories: (required, also allowed). The Romance
# languages' `many` (compact large numbers) is accepted, not required.
PLURAL_CATEGORIES = {
    "ar": ({"zero", "one", "two", "few", "many", "other"}, set()),
    "fa": ({"one", "other"}, set()),
    "tr": ({"one", "other"}, set()),
    "de": ({"one", "other"}, set()),
    "en": ({"one", "other"}, set()),
    "fr": ({"one", "other"}, {"many"}),
    "es": ({"one", "other"}, {"many"}),
    "it": ({"one", "other"}, {"many"}),
    "pt": ({"one", "other"}, {"many"}),
    "ms": ({"other"}, set()),
    "el": ({"one", "other"}, set()),
    "zh_Hans": ({"other"}, set()),
}
ICU_KINDS = ("plural", "select", "selectordinal")
ICU_HEAD = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(plural|selectordinal|select)\s*,", re.S)

HUMAN_WORDS = {"fiscal", "tax", "taxes", "taxable", "vat", "invoice", "invoices", "receipt", "receipts", "tse",
               "legal", "terms", "privacy", "price", "prices", "pricing", "amount", "amounts", "currency",
               "currencies", "money", "cost", "costs", "billing", "impressum", "cookie", "cookies", "consent",
               "gdpr", "licence", "license"}
MONEY_SYMBOL = re.compile(r"[£€$¥₺]\s?\d|\d\s?[£€$¥₺]")
HUMAN_COMPONENTS = {"receipts-and-legal"}

LENGTH_RATIO, LENGTH_SLACK = 2.5, 15

CHECK_ORDER = ("empty", "placeholders", "max_length", "script", "dropped", "icu_forms", "plural_forms", "rtl",
               "length_ratio", "brand", "untranslated", "glossary")


def family(model):
    """Model family: claude for anthropic/claude*, openai for codex/gpt*,
    gemini for gemini*/google*.
    Two accounts in the same family never review each other."""
    m = (model or "").strip().lower().split("/")[0]
    if m.startswith(("anthropic", "claude")):
        return "claude"
    if m.startswith(("openai", "codex", "gpt")):
        return "openai"
    if m.startswith(("gemini", "google")):
        return "gemini"
    return m


def script_for(lang, repo=REPO):
    try:
        with open(os.path.join(repo, "config", "bundles.json"), encoding="utf-8") as f:
            for spec in json.load(f).get("locales", {}).values():
                if spec.get("weblate") == lang and spec.get("script"):
                    return spec["script"]
    except (OSError, ValueError):
        pass
    return SCRIPTS.get(lang, "Latn")


def load_glossary(path=GLOSSARY_PATH):
    with open(path, encoding="utf-8") as f:
        g = json.load(f)
    if not isinstance(g, dict):
        raise ValueError(f"{path}: not a JSON object")
    return g


# --- ICU -------------------------------------------------------------------

def _match_brace(text, i):
    """Index of the } closing the { at i; ValueError when unbalanced."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("unbalanced braces")


def parse_icu(text):
    """[(argument, kind, {selector: branch text})], nested ones included,
    in order of appearance."""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "}":
            raise ValueError("unbalanced braces")
        if text[i] != "{":
            i += 1
            continue
        j = _match_brace(text, i)
        inner = text[i + 1:j]
        m = ICU_HEAD.match(inner)
        if m:
            branches, rest, k = {}, inner[m.end():], 0
            while k < len(rest):
                if rest[k].isspace():
                    k += 1
                    continue
                sel = re.match(r"(offset:\s*\d+|[^\s{}]+)", rest[k:])
                if not sel:
                    raise ValueError("bad ICU branch")
                k += sel.end()
                if sel.group(1).startswith("offset:"):
                    continue
                while k < len(rest) and rest[k].isspace():
                    k += 1
                if k >= len(rest) or rest[k] != "{":
                    raise ValueError(f"ICU selector {sel.group(1)!r} has no branch")
                e = _match_brace(rest, k)
                branches[sel.group(1)] = rest[k + 1:e]
                k = e + 1
            out.append((m.group(1), m.group(2), branches))
            for body in branches.values():
                out.extend(parse_icu(body))
        i = j + 1
    return out


def check_icu(english, target, lang):
    try:
        src = parse_icu(english)
    except ValueError:
        return None  # the English itself is checked upstream
    if not src:
        return None
    try:
        tgt = parse_icu(target)
    except ValueError as e:
        return str(e)
    if Counter((a, k) for a, k, _ in src) != Counter((a, k) for a, k, _ in tgt):
        return (f"ICU arguments {sorted((a, k) for a, k, _ in tgt)} != English "
                f"{sorted((a, k) for a, k, _ in src)}")
    src_by = {}
    for a, k, b in src:
        src_by.setdefault((a, k), b)
    required, optional = PLURAL_CATEGORIES.get(lang, ({"other"}, None))
    for a, k, branches in tgt:
        empty = [s for s, body in branches.items() if not body.strip()]
        if empty:
            return f"{a}: empty branch {', '.join(empty)}"
        keys = {s for s in branches if not s.startswith("=")}
        if k == "select":
            want = {s for s in src_by[(a, k)] if not s.startswith("=")}
            if keys != want:
                return f"{a}: select branches {sorted(keys)} != English {sorted(want)}"
        elif k == "plural":
            missing = sorted(required - keys)
            if missing:
                return f"{a}: plural branches missing {', '.join(missing)} for {lang}"
            if optional is not None:
                extra = sorted(keys - required - optional)
                if extra:
                    return f"{a}: plural branches {', '.join(extra)} are not {lang} categories"
        elif "other" not in keys:
            return f"{a}: no other branch"
    return None


# --- the other checks -----------------------------------------------------------

def _brand_re(term):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z])")


def brands_in(text):
    found, rest = [], text
    for term in BRAND_TERMS:
        if _brand_re(term).search(rest):
            found.append(term)
            rest = _brand_re(term).sub(" ", rest)
    return found


def strip_brands(text):
    for term in BRAND_TERMS:
        text = _brand_re(term).sub(" ", text)
    return text


def check_plural_forms(source, target, plural_number, meta, script):
    if plural_number and len(target) != plural_number:
        return f"{len(target)} forms, the language has {plural_number}"
    for n, form in enumerate(target):
        if not form.strip():
            return f"form {n} is empty"
        if all(bundle_checks.check_string(s, form, meta, script) for s in source):
            return f"form {n}: " + ": ".join(bundle_checks.check_string(source[-1], form, meta, script))
    return None


def check_rtl(english, target):
    if LTR_MARKS.search(target):
        return "left-to-right control mark in a right-to-left string"
    names = {w for w in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", english) if w[0].isupper() or any(c.isdigit() for c in w)}
    rest = strip_brands(bundle_checks.strip_placeholders(target))
    stray = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", rest) if w not in names]
    if stray:
        return f"Latin words in a right-to-left string: {', '.join(stray[:5])}"
    return None


def check_length(english, target):
    if len(target) > max(LENGTH_RATIO * len(english), len(english) + LENGTH_SLACK):
        return f"{len(target)} characters for {len(english)} in English"
    return None


def check_brand(english, target):
    missing = [t for t in brands_in(english) if t not in target]
    return f"brand/technical term not kept: {', '.join(missing)}" if missing else None


def check_untranslated(source, target, lang, glossary):
    if list(source) != list(target):
        return None
    eng = source[0] if source else ""
    if eng.strip() in bulk_locale.KEEP_NAMES or eng.strip() in set((glossary.get("same_as_source") or {}).get(lang, [])):
        return None
    letters = re.findall(r"[^\W\d_]", strip_brands(bundle_checks.strip_placeholders(eng)))
    if not letters:
        return None  # only brand terms, codes or placeholders
    return "the English text is copied unchanged"


def check_glossary(english, target, lang, glossary):
    low_e, low_t = english.lower(), target.lower()
    for entry in (glossary.get("terms") or {}).get(lang, []):
        term = (entry.get("source") or "").lower()
        if not term or not re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", low_e):
            continue
        bad = [f for f in entry.get("forbid") or [] if f.lower() in low_t]
        if bad:
            return f"'{entry['source']}' must not be translated as {', '.join(bad)}"
        accepted = entry.get("target") or []
        if accepted and not any(a.lower() in low_t for a in accepted):
            return f"'{entry['source']}' should be {' / '.join(accepted)}"
    return None


def check_unit(unit, lang, meta=None, glossary=None, plural_number=None, script=None):
    """-> OrderedDict code -> "pass" | detail, every check in CHECK_ORDER."""
    meta = dict(meta or {})
    if unit.get("flags") and not meta.get("flags"):
        meta["flags"] = unit["flags"]
    glossary = glossary or {}
    script = script or script_for(lang)
    source, target = list(unit.get("source") or [""]), list(unit.get("target") or [""])
    res = OrderedDict((c, None) for c in CHECK_ORDER)
    plural = len(source) > 1 or len(target) > 1
    if plural:
        res["plural_forms"] = check_plural_forms(source, target, plural_number, meta, script)
    else:
        bad = bundle_checks.check_string(source[0], target[0], meta, script)
        if bad:
            res[bad[0]] = bad[1]
        res["icu_forms"] = check_icu(source[0], target[0], lang)
    eng_all = "\n".join(source)
    for form in target:
        if lang in RTL_LANGS and res["rtl"] is None:
            res["rtl"] = check_rtl(eng_all, form)
        if res["length_ratio"] is None:
            res["length_ratio"] = check_length(max(source, key=len), form)
        if res["brand"] is None:
            res["brand"] = check_brand(source[min(target.index(form), len(source) - 1)], form)
        if res["glossary"] is None:
            res["glossary"] = check_glossary(source[min(target.index(form), len(source) - 1)], form, lang, glossary)
    res["untranslated"] = check_untranslated(source, target, lang, glossary)
    return OrderedDict((k, "pass" if v is None else v) for k, v in res.items())


def failed(checks):
    return [k for k, v in checks.items() if v not in ("pass", "pending", "skipped")]


# --- needs-human ----------------------------------------------------------------

def needs_human(key, component, meta_entry, labels, english):
    """A reason this unit needs a human reviewer, or None."""
    if component in HUMAN_COMPONENTS:
        return f"component {component} (legal)"
    if "legal" in ((meta_entry or {}).get("labels") or []):
        return "legal key (meta label)"
    if "legal" in set(labels or ()):
        return "legal key (Weblate label)"
    segs = {s for s in re.split(r"[^a-z0-9]+", (key or "").lower()) if s}
    hit = sorted(segs & HUMAN_WORDS)
    if hit:
        return f"tax/fiscal/money key ({hit[0]})"
    if MONEY_SYMBOL.search(english or ""):
        return "tax/fiscal/money wording (amount)"
    words = set(re.findall(r"[a-z]+", (english or "").lower()))
    hit = sorted(words & HUMAN_WORDS)
    if hit:
        return f"tax/fiscal/money wording ({hit[0]})"
    return None


# --- terminology hints (for the model, never a hard fail) ------------------------

def _short(text):
    return 0 < len(text.split()) <= 3 and len(text) <= 40


def term_index(approved_units):
    """English short label -> Counter of approved translations."""
    idx = {}
    for u in approved_units:
        src, tgt = list(u.get("source") or []), list(u.get("target") or [])
        if len(src) == 1 and len(tgt) == 1 and _short(src[0]) and tgt[0].strip():
            idx.setdefault(src[0].strip(), Counter())[tgt[0].strip()] += 1
    return idx


def terminology_hints(unit, idx):
    src, tgt = list(unit.get("source") or []), list(unit.get("target") or [])
    if len(src) != 1 or len(tgt) != 1:
        return []
    seen = idx.get(src[0].strip())
    if not seen:
        return []
    best, n = seen.most_common(1)[0]
    if best == tgt[0].strip():
        return []
    return [f"'{src[0].strip()}' is translated as '{best}' in {n} approved string(s)"]


# --- ADR-0040 / ADR-0106 scans ----------------------------------------------------

def run_scans(till, commit, entries):
    """universal-till's compliance and naming guards at `commit` over one
    line per (unit id, text). -> {unit id: which guard hit}. A guard that
    fails without naming a line raises build_bundle.BundleError."""
    build_bundle.require_till(till)
    build_bundle.require_released(till, commit)
    entries = list(entries)
    with tempfile.TemporaryDirectory(prefix="utt-review-scan-") as tmp:
        for rel in build_bundle.GUARDS:
            r = subprocess.run(["git", "-C", till, "show", f"{commit}:{rel}"], env=build_bundle.scrubbed_env(tmp),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode != 0:
                raise build_bundle.BundleError(f"can't read {rel} at {commit}: "
                                               f"{r.stderr.decode(errors='replace').strip()}")
            dest = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(r.stdout)
        dirs = {d: os.path.join(tmp, "scan", d) for d in ("locales", "help", "ui", "plugins")}
        for d in dirs.values():
            os.makedirs(d)
        line_unit = {}
        with open(os.path.join(dirs["locales"], "lines.json"), "w", encoding="utf-8") as f:
            for n, (uid, text) in enumerate(entries, 1):
                f.write(f"{uid}: {bundle_checks.scan_line(text)}\n")
                line_unit[n] = uid
        open(os.path.join(dirs["help"], "none.md"), "w").close()
        open(os.path.join(dirs["ui"], "none.html"), "w").close()
        os.makedirs(os.path.join(dirs["plugins"], "review"))
        with open(os.path.join(dirs["plugins"], "review", "plugin.json"), "w", encoding="utf-8") as f:
            f.write('{"canonical_type": "language"}\n')
        hits = {}
        for rel in build_bundle.GUARDS:
            args = [dirs["locales"], dirs["help"], dirs["ui"]]
            name = "competitor naming (ADR-0106)" if "competitor" in rel else "compliance wording (ADR-0040)"
            if "competitor" in rel:
                args.append(dirs["plugins"])
            r = subprocess.run(["bash", os.path.join(tmp, rel), *args], env=build_bundle.scrubbed_env(tmp),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if r.returncode == 0:
                continue
            out = r.stdout.decode(errors="replace")
            lines = [int(m.group(1)) for m in re.finditer(r"lines\.json:(\d+)", out)]
            if not lines:
                raise build_bundle.BundleError(f"{os.path.basename(rel)} failed without naming a string:\n"
                                               + out.replace(tmp + "/", "").strip()[:2000])
            for n in lines:
                uid = line_unit.get(n)
                if uid is None:
                    continue
                if uid not in hits:
                    hits[uid] = name
                elif name not in hits[uid]:
                    hits[uid] += "; " + name
    return hits


__all__ = ["check_unit", "failed", "needs_human", "run_scans", "family", "term_index", "terminology_hints",
           "load_glossary", "parse_icu", "check_icu", "script_for", "BRAND_TERMS"]
