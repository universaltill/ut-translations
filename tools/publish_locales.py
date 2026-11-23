#!/usr/bin/env python3
"""Compute the published-locale list and the approved strings of every
non-till surface (ADR-0177 section 2/3; ut-docs#5405, slice 3).

  published/locales.json            schema 1: one entry per Weblate language
  published/<surface>/<code>.json   flat key -> text, shipped strings only

Read-only against Weblate's PUBLIC API (languages, units, change history):
no token, no secret. Run it with no arguments to write `published/`;
`--dry-run` prints the summary only; `--check` validates the committed
`published/` offline (CI).

Rules (owner decisions of 2026-11-04):
  * a string ships only when Weblate holds it in state `approved` (30) against
    the current English and its change history shows an approval by a
    different account than the author (ADR-0160 section 4, reusing
    build_bundle.review_verdict). The public API cannot read team membership,
    so a non-AI approver is accepted as "a human" but is NOT recorded as a
    human review: `human_reviewed` needs the approver to be listed in
    config/publish-policy.json `human_reviewers`. A `legal` key ships only
    with such an approver. When the history is unreadable the string ships on
    its state alone and `review_verified` is false.
  * Weblate's failing style checks never block. A unit whose placeholder set,
    ICU argument names or HTML tag set differs from English is omitted (the
    consumer shows English for it) and counted in `omitted_broken`.
  * a `legal` key without that approval is OMITTED (English by design, per-key
    fallback), excluded from the coverage denominator and the `core` test, and
    counted in `omitted_legal`; the locale still publishes. A `core` key that
    is legal-tier is therefore English for AI-only locales: core UI keys such
    as `site.machine_label` must be `standard` tier.
  * a surface is published when coverage >= min_coverage, every `core` key
    ships, and omitted_broken <= max_broken_ratio of its keys.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_bundle  # noqa: E402
import bundle_checks  # noqa: E402
import i18n_map  # noqa: E402
import surfaces as surfaces_mod  # noqa: E402
from provision_weblate import _RefuseRedirects  # noqa: E402

DEFAULT_URL = "https://translate.universaltill.com"
PUBLISHED = "published"
SOURCE = "en"
STATE_APPROVED, STATE_READONLY = 30, 100
CODE_RE = re.compile(r"[A-Za-z]{2,3}(?:[_@][A-Za-z0-9]{2,8})*")
URL_LOCALE_RE = re.compile(r"[a-z]{2,3}-[a-z0-9]{2,8}")
TAG_RE = re.compile(r"<[A-Za-z][^<>]*>", re.S)
ATTR_RE = re.compile(r"\s([A-Za-z][A-Za-z0-9:-]*)\s*=")
JS_URL_RE = re.compile(r"(?:javascript|data|vbscript)\s*:", re.I)
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class PublishError(Exception):
    pass


class AuthRequired(PublishError):
    """The endpoint exists but needs a login (401/403)."""


# --- Weblate (public, read-only) ---------------------------------------------

class PublicClient:
    """GET-only, unauthenticated, https only, never follows a redirect."""

    def __init__(self, base=DEFAULT_URL, timeout=30):
        u = urllib.parse.urlparse(base)
        if u.scheme != "https":
            raise PublishError(f"refusing {u.scheme}:// for {u.hostname}: use https")
        self.base, self.origin, self.timeout = base.rstrip("/"), u.netloc, timeout
        self.opener = urllib.request.build_opener(_RefuseRedirects)

    def get(self, path):
        url = path if path.startswith("https://") else self.base + path
        if urllib.parse.urlparse(url).netloc != self.origin:
            raise PublishError(f"refusing to leave {self.origin}: {url}")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                if e.code == 404:
                    return None
                if e.code in (401, 403):
                    raise AuthRequired(f"GET {path}: HTTP {e.code} (needs login)") from None
            raise PublishError(f"GET {path}: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise PublishError(f"GET {path}: {e.reason}") from None

    def list(self, path):
        out, nxt = [], path
        while nxt:
            page = self.get(nxt)
            if page is None:
                return None
            if isinstance(page, list):
                return out + page
            out.extend(page.get("results", []))
            nxt = page.get("next")
            if nxt:
                u = urllib.parse.urlparse(nxt)
                if u.netloc and u.netloc != self.origin:
                    raise PublishError(f"refusing a next link to another host: {nxt}")
                nxt = u.path + ("?" + u.query if u.query else "")
        return out


# --- config ------------------------------------------------------------------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Config:
    def __init__(self, repo, surfaces_file=None):
        self.repo = repo
        reg = load_json(os.path.join(repo, "config/locale-regions.json"))
        self.regions = reg.get("locales") or {}
        pol = load_json(os.path.join(repo, "config/publish-policy.json"))
        self.policy = pol
        self.project = pol.get("project", "universal-till")
        self.human_reviewers = set(pol.get("human_reviewers") or [])
        ai = load_json(os.path.join(repo, "config/ai-accounts.json")).get("accounts") or {}
        self.ai = ai
        if surfaces_file:
            doc = load_json(surfaces_file)
            self.surfaces = surfaces_mod.validate(doc)  # no till-area collision check: a dry-run aid
        else:
            self.surfaces = surfaces_mod.load(repo)
        for code, spec in self.regions.items():
            if not CODE_RE.fullmatch(code):
                raise PublishError(f"config/locale-regions.json: bad code {code!r}")
            if not URL_LOCALE_RE.fullmatch(spec.get("url", "")):
                raise PublishError(f"config/locale-regions.json: {code}: url {spec.get('url')!r} must be lang-region, lower case")
        for name in self.surfaces:
            if not SLUG_RE.fullmatch(name):
                raise PublishError(f"bad surface name {name!r}")

    def setting(self, key, code, surface):
        """policy default, then overrides[code][key], then overrides[code][surface][key]."""
        v = self.policy.get(key)
        o = (self.policy.get("overrides") or {}).get(code) or {}
        if key in o:
            v = o[key]
        if key in (o.get(surface) or {}):
            v = o[surface][key]
        return v


def resolve_names(code, cfg):
    """-> (url_locale, bcp47, english_name, native_name). Override table first,
    then Babel/CLDR when installed, else fail asking for an override."""
    spec = cfg.regions.get(code)
    babel = None
    try:
        import babel  # noqa: F811
        from babel import Locale
    except ImportError:
        pass
    if spec:
        url, en, native = spec["url"], spec.get("english_name"), spec.get("native_name")
    elif babel is not None:
        parts = code.split("_")
        lang = parts[0].lower()
        region = next((p for p in parts[1:] if len(p) == 2 and p.isalpha()), None)
        if region is None:
            likely = babel.core.get_global("likely_subtags").get(lang) or ""
            region = likely.split("_")[-1] if "_" in likely else ""
        if not region:
            raise PublishError(f"language {code!r}: CLDR gives no region; add it to config/locale-regions.json")
        url, en, native = f"{lang}-{region.lower()}", None, None
    else:
        raise PublishError(f"language {code!r} has no entry in config/locale-regions.json and Babel is not installed to derive one: "
                           "add {url, english_name, native_name} there (owner decision on the region)")
    if (en is None or native is None) and babel is not None:
        loc = Locale.parse(code, sep="_")
        en = en or loc.get_display_name("en")
        native = native or loc.get_display_name(loc)
    if not en or not native:
        raise PublishError(f"language {code!r}: no english_name/native_name in config/locale-regions.json")
    if not URL_LOCALE_RE.fullmatch(url):
        raise PublishError(f"language {code!r}: derived url locale {url!r} is not lang-region; add an entry to config/locale-regions.json")
    lang, region = url.split("-", 1)
    return url, f"{lang}-{region.upper()}", en, native


# --- accounts / review --------------------------------------------------------

class PublicAccounts:
    """build_bundle.review_verdict's view of accounts when only the public
    API is readable: no team membership, so `human` = a name listed in the
    policy's human_reviewers."""

    def __init__(self, ai, humans):
        self.ai, self.humans = ai, set(humans)

    def is_ai(self, name):
        return name in self.ai or name.startswith("bot-")

    def model(self, name):
        return (self.ai.get(name) or {}).get("model")

    def is_human_reviewer(self, name):
        return name in self.humans and not self.is_ai(name)


