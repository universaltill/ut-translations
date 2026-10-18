#!/usr/bin/env python3
"""One-off import of the translations we already have into this repo's
area files, so Weblate starts pre-filled (ADR-0160 §9 steps 1 and 4;
ut-docs#4371, ut-docs#4372). Never translates or invents text.

Reads (never writes), at a git ref (default origin/main):
  de, es, pt   ut-plugin-language-<code>  locales/<code>.json
               + i18n-baseline/<code>.same-as-en.txt (the pack's reviewed
                 list of keys that are legitimately the same as English)
  tr, ar, fa   universal-till             web/locales/<code>.json
and writes, for every locale in config/bundles.json except the source and
every area that has an <area>/en.json here:
  <area>/<code>.json   flat key -> text, same format as <area>/en.json
                       (tools/i18n_map.dumps: sorted keys, 2-space indent,
                       UTF-8, trailing newline)

Rules:
- a key lands in the area whose en.json holds it; a key in no en.json is
  stale and dropped (reported);
- text is copied byte for byte: placeholders are never rewritten; a
  placeholder set that differs from English is imported as is and reported
  (Weblate's enforced placeholders check and the bundle check catch it);
- empty or non-string values are skipped;
- a value byte-identical to English is skipped unless the source's
  same-as-en list names the key. Weblate counts any non-empty target as
  translated (identical text only raises the `same` check), so an English
  copy would show as done; core's tr/ar/fa have no such list and do hold
  English copies, so for them every identical value is skipped and stays
  untranslated in Weblate;
- an area with nothing for a locale still gets `{}`, so the language shows
  in every component.

Weblate owns <area>/<code>.json once it exists: a file that exists and
differs from what this would write is refused (nothing is written at all)
unless --force. A re-run over its own output changes nothing.

Usage:
  python3 tools/import_existing.py [--repo DIR] [--workspace DIR] [--ref REF]
                                   [--locale CODE ...] [--dry-run] [--force]
                                   [--report FILE]
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundle_checks  # noqa: E402
import i18n_map  # noqa: E402

BUNDLES_PATH = "config/bundles.json"
AREAS_PATH = "meta/areas.json"

# locale -> (sibling repo, locale file, same-as-English list or None)
SOURCES = {
    "de": ("ut-plugin-language-de", "locales/de.json", "i18n-baseline/de.same-as-en.txt"),
    "es": ("ut-plugin-language-es", "locales/es.json", "i18n-baseline/es.same-as-en.txt"),
    "pt": ("ut-plugin-language-pt", "locales/pt.json", "i18n-baseline/pt.same-as-en.txt"),
    "tr": ("universal-till", "web/locales/tr.json", None),
    "ar": ("universal-till", "web/locales/ar.json", None),
    "fa": ("universal-till", "web/locales/fa.json", None),
}


class ImportFailed(Exception):
    pass


def git_show(repo, ref, path):
    """File text at ref, or None when the path doesn't exist there."""
    r = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"], capture_output=True, text=True)
    if r.returncode != 0:
        if "does not exist" in r.stderr or "exists on disk, but not in" in r.stderr:
            return None
        raise ImportFailed(f"git -C {repo} show {ref}:{path} failed: {r.stderr.strip()}")
    return r.stdout


