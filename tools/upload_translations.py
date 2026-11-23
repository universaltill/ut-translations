#!/usr/bin/env python3
"""Validate build-time translations and save them to Weblate as "needs review"
(ut-docs#5421, ADR-0160). A reusable, language-neutral tool; first used for
Italian.

  jobs      write the work for a translator model: per component a JSON of
            key -> {en, max, explanation} for every empty, non-legal string
  validate  check translation files (key -> text, one or more per component:
            <component>.json or <component>.<anything>.json) offline against
            <component>/en.json and meta/<component>.json: placeholders,
            max-length, markup, not-identical-to-English, no unknown or
            missing keys
  upload    validate, then save as state 20 ("translated" = waiting for review
            under dedicated reviewers). NEVER state 30: the writer is a
            translator bot and approval is somebody else's job.
  report    count units by state per component, approved and failing checks

Rules the code enforces:
  * only state 20 is ever sent (patch_body refuses anything else);
  * an approved unit (>= 30) is never touched;
  * `legal`-labelled strings (meta) are never written: they go to a human
    legal-review sheet instead;
  * idempotent: identical text in state 20 is left alone; a unit that already
    holds different text is kept unless --overwrite.

Token: macOS Keychain service "weblate-translator" (or WEBLATE_TRANSLATOR_TOKEN),
never printed. The reviewer token is not used here.

  python3 tools/upload_translations.py validate --locale it --dir DIR
  python3 tools/upload_translations.py upload   --locale it --dir DIR --dry-run
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundle_checks  # noqa: E402
import bulk_locale  # noqa: E402
from provision_weblate import WeblateClient, WeblateError  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEEDS_REVIEW = 20
APPROVED = 30
PROJECT = bulk_locale.PROJECT

# Words that are the same in every language; an identical-to-English
# translation of anything else is treated as "not translated".
INTERNATIONAL = {
    "email", "e-mail", "pdf", "csv", "json", "xml", "api", "url", "usb", "nfc", "qr", "sku", "pin",
    "wi-fi", "wifi", "bluetooth", "ok", "gps", "ssid", "ip", "dns", "http", "https", "html", "id",
    "sms", "iban", "bic", "pos", "ean", "upc", "pdf/a", "menu", "online",
    "offline", "standard", "extra", "no", "tag", "hub", "cloud", "plugin", "stripe", "sumup",
}
TOKEN_STRIP = re.compile(r"%(\[\d+\])?[-+#0]*(\d+|\*)?(\.(\d+|\*))?[a-zA-Z%]|\{\{[^}]*\}\}|\{[^}]*\}|<[^>]+>")


def patch_body(text, state=NEEDS_REVIEW):
    """The only body this tool sends. Anything but 'needs review' is refused."""
    if state != NEEDS_REVIEW:
        raise ValueError(f"refusing to write state {state}: only {NEEDS_REVIEW} (needs review)")
    return {"target": [text], "state": NEEDS_REVIEW}


def _words(english):
    return [w for w in re.findall(r"[^\W\d_]+(?:[-/][^\W\d_]+)*", TOKEN_STRIP.sub(" ", english))]


def identical_ok(english):
    if english.strip() in bulk_locale.KEEP_NAMES:
        return True
    ws = [w for w in _words(english) if len(w) >= 3]
    return all(w.lower() in INTERNATIONAL for w in ws)


def problems(english, text, meta, script="Latn", same_ok=False):
    """-> None, or (kind, detail) when the text may not be saved."""
    if isinstance(text, str) and text.strip() and "\n" not in english and "\n" in text.strip():
        return "multiline", "more than one line for a one-line string"
    bad = bundle_checks.check_string(english, text, {"flags": (meta or {}).get("flags") or ""}, script)
    if bad:
        return bad
    if text == english and not same_ok and not identical_ok(english):
        return "identical", "same as the English text"
    edge = lambda t: (t[:len(t) - len(t.lstrip())], t[len(t.rstrip()):])
    if edge(text) != edge(english):
        return "whitespace", "leading or trailing space differs from the English"
    return None


def load_meta(repo=REPO):
    out = {}
    for name in sorted(os.listdir(os.path.join(repo, "meta"))):
        if not name.endswith(".json") or name in ("areas.json", "source.json"):
            continue
        comp = name[:-5]
        with open(os.path.join(repo, "meta", name), encoding="utf-8") as f:
            for k, v in json.load(f).items():
                out[(comp, k)] = v
    return out


def load_english(comp, repo=REPO):
    with open(os.path.join(repo, comp, "en.json"), encoding="utf-8") as f:
        return json.load(f)


def is_legal_key(comp, key, meta):
    return "legal" in ((meta.get((comp, key)) or {}).get("labels") or [])


def validate_map(comp, tr, english, meta, require_all=None, script="Latn", same=()):
    """-> [(key, kind, detail)] for one component's merged translation map."""
    out = []
    for k, v in tr.items():
        if k not in english:
            out.append((k, "unknown_key", "not in en.json"))
            continue
        if is_legal_key(comp, k, meta):
            out.append((k, "legal", "legal strings go to the legal-review sheet, not Weblate"))
            continue
        bad = problems(english[k], v, meta.get((comp, k)), script, f"{comp}:{k}" in same)
        if bad:
            out.append((k, bad[0], bad[1]))
    for k in sorted(set(require_all or ()) - set(tr)):
        out.append((k, "missing", "no translation"))
    return out