def broken(english, target, meta):
    """None, or why a translation would break the page: empty, a different
    placeholder / ICU argument set, a different HTML tag set."""
    if not isinstance(target, str) or not target.strip():
        return "empty"
    icu = i18n_map.is_icu(english, meta)
    if bundle_checks.till_verbs(english) != bundle_checks.till_verbs(target) \
            or bundle_checks.tokens(english, icu) != bundle_checks.tokens(target, icu):
        return "placeholders"
    tags = lambda t: {k: v for k, v in bundle_checks.kept_items(t).items() if k.startswith("tag:")}  # noqa: E731
    if tags(english) != tags(target):
        return "html_tags"
    # no attribute (onclick, style, ...) or javascript: URL the English does not have
    if Counter(_attrs(target)) - Counter(_attrs(english)) or JS_URL_RE.search(target):
        return "html_attributes"
    return None


def _attrs(text):
    return [a.lower() for t in TAG_RE.findall(text) for a in ATTR_RE.findall(t)]


def is_legal(meta):
    return build_bundle.LEGAL in (meta.get("labels") or [])


# --- one (language, surface) -----------------------------------------------

def translate_surface(client, cfg, comp, wl, en, meta):
    """-> dict(strings, omitted Counter, review_verified, human_approved)."""
    base = f"/api/translations/{cfg.project}/{comp}/{wl}"
    omitted, strings, defective = Counter(), {}, set()
    units = client.list(f"{base}/units/?q=state%3Aapproved&page_size=1000") or []
    approved = [u for u in units if STATE_APPROVED <= (u.get("state") or 0) < STATE_READONLY]
    history, readable = {}, True
    if approved:
        try:
            chs = client.list(f"{base}/changes/?page_size=1000")
        except AuthRequired:
            chs, readable = [], False  # history needs a login: ship on state, review_verified=false
        else:
            # any other failure (429, 5xx, a missing list) must not downgrade the gate
            if chs is None:
                raise PublishError(f"{base}/changes/ not found although approved units exist")
        if readable:
            for ch in chs:
                uid = build_bundle.unit_id(ch.get("unit"))
                if uid is not None:
                    history.setdefault(uid, []).append(ch)
            for v in history.values():
                v.sort(key=lambda c: (c.get("timestamp") or "", c.get("id") or 0))
    accounts = PublicAccounts(cfg.ai, cfg.human_reviewers)
    human = 0
    for u in approved:
        key = u.get("context")
        if key not in en:
            continue
        target = build_bundle.first(u.get("target"))
        m = meta.get(key) or {}
        legal = is_legal(m)
        if build_bundle.first(u.get("source")) != en[key] or build_bundle.first(u.get("previous_source")):
            omitted["stale_source"] += 1
            defective.add(key)
            continue
        approver = None
        if readable:
            why = build_bundle.review_verdict(target, history.get(u.get("id"), []), legal, accounts)
            # an approver the public API can't classify is a human outside the list: fine for a standard key
            if why == "unknown_approver" and not legal:
                why = None
            if why:
                omitted["legal_english_only" if why == "legal_needs_human" else "review_" + why] += 1
                continue
            approver = _approver(history.get(u.get("id"), []), target)
        elif legal:
            omitted["legal_english_only"] += 1
            continue
        why = broken(en[key], target, m)
        if why:
            omitted["broken_" + why] += 1
            defective.add(key)
            continue
        strings[key] = target
        if approver and accounts.is_human_reviewer(approver):
            human += 1
    return {"strings": strings, "omitted": omitted, "review_verified": readable, "human_approved": human,
            "defective": defective}