def flatten(obj, prefix=""):
    """Nested {"a": {"b": "x"}} -> {"a.b": "x"}; flat input is unchanged."""
    out = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def parse_key_list(text):
    """One key per line; `#` comments and blank lines ignored."""
    if text is None:
        return None
    return {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")}


def load_english(repo):
    """-> {area: {key: English}} from this repo's <area>/en.json files."""
    try:
        with open(os.path.join(repo, AREAS_PATH), encoding="utf-8") as f:
            areas = list(json.load(f))
    except (OSError, ValueError) as e:
        raise ImportFailed(f"cannot read {AREAS_PATH}: {e}")
    out = {}
    for area in areas:
        i18n_map.validate_area_id(area)
        path = os.path.join(repo, area, "en.json")
        try:
            with open(path, encoding="utf-8") as f:
                out[area] = json.load(f)
        except (OSError, ValueError) as e:
            raise ImportFailed(f"cannot read {area}/en.json: {e}")
    return out


def load_meta(repo, area):
    try:
        with open(os.path.join(repo, "meta", f"{area}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def placeholder_mismatch(english, target, meta):
    icu = i18n_map.is_icu(english, {"plural": "icu-message-format" in ((meta or {}).get("flags") or "")})
    return (bundle_checks.till_verbs(english) != bundle_checks.till_verbs(target)
            or bundle_checks.tokens(english, icu) != bundle_checks.tokens(target, icu))


def split(english_by_area, translations, same_ok, metas=None):
    """Pure: -> ({area: {key: text}}, stats). `same_ok` is the set of keys
    allowed to equal English (None = none)."""
    metas = metas or {}
    key_area = {k: a for a, keys in english_by_area.items() for k in keys}
    files = {a: {} for a in english_by_area}
    stats = {"stale": [], "empty": [], "non_string": [], "same_as_en": [], "placeholders": []}
    for key, text in sorted(translations.items()):
        area = key_area.get(key)
        if area is None:
            stats["stale"].append(key)
            continue
        if not isinstance(text, str):
            stats["non_string"].append(key)
            continue
        if not text.strip():
            stats["empty"].append(key)
            continue
        english = english_by_area[area][key]
        if text == english and key not in (same_ok or ()):
            stats["same_as_en"].append(key)
            continue
        if placeholder_mismatch(english, text, metas.get(area, {}).get(key)):
            stats["placeholders"].append(key)
        files[area][key] = text
    return files, stats


def read_source(workspace, code, ref):
    if code not in SOURCES:
        raise ImportFailed(f"no existing-translation source is configured for locale {code!r}")
    repo_name, locale_path, same_path = SOURCES[code]
    repo = os.path.join(workspace, repo_name)
    text = git_show(repo, ref, locale_path)
    if text is None:
        raise ImportFailed(f"{repo_name}@{ref}: {locale_path} is missing")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ImportFailed(f"{repo_name}@{ref}:{locale_path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ImportFailed(f"{repo_name}@{ref}:{locale_path} is not a JSON object")
    same_ok = parse_key_list(git_show(repo, ref, same_path)) if same_path else None
    return flatten(data), same_ok


def plan(repo, workspace, ref, locales=None):
    """-> ({relative path: text}, report). No writes."""
    with open(os.path.join(repo, BUNDLES_PATH), encoding="utf-8") as f:
        bundles = json.load(f)["locales"]
    codes = [c for c, spec in bundles.items() if not spec.get("source")]
    if locales:
        unknown = sorted(set(locales) - set(codes))
        if unknown:
            raise ImportFailed(f"not a translated bundle locale in {BUNDLES_PATH}: {', '.join(unknown)}")
        codes = [c for c in codes if c in locales]
    english = load_english(repo)
    metas = {a: load_meta(repo, a) for a in english}
    out, report = {}, {}
    for code in codes:
        translations, same_ok = read_source(workspace, code, ref)
        files, stats = split(english, translations, same_ok, metas)
        for area, keys in files.items():
            out[f"{area}/{code}.json"] = i18n_map.dumps(keys)
        report[code] = {
            "source": SOURCES[code][0],
            "areas": {a: {"imported": len(files[a]), "en": len(english[a])} for a in english},
            "imported": sum(len(v) for v in files.values()),
            "en": sum(len(v) for v in english.values()),
            **stats,
        }
    return out, report


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def write(repo, files, force=False, dry_run=False):
    """-> (written, unchanged). Refuses before writing anything when a file
    exists with other content, unless force."""
    existing = {p: _read(os.path.join(repo, p)) for p in files}
    conflicts = sorted(p for p, old in existing.items() if old is not None and old != files[p])
    if conflicts and not force:
        raise ImportFailed("refusing to overwrite files Weblate owns (they differ from the import; "
                           "--force to overwrite): " + ", ".join(conflicts))
    written = sorted(p for p, old in existing.items() if old != files[p])
    unchanged = sorted(p for p in files if p not in written)
    if not dry_run:
        for p in written:
            path = os.path.join(repo, p)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(files[p])
    return written, unchanged


def summary(report):
    lines = []
    for code, r in report.items():
        pct = 100.0 * r["imported"] / r["en"] if r["en"] else 0.0
        lines.append(f"{code} ({r['source']}): {r['imported']}/{r['en']} ({pct:.1f}%); "
                     f"skipped same-as-English {len(r['same_as_en'])}, empty {len(r['empty'])}, "
                     f"stale {len(r['stale'])}; placeholder sets differing from English "
                     f"{len(r['placeholders'])}")
        lines.append("  " + ", ".join(f"{a}={v['imported']}/{v['en']}" for a, v in r["areas"].items()))
        for name in ("stale", "placeholders"):
            if r[name]:
                lines.append(f"  {name}: {', '.join(r[name])}")
    return "\n".join(lines)


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=os.path.join(here, ".."))
    ap.add_argument("--workspace", default=os.path.join(here, "..", ".."),
                    help="folder holding universal-till and ut-plugin-language-* (default: this repo's parent)")
    ap.add_argument("--ref", default="origin/main", help="git ref read in every source repo")
    ap.add_argument("--locale", action="append", help="limit to these locales (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite <area>/<code>.json that differ")
    ap.add_argument("--report", help="also write the full report as JSON here")
    args = ap.parse_args(argv)
    try:
        files, report = plan(args.repo, args.workspace, args.ref, args.locale)
        written, unchanged = write(args.repo, files, force=args.force, dry_run=args.dry_run)
    except (ImportFailed, ValueError, OSError) as e:
        print(f"import failed: {e}", file=sys.stderr)
        return 1
    print(summary(report))
    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {len(written)} file(s), {len(unchanged)} unchanged")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(i18n_map.dumps(report, sort=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