def upload_component(client, comp, locale, tr, meta, dry_run=False, overwrite=False, script="Latn", note=None, same=()):
    stats = {"written": 0, "unchanged": 0, "skipped_approved": 0, "skipped_legal": 0,
             "skipped_differs": 0, "rejected": [], "failed": [], "commented": 0}
    units = client.list(f"/api/translations/{PROJECT}/{comp}/{locale}/units/?page_size=1000") or []
    for u in units:
        key = u.get("context") or ""
        if key not in tr:
            continue
        state = u.get("state") or 0
        cur = (u.get("target") or [""])[0]
        english = u["source"][0]
        if state >= APPROVED:
            stats["skipped_approved"] += 1
            continue
        if is_legal_key(comp, key, meta) or any((l.get("name") if isinstance(l, dict) else l) == "legal"
                                                for l in u.get("labels") or []):
            stats["skipped_legal"] += 1
            continue
        text = tr[key]
        bad = problems(english, text, meta.get((comp, key)), script, f"{comp}:{key}" in same)
        if bad:
            stats["rejected"].append({"key": key, "kind": bad[0], "detail": bad[1]})
            continue
        if cur == text and state == NEEDS_REVIEW:
            stats["unchanged"] += 1
            continue
        if cur.strip() and not overwrite:
            stats["skipped_differs"] += 1
            continue
        if dry_run:
            stats["written"] += 1
            continue
        try:
            _patch(client, u["id"], patch_body(text))
            stats["written"] += 1
        except WeblateError as e:
            stats["failed"].append({"key": key, "error": str(e)[:160]})
            continue
        if note:
            try:
                _comment(client, u["id"], note)
                stats["commented"] += 1
            except WeblateError:
                note = None  # the endpoint is not available: stop trying
    return stats


def _patch(client, unit_id, body):
    for _ in range(90):
        try:
            status, out = client.request("PATCH", f"/api/units/{unit_id}/", body)
            if status == 404:
                raise WeblateError(f"PATCH unit {unit_id}: 404 not found")
            return out
        except WeblateError as e:
            if "429" in str(e):
                time.sleep(60)
                continue
            raise
    raise WeblateError("throttled for too long")


def _comment(client, unit_id, text):
    status, out = client.request("POST", f"/api/units/{unit_id}/comments/", {"comment": text, "scope": "translation"})
    if status == 404:
        raise WeblateError("comments endpoint not available (404)")
    return out


def load_same(locale):
    """Reviewed "component:key" strings that are legitimately the same as the
    English (brand, loan word): config/glossary/<locale>.same.json."""
    path = os.path.join(REPO, "config", "glossary", f"{locale}.same.json")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return set(json.load(f))


def components():
    with open(os.path.join(REPO, "meta", "areas.json"), encoding="utf-8") as f:
        return list(json.load(f))


def read_dir(d, comp):
    """Merge <comp>.json and <comp>.*.json in d."""
    out = {}
    for name in sorted(os.listdir(d)):
        if name == comp + ".json" or (name.startswith(comp + ".") and name.endswith(".json")):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                data = json.load(f)
            dup = set(out) & set(data)
            if dup:
                raise SystemExit(f"{name}: duplicate keys across files: {sorted(dup)[:3]}")
            out.update(data)
    return out