def _approver(history, target):
    text = approver = None
    for ch in history:
        if ch.get("action") in build_bundle.INVALIDATING:
            approver = None
            continue
        tgt = ch.get("target")
        if ch.get("action") in build_bundle.TEXT_ACTIONS and isinstance(tgt, str) and tgt != text:
            text, approver = tgt, None
        if ch.get("action") == build_bundle.A_APPROVE or (
                ch.get("action") == build_bundle.A_ACCEPT and (ch.get("details") or {}).get("state") == build_bundle.STATE_APPROVED):
            if tgt == text:
                approver = build_bundle.username(ch.get("user"))
    return approver


# --- the whole list -----------------------------------------------------------

def load_surface_english(repo, surfaces):
    """-> {surface: (en, meta)} for surfaces whose English is in this repo."""
    out = {}
    for name, sp in surfaces.items():
        comp = sp["component"]
        p = os.path.join(repo, comp, "en.json")
        if not os.path.isfile(p):
            continue
        mp = os.path.join(repo, "meta", f"{comp}.json")
        out[name] = (load_json(p), load_json(mp) if os.path.isfile(mp) else {})
    return out


def nest(surfaces):
    """cloud-manage -> surfaces["cloud"]["manage"]; everything else flat."""
    out = {}
    for name, val in surfaces.items():
        if name.startswith("cloud-"):
            out.setdefault("cloud", {})[name[len("cloud-"):]] = val
        else:
            out[name] = val
    return out


