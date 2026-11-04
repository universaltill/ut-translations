#!/usr/bin/env python3
"""Which languages the bundle workflow may build, and how each one is set up
(ADR-0160 §5; ut-docs#4640). Adding a language in Weblate is the only step:
the code, name and script of every other language are derived here.

Identifiers. The workflow input is the WEBLATE code (de, pt_BR, zh_Hans).
The till tag is code.lower() with "_" -> "-" (zh_Hans -> zh-hans): the plugin
id com.universaltill.language-<tag>, locales/<tag>.json, the manifest's
locales and bundle.json's locale. Weblate calls use the code.

What config/bundles.json may say (overrides only, a closed set of fields):
  source           the language is built from this repo's English
  human_spot_check publishes through the bundle-publish-human environment
  min_coverage     share of the keys a bundle must hold before it publishes
  script           ISO 15924 script (only a known one; a suffix like _Hans wins)
  name             exact manifest name (keeps the first seven byte-identical)
A language without an override gets human_spot_check false and no
min_coverage — except that a regional or script variant (de_AT, en_GB,
tr_CY) inherits human_spot_check from its base language's override, so a
variant of a human-spot-check language can never route around the
bundle-publish-human environment (review 2026-11-04). Only the code and the
display name are ever read from Weblate; no review-gate field (ADR-0160 §4)
can come from there.

CLI:  locales.py --check <code> [--live]
  prints tag=<tag> and human=true|false (from the overrides only, the base
  language's included). --live
  also requires the code in Weblate's public language list ($WEBLATE_URL,
  https, no token, no redirect). Exit 1 on any refusal.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundle_checks  # noqa: E402

# The workflow's validate job carries this exact string (tests/test_workflow.py).
CODE_RE_TEXT = "^[a-z]{2,3}(_[A-Za-z0-9]{2,8})?$"
CODE_RE = re.compile(CODE_RE_TEXT)
ALLOWED_OVERRIDES = ("source", "human_spot_check", "min_coverage", "script", "name")
TOP_KEYS = ("note", "project", "human_reviewers_team", "overrides")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
NAME_MAX = 64

# Scripts the per-string check (bundle_checks.check_script) can tell apart.
KNOWN_SCRIPTS = frozenset({
    "Latn", "Cyrl", "Grek", "Arab", "Hebr", "Deva", "Beng", "Thai", "Kore", "Jpan", "Hans", "Hant",
    "Geor", "Armn", "Taml", "Gujr", "Guru", "Knda", "Mlym", "Telu", "Sinh", "Khmr", "Laoo", "Mymr", "Ethi",
})

# Language -> script when the code names no script. A language written in
# more than one script (zh, sr, uz, mn, pa in Pakistan...) is absent on
# purpose: it needs a script suffix (zh_Hans) or an explicit override.
SCRIPTS = {}
for _script, _langs in {
    "Latn": "af az bs ca cs cy da de en es et eu fi fil fr ga gl hr hu id is it lt lv ms mt nb nn nl no pl pt "
            "ro sk sl sq sv sw tr vi",
    "Cyrl": "bg be mk ru uk kk",
    "Arab": "ar fa ur ps ckb",
    "Hebr": "he", "Grek": "el", "Thai": "th", "Kore": "ko", "Jpan": "ja",
    "Deva": "hi mr ne", "Beng": "bn", "Taml": "ta", "Telu": "te", "Knda": "kn", "Mlym": "ml", "Gujr": "gu",
    "Guru": "pa", "Sinh": "si", "Khmr": "km", "Laoo": "lo", "Mymr": "my", "Geor": "ka", "Armn": "hy",
    "Ethi": "am",
}.items():
    for _l in _langs.split():
        SCRIPTS[_l] = _script


# The public, anonymous-readable Weblate (no secret): the validate job has no
# environment, so it cannot read the bundle-build WEBLATE_URL variable.
PUBLIC_WEBLATE_URL = "https://translate.universaltill.com"


class LocaleError(Exception):
    pass


def till_tag(code):
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        raise LocaleError(f"locale {code!r} must match {CODE_RE_TEXT}")
    return code.lower().replace("_", "-")


def _check_script_name(script, where):
    if script not in KNOWN_SCRIPTS:
        raise LocaleError(f"{where}: script {script!r} is not one of {sorted(KNOWN_SCRIPTS)}")
    if not bundle_checks.supports_script(script):
        raise LocaleError(f"{where}: no per-string checker for script {script!r}; the bundle would be unchecked")
    return script


def script_for(code, override=None):
    """ISO 15924 script of a language. A script in the code wins; an override
    must agree with it. Unknown -> LocaleError (never a default)."""
    till_tag(code)
    lang, _, suffix = code.partition("_")
    in_code = suffix.title() if len(suffix) == 4 and suffix.isalpha() and suffix.title() in KNOWN_SCRIPTS else None
    if override is not None:
        _check_script_name(override, f"{code} override")
        if in_code and in_code != override:
            raise LocaleError(f"{code}: override script {override} contradicts the {in_code} in the code")
        return override
    if in_code:
        return _check_script_name(in_code, code)
    if lang not in SCRIPTS:
        raise LocaleError(f"{code}: no known script for language {lang!r} (add a script override, or a script "
                          "suffix such as zh_Hans)")
    return _check_script_name(SCRIPTS[lang], code)


def clean_name(name, code):
    """A display name safe to put in a manifest: letters, marks, digits,
    spaces and ()-'.,; at most NAME_MAX characters."""
    out = []
    for ch in unicodedata.normalize("NFC", name if isinstance(name, str) else ""):
        cat = unicodedata.category(ch)
        if cat[0] in "LMN" or ch in "()-'.,":
            out.append(ch)
        elif cat == "Zs" or ch in "\t\n":
            out.append(" ")
    text = re.sub(r" +", " ", "".join(out)).strip()[:NAME_MAX].strip()
    return text or code