def cmd_jobs(a):
    meta = load_meta()
    done = {}
    if a.skip_dir:
        for comp in components():
            done[comp] = set(read_dir(a.skip_dir, comp))
    os.makedirs(a.dir, exist_ok=True)
    n = 0
    for comp in a.component or components():
        en = load_english(comp)
        keys = [k for k in en if not is_legal_key(comp, k, meta) and k not in done.get(comp, ())]
        chunks = [keys[i:i + a.chunk] for i in range(0, len(keys), a.chunk)]
        for i, ks in enumerate(chunks, 1):
            job = {}
            for k in ks:
                m = meta.get((comp, k)) or {}
                job[k] = {"en": en[k]}
                ml = bundle_checks.max_length(m.get("flags"))
                if ml:
                    job[k]["max"] = ml
                if m.get("explanation"):
                    job[k]["explanation"] = m["explanation"]
            with open(os.path.join(a.dir, f"{comp}.{i:02d}.json"), "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False, indent=1)
            n += len(ks)
    print(f"{n} strings in jobs under {a.dir}")


def cmd_validate(a, quiet=False):
    meta = load_meta()
    bad_total = 0
    for comp in a.component or components():
        tr = read_dir(a.dir, comp)
        if not tr:
            continue
        en = load_english(comp)
        req = None
        if a.require_all:
            req = {k for k in en if not is_legal_key(comp, k, meta)}
            if a.skip_dir:
                req -= set(read_dir(a.skip_dir, comp))
        probs = validate_map(comp, tr, en, meta, req, script=script_of(a.locale), same=load_same(a.locale))
        bad_total += len(probs)
        print(f"{comp}: {len(tr)} strings, {len(probs)} problems")
        for k, kind, detail in probs[:a.show]:
            print(f"   {k}: {kind} {detail}")
    print("problems:", bad_total)
    return 1 if bad_total else 0


def script_of(locale):
    """The ISO 15924 script a draft in `locale` is checked against (Grek for el,
    Arab for ar and fa); Latin only when the language is Latin."""
    import review_checks
    return review_checks.script_for(locale)


def client(a):
    return WeblateClient(a.weblate_url, bulk_locale.secret(bulk_locale.TRANSLATOR_KEYCHAIN, "WEBLATE_TRANSLATOR_TOKEN"))


def cmd_upload(a):
    if cmd_validate(a):
        return 1
    meta = load_meta()
    c = client(a)
    totals = {}
    rej = []
    for comp in a.component or components():
        tr = read_dir(a.dir, comp)
        if not tr:
            continue
        s = upload_component(c, comp, a.locale, tr, meta, a.dry_run, a.overwrite, script=script_of(a.locale), note=a.note, same=load_same(a.locale))
        rej += [(comp, r) for r in s["rejected"]]
        print(comp, {k: (len(v) if isinstance(v, list) else v) for k, v in s.items()}, flush=True)
        for k, v in s.items():
            totals[k] = totals.get(k, 0) + (len(v) if isinstance(v, list) else v)
    print("totals:", totals)
    return 1 if rej or totals.get("failed") else 0


def cmd_report(a):
    c = client(a)
    tot = {}
    for comp in components():
        units = c.list(f"/api/translations/{PROJECT}/{comp}/{a.locale}/units/?page_size=1000") or []
        by = {}
        for u in units:
            by[u["state"]] = by.get(u["state"], 0) + 1
        failing = c.list(f"/api/translations/{PROJECT}/{comp}/{a.locale}/units/?page_size=1000&q=has:check") or []
        print(f"{comp}: total {len(units)} by state {dict(sorted(by.items()))} failing_checks {len(failing)}"
              f" approved {by.get(30, 0)}")
        for k, v in by.items():
            tot[k] = tot.get(k, 0) + v
        tot["failing"] = tot.get("failing", 0) + len(failing)
    print("TOTAL", dict(sorted(tot.items(), key=str)))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=["jobs", "validate", "upload", "report"])
    p.add_argument("--locale", default="it")
    p.add_argument("--dir", help="folder of translation files (key -> text), or the jobs output")
    p.add_argument("--skip-dir", help="folder of already-done files (seed): their keys are not required/jobbed")
    p.add_argument("--component", action="append")
    p.add_argument("--chunk", type=int, default=140)
    p.add_argument("--show", type=int, default=25)
    p.add_argument("--require-all", action="store_true", help="validate: every non-legal key must be present")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--note", help="upload: unit comment to add on each written string")
    p.add_argument("--weblate-url", default=os.environ.get("WEBLATE_URL", bulk_locale.WEBLATE_URL))
    a = p.parse_args(argv)
    if a.command != "report" and not a.dir:
        p.error("--dir is required")
    return {"jobs": cmd_jobs, "validate": cmd_validate, "upload": cmd_upload, "report": cmd_report}[a.command](a) or 0


if __name__ == "__main__":
    sys.exit(main())