def compute(repo, cfg, client, previous=None, now=None, source_commit=None):
    """-> (doc, files {relative path: text}, summary lines)."""
    english = load_surface_english(repo, cfg.surfaces)
    langs = client.list(f"/api/projects/{cfg.project}/languages/")
    if langs is None:
        raise PublishError(f"Weblate project {cfg.project!r} not found")
    codes = sorted({l["code"] for l in langs if l.get("code")} | {SOURCE})
    for c in codes:
        if not CODE_RE.fullmatch(c):
            raise PublishError(f"Weblate language code {c!r} is not a plain code")
    locales, files, seen_url = [], {}, {}
    for code in codes:
        url, bcp47, en_name, native = resolve_names(code, cfg)
        if url in seen_url:
            raise PublishError(f"languages {seen_url[url]!r} and {code!r} both map to url locale {url!r}: add an override in config/locale-regions.json")
        seen_url[url] = code
        info = client.get(f"/api/languages/{code}/")
        direction = (info or {}).get("direction")
        if direction not in ("ltr", "rtl"):
            raise PublishError(f"language {code!r}: Weblate gives no text direction ({direction!r})")
        surf, reasons, human_all, verified_all, shipped_any = {}, [], True, True, False
        for name, (en, meta) in english.items():
            total = len(en)
            legal_keys = sum(1 for k in en if is_legal(meta.get(k) or {}))
            if code == SOURCE:
                strings, omitted, verified, human, defective = dict(en), Counter(), True, total, set()
            else:
                r = translate_surface(client, cfg, cfg.surfaces[name]["component"], code, en, meta)
                strings, omitted, verified, human, defective = r["strings"], r["omitted"], r["review_verified"], r["human_approved"], r["defective"]
            broken_n = sum(v for k, v in omitted.items() if k.startswith("broken_"))
            # a legal-tier key not shipped is English by design (ADR-0160 s4): outside the
            # coverage denominator and the core test, reported as omitted_legal. A legal key
            # that is broken or stale is a defect, not a missing approval: it still counts.
            legal_out = {k for k in en if is_legal(meta.get(k) or {}) and k not in strings and k not in defective}
            core = {k for k, m in meta.items() if k in en and m.get("core") and k not in legal_out}
            denom = total - len(legal_out)
            cov = round(len(strings) / denom, 4) if denom else 0.0
            core_ok = core <= set(strings)
            why = None
            if code != SOURCE:
                if not strings:
                    why = "no approved strings"
                elif cov < cfg.setting("min_coverage", code, name):
                    why = f"coverage {cov:.2f} below {cfg.setting('min_coverage', code, name)}"
                elif not core_ok:
                    why = f"{len(core - set(strings))} core keys not shipped"
                elif total and broken_n / total > cfg.setting("max_broken_ratio", code, name):
                    why = f"{broken_n} of {total} strings broken (placeholders/tags), above {cfg.setting('max_broken_ratio', code, name)}"
            ok = why is None
            entry = {"published": ok, "approved": len(strings), "total": total, "coverage": cov, "core_ok": core_ok,
                     "omitted_broken": broken_n, "omitted_legal": len(legal_out), "omitted": dict(sorted(omitted.items())),
                     "legal_approved": all(k in strings for k in en if is_legal(meta.get(k) or {})),
                     "legal_keys": legal_keys, "review_verified": verified}
            if not ok:
                entry["reason"] = why
                reasons.append(f"{name}: {why}")
            else:
                shipped_any = True
                files[f"{PUBLISHED}/{name}/{code}.json"] = i18n_map.dumps(strings)
                human_all &= bool(total) and human == len(strings)
                verified_all &= verified
            surf[name] = entry
        if not english:
            reasons.append("no surface has English in this repo yet")
        is_src = code == SOURCE
        human_reviewed = True if is_src else bool(shipped_any and human_all and cfg.human_reviewers)
        locales.append({
            "code": code, "weblate": code, "url": url, "bcp47": bcp47, "dir": direction,
            "english_name": en_name, "native_name": native,
            "published": shipped_any, "reason": "; ".join(reasons) if not shipped_any else "",
            "human_reviewed": human_reviewed, "machine_label": not human_reviewed,
            "review_verified": bool(shipped_any and verified_all),
            "legal_approved": bool(surf) and all(s["legal_approved"] for s in surf.values()),
            "surfaces": nest(surf),
        })
    withdrawn = _withdrawn(previous, locales, cfg)
    doc = {"schema": 1, "generated_at": (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source_commit": source_commit, "locales": locales, "withdrawn": withdrawn}
    return doc, files


def _withdrawn(previous, locales, cfg):
    live = {(l["url"], s) for l in locales for s, v in _flat(l["surfaces"]) if v.get("published")}
    out = {(w["url"], w["surface"]) for w in (cfg.policy.get("seed_withdrawn") or [])}
    if previous:
        out |= {(w["url"], w["surface"]) for w in previous.get("withdrawn") or []}
        for l in previous.get("locales") or []:
            out |= {(l["url"], s) for s, v in _flat(l.get("surfaces") or {}) if v.get("published")}
    return [{"url": u, "surface": s} for u, s in sorted(out - live)]


def _flat(surfaces):
    for name, v in surfaces.items():
        if "published" in v:
            yield name, v
        else:
            for sub, vv in v.items():
                yield f"{name}-{sub}", vv


def same_content(a, b):
    strip = lambda d: {k: v for k, v in d.items() if k not in ("generated_at", "source_commit")}  # noqa: E731
    return strip(a) == strip(b)


def summary(doc):
    lines = []
    for l in doc["locales"]:
        parts = [f"{n} {'LIVE' if v['published'] else 'off'} {v['approved']}/{v['total']}"
                 + (f" omitted_broken={v['omitted_broken']}" if v["omitted_broken"] else "")
                 + (f" omitted_legal={v['omitted_legal']}" if v.get("omitted_legal") else "")
                 for n, v in _flat(l["surfaces"])]
        lines.append(f"{l['code']:8} {l['url']:6} {l['dir']}  {'PUBLISHED' if l['published'] else 'not-published'}  "
                     f"label={'ai' if l['machine_label'] else 'none'} review_verified={l['review_verified']}  "
                     + ("; ".join(parts) or "-") + (f"  [{l['reason']}]" if l["reason"] else ""))
    lines.append(f"withdrawn urls: {len(doc['withdrawn'])}")
    return lines


# --- write / check -------------------------------------------------------------

def git_head(repo):
    r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return r.stdout.decode().strip() or None if r.returncode == 0 else None


def write(repo, doc, files, previous):
    """-> True when anything changed."""
    out_dir = os.path.join(repo, PUBLISHED)
    if previous and same_content(previous, doc):
        doc["generated_at"], doc["source_commit"] = previous.get("generated_at"), previous.get("source_commit")
    texts = dict(files)
    texts[f"{PUBLISHED}/locales.json"] = i18n_map.dumps(doc, sort=False)
    changed = False
    for rel, text in texts.items():
        p = os.path.join(repo, rel)
        if os.path.isfile(p) and open(p, encoding="utf-8").read() == text:
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        changed = True
    if os.path.isdir(out_dir):
        for sub in sorted(os.listdir(out_dir)):
            d = os.path.join(out_dir, sub)
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    rel = f"{PUBLISHED}/{sub}/{fn}"
                    if fn.endswith(".json") and rel not in texts:
                        os.remove(os.path.join(d, fn))
                        changed = True
    return changed


def check(repo, surfaces_file=None):
    """Offline validation of the committed published/ tree -> list of problems."""
    cfg = Config(repo, surfaces_file)
    problems = []
    seen = {}
    for code, spec in cfg.regions.items():
        if spec["url"] in seen:
            problems.append(f"locale-regions: {code} and {seen[spec['url']]} share url {spec['url']}")
        seen[spec["url"]] = code
    path = os.path.join(repo, PUBLISHED, "locales.json")
    if not os.path.isfile(path):
        return problems
    doc = load_json(path)
    if doc.get("schema") != 1 or not isinstance(doc.get("locales"), list):
        return problems + ["published/locales.json: schema must be 1 with a locales list"]
    expected, urls = set(), {}
    english = load_surface_english(repo, cfg.surfaces)
    for l in doc["locales"]:
        if not CODE_RE.fullmatch(str(l.get("weblate"))):
            problems.append(f"locales.json: bad weblate code {l.get('weblate')!r}")
            continue
        for f in ("weblate", "url", "bcp47", "dir", "native_name", "english_name", "machine_label", "human_reviewed", "surfaces"):
            if f not in l:
                problems.append(f"locales.json: {l.get('weblate')}: missing {f}")
        if l.get("dir") not in ("ltr", "rtl"):
            problems.append(f"locales.json: {l.get('weblate')}: dir {l.get('dir')!r}")
        if l.get("machine_label") != (not l.get("human_reviewed")):
            problems.append(f"locales.json: {l.get('weblate')}: machine_label must be the negation of human_reviewed")
        if l.get("url") in urls:
            problems.append(f"locales.json: url {l.get('url')} used by {urls[l['url']]} and {l.get('weblate')}")
        urls[l.get("url")] = l.get("weblate")
        for name, v in _flat(l.get("surfaces") or {}):
            rel = f"{PUBLISHED}/{name}/{l['weblate']}.json"
            if v.get("published"):
                expected.add(rel)
                if name not in english:
                    problems.append(f"{rel}: surface {name} has no English in this repo")
                    continue
                if not os.path.isfile(os.path.join(repo, rel)):
                    problems.append(f"{rel}: published but missing")
                    continue
                en, meta = english[name]
                strings = load_json(os.path.join(repo, rel))
                for k, t in strings.items():
                    if k not in en:
                        problems.append(f"{rel}: key {k!r} is not in English")
                    elif l["weblate"] != SOURCE and broken(en[k], t, meta.get(k) or {}):
                        problems.append(f"{rel}: {k!r} breaks placeholders/tags/empty ({broken(en[k], t, meta.get(k) or {})})")
            elif os.path.exists(os.path.join(repo, rel)):
                problems.append(f"{rel}: not published but a strings file exists")
    d = os.path.join(repo, PUBLISHED)
    for sub in sorted(os.listdir(d)):
        if os.path.isdir(os.path.join(d, sub)):
            for fn in sorted(os.listdir(os.path.join(d, sub))):
                if f"{PUBLISHED}/{sub}/{fn}" not in expected:
                    problems.append(f"{PUBLISHED}/{sub}/{fn}: not listed as published in locales.json")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--url", default=os.environ.get("WEBLATE_URL", DEFAULT_URL))
    ap.add_argument("--surfaces-file", help="alternative surfaces.json (dry-run aid)")
    ap.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    ap.add_argument("--check", action="store_true", help="validate the committed published/ tree offline")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)
    try:
        if a.check:
            problems = check(repo, a.surfaces_file)
            for p in problems:
                print(f"::error::{p}" if os.environ.get("GITHUB_ACTIONS") else p)
            print("published/ ok" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0
        cfg = Config(repo, a.surfaces_file)
        pp = os.path.join(repo, PUBLISHED, "locales.json")
        previous = load_json(pp) if os.path.isfile(pp) else None
        doc, files = compute(repo, cfg, PublicClient(a.url), previous, source_commit=git_head(repo))
        print("\n".join(summary(doc)))
        changed = False
        if not a.dry_run:
            changed = write(repo, doc, files, previous)
            print("published/ changed" if changed else "published/ unchanged")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"changed={'true' if changed else 'false'}\n")
        return 0
    except (PublishError, surfaces_mod.SurfaceError, OSError, ValueError) as e:
        print(f"publish_locales: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