def validate_shape(cfg):
    """Refuse a config/bundles.json that is not overrides-only."""
    if not isinstance(cfg, dict):
        raise LocaleError("config/bundles.json is not an object")
    extra = sorted(set(cfg) - set(TOP_KEYS))
    if extra:
        raise LocaleError(f"config/bundles.json: unknown key(s) {extra}")
    for k in ("project", "human_reviewers_team"):
        if not isinstance(cfg.get(k), str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", cfg[k]):
            raise LocaleError(f"config/bundles.json: {k} must be a short slug")
    ov = cfg.get("overrides")
    if not isinstance(ov, dict):
        raise LocaleError("config/bundles.json: overrides must be an object")
    tags = {}
    for code, o in ov.items():
        tag = till_tag(code)
        if tag in tags:
            raise LocaleError(f"overrides {tags[tag]!r} and {code!r} both map to {tag!r}")
        tags[tag] = code
        if not isinstance(o, dict):
            raise LocaleError(f"override for {code} is not an object")
        bad = sorted(set(o) - set(ALLOWED_OVERRIDES))
        if bad:
            raise LocaleError(f"override for {code}: unknown field(s) {bad}")
        for f in ("source", "human_spot_check"):
            if f in o and not isinstance(o[f], bool):
                raise LocaleError(f"override for {code}: {f} must be true or false")
        if "min_coverage" in o:
            v = o["min_coverage"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
                raise LocaleError(f"override for {code}: min_coverage must be a number in (0, 1]")
        if "script" in o:
            script_for(code, o["script"])
        if "name" in o and (not isinstance(o["name"], str) or not o["name"].strip()):
            raise LocaleError(f"override for {code}: name must be a non-empty string")
    return cfg


def load_config(repo):
    with open(os.path.join(repo, "config", "bundles.json"), encoding="utf-8") as f:
        return validate_shape(json.load(f))


def human_spot_check(cfg, code):
    """Whether `code` publishes through bundle-publish-human: its own override
    first, else its base language's (de_AT -> de), else False. Only
    human_spot_check is inherited: source and min_coverage never are."""
    till_tag(code)
    ov = cfg["overrides"]
    own = ov.get(code) or {}
    if "human_spot_check" in own:
        return bool(own["human_spot_check"])
    base = ov.get(code.partition("_")[0]) or {}
    return bool(base.get("human_spot_check", False))


def derive_spec(code, weblate_name, override=None, cfg=None):
    """The build spec of one language. Gate fields come from the overrides
    only; Weblate supplies the code and a sanitised display name."""
    o = override or {}
    spec = {
        "weblate": code,
        "tag": till_tag(code),
        "name": o["name"] if "name" in o else clean_name(weblate_name, code),
        "script": script_for(code, o.get("script")),
        "human_spot_check": human_spot_check(cfg, code) if cfg is not None else bool(o.get("human_spot_check", False)),
    }
    if o.get("source"):
        spec["source"] = True
    if "min_coverage" in o:
        spec["min_coverage"] = o["min_coverage"]
    return spec


def _language_rows(data):
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise LocaleError("Weblate's language list is not a list")
    out = {}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("code"), str):
            raise LocaleError("Weblate's language list holds an entry without a code")
        out[r["code"]] = r.get("name") if isinstance(r.get("name"), str) else ""
    if not out:
        raise LocaleError("Weblate lists no languages for the project")
    return out


def fetch_languages(client, project):
    """{code: name} from the authenticated client; fails closed."""
    try:
        data = client.get(f"/api/projects/{urllib.parse.quote(project)}/languages/")
    except Exception as e:  # WeblateError, OSError...
        raise LocaleError(f"can't read Weblate's language list: {e}") from None
    if data is None:
        raise LocaleError("Weblate's language list was not found (404)")
    return _language_rows(data)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch_languages_public(base_url, project, timeout=30):
    """The same list without a token (the endpoint is anonymously readable):
    https only (loopback http for tests), no redirect, fails closed."""
    u = urllib.parse.urlparse(base_url or "")
    if u.scheme != "https" and not (u.scheme == "http" and u.hostname in LOOPBACK):
        raise LocaleError(f"WEBLATE_URL must be an https URL (got {base_url!r})")
    url = base_url.rstrip("/") + f"/api/projects/{urllib.parse.quote(project)}/languages/"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.build_opener(_NoRedirects).open(req, timeout=timeout) as r:
            if r.status != 200:
                raise LocaleError(f"Weblate language list: HTTP {r.status}")
            data = json.loads(r.read())
    except LocaleError:
        raise
    except urllib.error.HTTPError as e:
        e.close()
        raise LocaleError(f"Weblate language list: HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LocaleError(f"can't read Weblate's language list: {e}") from None
    return _language_rows(data)


def resolve_spec(source, repo, code, languages_fetch=None):
    """-> spec for `code`. `source` is a Weblate client (has .get), a
    {code: name} dict, a list of language rows, or None (only a `source`
    override, i.e. English, resolves without Weblate). Refuses a code Weblate
    does not list, and any two listed codes that share a till tag."""
    cfg = load_config(repo) if isinstance(repo, str) else repo
    tag = till_tag(code)
    override = cfg["overrides"].get(code)
    if override and override.get("source"):
        # Built from this repo's English: no Weblate list needed.
        return derive_spec(code, "", override, cfg)
    live = None
    if source is not None:
        if hasattr(source, "get") and not isinstance(source, dict):
            live = fetch_languages(source, cfg["project"])
        elif isinstance(source, dict):
            live = _language_rows([{"code": c, "name": n} for c, n in source.items()])
        else:
            live = _language_rows(source)
        seen = {}
        for c in live:
            if not CODE_RE.fullmatch(c):
                continue
            t = till_tag(c)
            if t in seen and seen[t] != c:
                raise LocaleError(f"Weblate languages {seen[t]!r} and {c!r} both map to till tag {t!r}")
            seen[t] = c
    if live is None:
        raise LocaleError("WEBLATE_URL and WEBLATE_TOKEN are needed to resolve a translated language")
    if code not in live:
        raise LocaleError(f"{code!r} is not a language of Weblate project {cfg['project']!r}")
    return derive_spec(code, live[code], override, cfg)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a locale code and print its till tag.")
    ap.add_argument("--check", required=True, metavar="CODE")
    ap.add_argument("--live", action="store_true", help="also require it in Weblate's public language list")
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.repo)
        tag = till_tag(args.check)
        o = cfg["overrides"].get(args.check, {})
        if args.live:
            if not o.get("source"):
                live = fetch_languages_public((os.environ.get("WEBLATE_URL") or PUBLIC_WEBLATE_URL), cfg["project"])
                resolve_spec(live, cfg, args.check)
        else:
            script_for(args.check, o.get("script"))
    except (LocaleError, OSError, ValueError) as e:
        print(f"locale refused: {e}", file=sys.stderr)
        return 1
    print(f"tag={tag}")
    print(f"human={'true' if human_spot_check(cfg, args.check) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
