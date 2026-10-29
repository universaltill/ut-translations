#!/usr/bin/env python3
"""Build the signed-to-be language bundle com.universaltill.language-<code>
from approved translations only (ADR-0160 §4, §5; ut-docs#4367).

  export   every area component's units in state approved, read from the
           Weblate REST API (Weblate can't push to this repo yet). With
           --text-source git the text comes from <area>/<weblate code>.json
           here instead, and ships only if it equals Weblate's approved text.
  review   from the translation's change history: the approval is an
           explicit "Translation approved" (or a suggestion accepted straight
           into approved) by a different account than the one that wrote the
           current text; the approver is, deny by default, an AI account
           listed in config/ai-accounts.json or a member of the
           human-reviewers team (anyone else: unknown_approver); when both
           are AI accounts they are on different models; a `legal` key is
           approved by a human-reviewers member, not a bot, on the current
           English. Team membership is read at build time. Bulk-approved
           strings carry no approval event and are omitted. A translated
           locale fails while config/ai-accounts.json lists no account
           (ut-docs#4366).
  checks   bundle_checks.check_string per string (placeholders, max_length,
           script, nothing dropped). Any failure omits the string — never
           written as English.
  english  for the en bundle (this repo's <area>/en.json is its text): every
           <area>/en.json and meta/<area>.json must equal what
           tools/i18n_map.py renders from universal-till's web/locales/en.json
           + web/i18n-meta/ at meta/source.json's commits (main, and each
           line's release tag). Any difference fails the build.
  scans    universal-till's guard-compliance-claims.sh and
           guard-competitor-naming.sh, read from universal-till at the
           bundle's source_commit (`git show <commit>:<path>`, so the copy is
           pinned whatever the checkout's HEAD), run over the bundle's locale
           file, a normalized one-line-per-string view of it, and the
           manifest. A hit fails the whole bundle. --no-scans skips them (the
           workflow's build job, which holds the Weblate token, runs no
           universal-till code); --scan-bundle runs them over a built bundle
           (the workflow's verify job, which holds no secret).
  commit   universal-till code (the guards, the en files) is only read from a
           commit on universal-till main or a vX.Y.Z tag's commit
           (till_verify.require_released_commit): a fork's commit is
           fetchable by sha.
  package  manifest.json, bundle.json, locales/<code>.json in a
           deterministic tar.gz the till's marketplace installer accepts once
           the marketplace has signed it (tools/till_verify.py proves that).

Version: 2.<YYYYMMDD>.<second of the day> of generated_at (UTC) — above the
1.x packs it replaces, rising with every build, no state to keep.

Env: WEBLATE_URL, WEBLATE_TOKEN (a read token that can see users' groups:
     Weblate's user.view permission), not needed for --locale en.

Exit: 0 built (report.json says whether it is publishable) or scans passed,
      1 a scan or build failure, 2 usage, 3 built but not publishable and
      --require-publishable was given.
"""

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundle_checks  # noqa: E402
import i18n_map  # noqa: E402
import sync_english  # noqa: E402
import till_verify  # noqa: E402
from provision_weblate import WeblateClient, WeblateError  # noqa: E402

ID_PREFIX = "com.universaltill.language-"
LEGAL = "legal"
STATE_APPROVED = 30
STATE_READONLY = 100
GUARDS = ("scripts/ci/guard-compliance-claims.sh", "scripts/ci/guard-competitor-naming.sh")
LOCALE_RE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*")
WEBLATE_CODE_RE = re.compile(r"[A-Za-z]{2,3}(?:[_@-][A-Za-z0-9]{2,8})*")
SHA_RE = re.compile(r"[0-9a-f]{40}")

# Weblate change actions (weblate/trans/actions.py, ActionEvents).
A_CHANGE, A_NEW, A_AUTO, A_ACCEPT, A_REVERT, A_UPLOAD = 2, 5, 6, 7, 8, 9
A_REPLACE, A_SOURCE_CHANGE, A_BULK_EDIT, A_APPROVE, A_MARKED_EDIT = 27, 30, 32, 36, 37
A_REPO_UPDATE, A_UPLOAD_UPDATE, A_PROPAGATED, A_USER_REVERT = 59, 72, 77, 94
# Actions that can set a translation's text (Weblate's ACTIONS_CONTENT minus
# the metadata-only ones).
TEXT_ACTIONS = {A_CHANGE, A_NEW, A_AUTO, A_ACCEPT, A_REVERT, A_UPLOAD, A_REPLACE, A_BULK_EDIT,
                A_APPROVE, A_REPO_UPDATE, A_UPLOAD_UPDATE, A_PROPAGATED, A_USER_REVERT}
# After these the text needs a fresh approval whatever the unit state says.
INVALIDATING = {A_SOURCE_CHANGE, A_MARKED_EDIT}


class BundleError(Exception):
    pass


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def username(url):
    """`.../api/users/<username>/` -> username."""
    if not url:
        return None
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if len(parts) >= 3 and parts[-2] == "users":
        return urllib.parse.unquote(parts[-1])
    return None


def unit_id(url):
    parts = [p for p in urllib.parse.urlparse(url or "").path.split("/") if p]
    if len(parts) >= 2 and parts[-2] == "units" and parts[-1].isdigit():
        return int(parts[-1])
    return None


def first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v if isinstance(v, str) else ""


class Accounts:
    """Who an account is: AI (and on which model), bot, human reviewer."""

    def __init__(self, client, team, ai_accounts):
        self.c = client
        self.team = team
        self.ai = ai_accounts
        self._users = {}
        self._team_id = None

    def team_id(self):
        if self._team_id is None:
            groups = self.c.list("/api/groups/") or []
            ids = [g.get("id") for g in groups if g.get("name") == self.team]
            if len(ids) != 1:
                raise BundleError(f"Weblate team {self.team!r} not found exactly once (found {len(ids)})")
            self._team_id = ids[0]
        return self._team_id

    def user(self, name):
        if name not in self._users:
            u = self.c.get(f"/api/users/{urllib.parse.quote(name)}/")
            if u is None:
                raise BundleError(f"Weblate user {name!r} not found")
            if "groups" not in u or "is_bot" not in u:
                raise BundleError("the Weblate token can't see users' groups and bot flag (needs user.view): "
                                  "legal and AI-review rules can't be enforced")
            self._users[name] = u
        return self._users[name]

    def is_ai(self, name):
        return name in self.ai or bool(self.user(name).get("is_bot"))

    def model(self, name):
        return (self.ai.get(name) or {}).get("model")

    def in_team(self, name):
        tid = self.team_id()
        for g in self.user(name).get("groups") or []:
            gid = g.get("id") if isinstance(g, dict) else None
            if gid is None:
                parts = [p for p in urllib.parse.urlparse(str(g)).path.split("/") if p]
                gid = int(parts[-1]) if parts and parts[-1].isdigit() else None
            if gid == tid:
                return True
        return False

    def is_human_reviewer(self, name):
        return not self.is_ai(name) and self.in_team(name)


def review_verdict(target, history, legal, accounts):
    """None when the current text carries a valid approval, else the omission
    reason. `history` is the unit's changes, oldest first."""
    text, author, approver = None, None, None
    for ch in history:
        action = ch.get("action")
        if action in INVALIDATING:
            approver = None
            continue
        tgt = ch.get("target")
        if action in TEXT_ACTIONS and isinstance(tgt, str) and tgt != text:
            text = tgt
            author = username(ch.get("author")) or username(ch.get("user"))
            approver = None
        is_approval = action == A_APPROVE or (
            action == A_ACCEPT and (ch.get("details") or {}).get("state") == STATE_APPROVED)
        if is_approval and tgt == text:
            approver = username(ch.get("user"))
    if text != target:
        return "no_history"  # the current text has no recorded author
    if not approver:
        return "no_approval"
    if not author:
        return "no_author"
    if approver == author:
        return "self_approved"
    if accounts.is_ai(approver):
        if approver not in accounts.ai:
            return "unknown_ai_account"
        if accounts.is_ai(author):
            if author not in accounts.ai:
                return "unknown_ai_account"
            if accounts.model(author) == accounts.model(approver):
                return "same_model"
    if legal and not accounts.is_human_reviewer(approver):
        return "legal_needs_human"
    # Deny by default: a listed AI account, or a human-reviewers member.
    if approver not in accounts.ai and not accounts.is_human_reviewer(approver):
        return "unknown_approver"
    return None


def unit_labels(unit):
    out = set()
    for lab in unit.get("labels") or []:
        out.add(lab.get("name") if isinstance(lab, dict) else lab)
    return out


class Builder:
    def __init__(self, repo, locale, client=None, till=None, text_source="api", now=None, scans=True):
        self.repo = repo
        self.cfg = load_json(os.path.join(repo, "config", "bundles.json"))
        ai = load_json(os.path.join(repo, "config", "ai-accounts.json")).get("accounts") or {}
        if not LOCALE_RE.fullmatch(locale or ""):
            raise BundleError(f"locale {locale!r} must match ^[a-z]{{2,3}}(-[a-z0-9]{{2,8}})*$")
        spec = self.cfg["locales"].get(locale)
        if spec is None:
            raise BundleError(f"locale {locale!r} is not in config/bundles.json")
        if not WEBLATE_CODE_RE.fullmatch(spec.get("weblate", "")):
            raise BundleError(f"bad Weblate code for {locale}: {spec.get('weblate')!r}")
        if text_source not in ("api", "git"):
            raise BundleError("text_source must be api or git")
        self.locale, self.spec = locale, spec
        self.client, self.till, self.text_source, self.scans = client, till, text_source, scans
        self.ai = ai
        self.accounts = Accounts(client, self.cfg["human_reviewers_team"], ai) if client else None
        self.now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)
        self.strings = {}
        self.omitted = Counter()
        self.details = {}
        self.key_count = 0

    def omit(self, key, reason, detail=""):
        self.omitted[reason] += 1
        self.details[key] = f"{reason}: {detail}" if detail else reason

    # --- export ------------------------------------------------------------

    def areas(self):
        areas = load_json(os.path.join(self.repo, "meta", "areas.json"))
        for a in areas:
            i18n_map.validate_area_id(a)
        return list(areas)

    def export(self):
        if not self.spec.get("source") and not self.ai:
            raise BundleError("config/ai-accounts.json lists no AI accounts: a translated bundle is not built "
                              "until ut-docs#4366 has listed the Weblate AI accounts and their models")
        for area in self.areas():
            en = load_json(os.path.join(self.repo, area, "en.json"))
            meta = load_json(os.path.join(self.repo, "meta", f"{area}.json"))
            self.key_count += len(en)
            if self.spec.get("source"):
                for key, text in en.items():
                    self.accept(key, text, text, meta.get(key) or {})
                continue
            self.export_area(area, en, meta)

    def export_area(self, area, en, meta):
        if self.client is None:
            raise BundleError("WEBLATE_URL and WEBLATE_TOKEN are needed for a translated locale")
        project, wl = self.cfg["project"], self.spec["weblate"]
        base = f"/api/translations/{project}/{area}/{wl}"
        units = self.client.list(f"{base}/units/?q=state%3Aapproved&page_size=1000")
        if units is None:
            units = []  # the language doesn't exist in this component yet
        approved = [u for u in units if STATE_APPROVED <= (u.get("state") or 0) < STATE_READONLY]
        seen = set()
        git_text = None
        if self.text_source == "git":
            p = os.path.join(self.repo, area, f"{wl}.json")
            git_text = load_json(p) if os.path.exists(p) else {}
        history = {}
        if approved:
            for ch in self.client.list(f"{base}/changes/?page_size=1000") or []:
                uid = unit_id(ch.get("unit"))
                if uid is not None:
                    history.setdefault(uid, []).append(ch)
            for chs in history.values():
                chs.sort(key=lambda c: (c.get("timestamp") or "", c.get("id") or 0))
        for u in approved:
            key = u.get("context")
            if key not in en:
                self.omit(f"{area}:{key}", "unknown_key", "not in this area's English (union of supported lines)")
                continue
            seen.add(key)
            target = first(u.get("target"))
            if first(u.get("source")) != en[key] or first(u.get("previous_source")):
                self.omit(key, "stale_source", "approved against different English")
                continue
            if git_text is not None and git_text.get(key) != target:
                self.omit(key, "git_mismatch", "git text differs from Weblate's approved text")
                continue
            m = meta.get(key) or {}
            legal = LEGAL in (m.get("labels") or []) or LEGAL in unit_labels(u)
            why = review_verdict(target, history.get(u.get("id"), []), legal, self.accounts)
            if why:
                self.omit(key, why)
                continue
            self.accept(key, en[key], target, m)
        self.omitted["not_approved"] += len(set(en) - seen)

    def accept(self, key, english, target, meta):
        bad = bundle_checks.check_string(english, target, meta, self.spec["script"])
        if bad:
            self.omit(key, *bad)
            return
        self.strings[key] = target

    # --- bundle ------------------------------------------------------------

    def version(self):
        n = self.now
        return f"2.{n:%Y%m%d}.{n.hour * 3600 + n.minute * 60 + n.second}"

    def source(self):
        return read_source(self.repo)[:2]

    # --- English (the en bundle) -------------------------------------------

    def check_english(self, commit):
        """<area>/en.json and meta/<area>.json as written here (the en
        bundle's text; every locale's English, flags and legal labels) must
        be exactly what sync_english.py renders from universal-till."""
        till = require_till(self.till)
        require_released(till, commit)
        _, _, src = read_source(self.repo)
        with tempfile.TemporaryDirectory(prefix="utt-en-") as home:
            def show(ref, path):
                r = subprocess.run(["git", "-C", till, "show", f"{ref}:{path}"], env=scrubbed_env(home),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if r.returncode != 0:
                    return None
                try:
                    return json.loads(r.stdout)
                except ValueError as e:
                    raise BundleError(f"{ref}:{path} is not JSON: {e}") from None

            def tag_commit(tag):
                r = subprocess.run(["git", "-C", till, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
                                   env=scrubbed_env(home), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                return r.stdout.decode().strip() if r.returncode == 0 else None

            en, areas = show(commit, sync_english.EN_PATH), show(commit, sync_english.AREAS_PATH)
            if not isinstance(en, dict) or not isinstance(areas, dict):
                raise BundleError(f"universal-till {commit} has no {sync_english.EN_PATH} / {sync_english.AREAS_PATH}")
            lines = [i18n_map.Line("main", en, show(commit, sync_english.META_PATH) or {})]
            for line in sorted(src.get("lines") or {}, key=sync_english._line_key, reverse=True):
                spec = src["lines"][line] or {}
                tag, c = spec.get("tag", ""), spec.get("commit", "")
                if not sync_english.TAG_RE.fullmatch(tag) or not SHA_RE.fullmatch(c):
                    raise BundleError(f"meta/source.json line {line}: bad tag/commit {tag!r} {c!r}")
                got = tag_commit(tag)
                if got != c:
                    raise BundleError(f"meta/source.json line {line}: {c} is not {tag}'s commit "
                                      f"({got or 'no such tag in the till checkout'})")
                line_en = show(c, sync_english.EN_PATH)
                if not isinstance(line_en, dict):
                    raise BundleError(f"{tag} has no {sync_english.EN_PATH}")
                lines.append(i18n_map.Line(line, line_en, show(c, sync_english.META_PATH) or {}))
        try:
            want = i18n_map.render(i18n_map.build_union(lines), areas)
        except ValueError as e:
            raise BundleError(f"universal-till {commit}: {e}") from None
        differ = []
        for rel, text in sorted(want.items()):
            if rel == "meta/areas.json":
                continue
            path = os.path.join(self.repo, rel)
            if not os.path.isfile(path) or load_json(path) != json.loads(text):
                differ.append(rel)
        rendered = {rel.split("/")[0] for rel in want if rel.endswith("/en.json")}
        if set(self.areas()) != rendered:
            differ.append("meta/areas.json (the area list)")
        if differ:
            raise BundleError("the en bundle ships this repo's English, which differs from universal-till's "
                              f"web/locales/en.json + web/i18n-meta/ at {commit[:12]} (and the supported lines' "
                              "tags) in: " + ", ".join(differ) + " — run tools/sync_english.py")

    def manifest(self, lines):
        name = self.spec["name"]
        return {
            "id": ID_PREFIX + self.locale,
            "name": name,
            "version": self.version(),
            "description": f"{name} language bundle for Universal Till, built from approved "
                           "translations; strings not approved yet show in English.",
            "author": "Universal Till",
            "website": "https://universaltill.com",
            "canonical_type": "language",
            "runtime": "none",
            "device_arch": "any",
            "min_pos_version": f"{lines[0]}.0" if lines else "1.0.0",
            "permissions": ["ui.locale"],
            "locales": [self.locale],
        }

    def bundle_info(self, commit, lines):
        return {
            "locale": self.locale,
            "pos_lines": lines,
            "source_commit": commit,
            "generated_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "key_count": self.key_count,
            "approved_count": len(self.strings),
            "omitted": dict(sorted(self.omitted.items())),
        }

    def publish_problems(self, info):
        problems = []
        if info["approved_count"] == 0:
            problems.append("no approved strings: an empty bundle is never published")
        floor = self.spec.get("min_coverage")
        if floor and info["key_count"] and info["approved_count"] < floor * info["key_count"]:
            problems.append(
                f"{info['approved_count']}/{info['key_count']} strings is below min_coverage {floor}: "
                f"this bundle would replace the installed {ID_PREFIX}{self.locale} pack (ut-docs#4371)")
        return problems

    # --- scans -------------------------------------------------------------

    def run_guards(self, commit, locale_text, manifest_text):
        run_guards(self.till, commit, self.locale, self.strings, locale_text, manifest_text)

    # --- package -----------------------------------------------------------

    def build(self, out_dir):
        commit, lines = self.source()
        if self.scans:
            require_till(self.till)
        # Every locale, not only en (review HIGH-1): <area>/en.json and
        # meta/<area>.json are un-owned so the sync PR can auto-merge, so
        # the legal labels and English a translated bundle is judged by must
        # equal what universal-till renders at source_commit.
        self.check_english(commit)
        self.export()
        manifest = self.manifest(lines)
        info = self.bundle_info(commit, lines)
        files = {
            "manifest.json": i18n_map.dumps(manifest, sort=False),
            "bundle.json": i18n_map.dumps(info, sort=False),
            f"locales/{self.locale}.json": i18n_map.dumps(self.strings),
        }
        if self.scans:
            self.run_guards(commit, files[f"locales/{self.locale}.json"], files["manifest.json"])
        os.makedirs(out_dir, exist_ok=True)
        name = f"{manifest['id']}_{manifest['version']}_universal.tar.gz"
        path = os.path.join(out_dir, name)
        data = deterministic_targz(files, int(self.now.timestamp()))
        with open(path, "wb") as f:
            f.write(data)
        sha = hashlib.sha256(data).hexdigest()
        with open(path + ".sha256", "w") as f:
            f.write(f"{sha}  {name}\n")
        problems = self.publish_problems(info)
        report = {
            "bundle": name, "sha256": sha, "plugin_id": manifest["id"], "version": manifest["version"],
            "locale": self.locale, "human_spot_check": bool(self.spec.get("human_spot_check")),
            "publishable": not problems, "problems": problems, "bundle_json": info,
            "omitted": dict(sorted(self.details.items())),
        }
        with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
            f.write(i18n_map.dumps(report, sort=False))
        with open(os.path.join(out_dir, "review.md"), "w", encoding="utf-8") as f:
            f.write(self.review_md(report))
        return report

    def review_md(self, report):
        info = report["bundle_json"]
        rows = [f"# {report['plugin_id']} {report['version']}", "",
                f"- source_commit `{info['source_commit']}`, lines {', '.join(info['pos_lines']) or '-'}",
                f"- {info['approved_count']} of {info['key_count']} strings; omitted: "
                + (", ".join(f"{k} {v}" for k, v in info["omitted"].items()) or "none"),
                f"- publishable: {'yes' if report['publishable'] else 'no — ' + '; '.join(report['problems'])}",
                "", "| key | translation |", "|---|---|"]
        for key, text in sorted(self.strings.items()):
            cell = text.replace("|", "\\|").replace("\n", " ")
            rows.append(f"| `{key}` | {cell} |")
        return "\n".join(rows) + "\n"


def read_source(repo):
    """meta/source.json -> (main commit, supported lines oldest first, raw)."""
    src = load_json(os.path.join(repo, "meta", "source.json"))
    commit = (src.get("main") or {}).get("commit", "")
    if not SHA_RE.fullmatch(commit):
        raise BundleError(f"meta/source.json main.commit {commit!r} is not a full sha")
    lines = sorted(src.get("lines") or {}, key=lambda v: [int(x) for x in v.split(".")])
    return commit, lines, src


def require_till(till):
    if not till:
        raise BundleError("--till-source (a universal-till checkout holding source_commit) is required "
                          "for the compliance and naming scans and the en check")
    return till


def require_released(till, commit):
    try:
        return till_verify.require_released_commit(till, commit)
    except till_verify.VerifyError as e:
        raise BundleError(str(e)) from None


def run_guards(till, commit, locale, strings, locale_text, manifest_text):
    """universal-till's compliance and naming guards at `commit`, over one
    bundle's strings and manifest. Raises BundleError on a hit."""
    require_till(till)
    require_released(till, commit)
    with tempfile.TemporaryDirectory(prefix="utt-scan-") as tmp:
        for rel in GUARDS:
            r = subprocess.run(["git", "-C", till, "show", f"{commit}:{rel}"], env=scrubbed_env(tmp),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode != 0:
                raise BundleError(f"can't read {rel} at {commit}: {r.stderr.decode(errors='replace').strip()}")
            dest = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(r.stdout)
        dirs = {d: os.path.join(tmp, "scan", d) for d in ("locales", "help", "ui", "plugins")}
        for d in dirs.values():
            os.makedirs(d)
        with open(os.path.join(dirs["locales"], f"{locale}.json"), "w", encoding="utf-8") as f:
            f.write(locale_text)
        with open(os.path.join(dirs["locales"], f"{locale}.lines.json"), "w", encoding="utf-8") as f:
            for key, text in sorted(strings.items()):
                f.write(f"{key}: {bundle_checks.scan_line(text)}\n")
        # Both guards fail closed on an empty surface; these two are empty.
        open(os.path.join(dirs["help"], "none.md"), "w").close()
        open(os.path.join(dirs["ui"], "none.html"), "w").close()
        os.makedirs(os.path.join(dirs["plugins"], "bundle"))
        with open(os.path.join(dirs["plugins"], "bundle", "plugin.json"), "w", encoding="utf-8") as f:
            f.write(manifest_text)
        failures = []
        for rel in GUARDS:
            args = [dirs["locales"], dirs["help"], dirs["ui"]]
            if "competitor" in rel:
                args.append(dirs["plugins"])
            # A scrubbed environment: this is another repo's script, and a
            # local run's env may hold the Weblate token (the workflow runs
            # the scans in its verify job, which holds no secret).
            r = subprocess.run(["bash", os.path.join(tmp, rel), *args], env=scrubbed_env(tmp),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = r.stdout.decode(errors="replace").replace(tmp + "/", "")
            if r.returncode != 0:
                failures.append(f"{os.path.basename(rel)} failed:\n{out.strip()}")
        if failures:
            raise BundleError("compliance/naming scan hit — the bundle is not published until a human "
                              "fixes the string in Weblate:\n" + "\n".join(failures))


def _member(tf, name):
    try:
        f = tf.extractfile(name)
    except KeyError:
        raise BundleError(f"bundle has no {name}") from None
    if f is None:
        raise BundleError(f"bundle {name} is not a regular file")
    return f.read().decode("utf-8")


def scan_bundle(repo, till, path, locale):
    """Run the guards over a bundle another job built: its id must be this
    locale's and its source_commit meta/source.json's."""
    if not LOCALE_RE.fullmatch(locale or ""):
        raise BundleError(f"bad locale {locale!r}")
    commit = read_source(repo)[0]
    with tarfile.open(path) as tf:
        manifest_text = _member(tf, "manifest.json")
        manifest = json.loads(manifest_text)
        if manifest.get("id") != ID_PREFIX + locale:
            raise BundleError(f"bundle id {manifest.get('id')!r} is not {ID_PREFIX}{locale}")
        info = json.loads(_member(tf, "bundle.json"))
        if info.get("source_commit") != commit:
            raise BundleError(f"bundle.json source_commit {info.get('source_commit')!r} is not "
                              f"meta/source.json's main.commit {commit}")
        locale_text = _member(tf, f"locales/{locale}.json")
    strings = json.loads(locale_text)
    if not isinstance(strings, dict) or not all(isinstance(v, str) for v in strings.values()):
        raise BundleError(f"locales/{locale}.json is not a flat key -> string map")
    run_guards(till, commit, locale, strings, locale_text, manifest_text)


def scrubbed_env(home):
    """PATH only (plus a throwaway HOME): no token reaches a child process."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home}


def deterministic_targz(files, mtime):
    """Same bytes for the same input: sorted entries, fixed owner/mode/time,
    no gzip filename. Regular files only, no ./ prefix (the till's importer
    rejects it), no AppleDouble junk (the marketplace hygiene gate)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for name in sorted(files):
            data = files[name].encode("utf-8")
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode = len(data), mtime, 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=mtime) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def write_outputs(report, out_dir):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"bundle={os.path.join(out_dir, report['bundle'])}\n")
        f.write(f"sha256={report['sha256']}\n")
        f.write(f"version={report['version']}\n")
        # report.json is handed to the publish job too: pinned like the bundle.
        with open(os.path.join(out_dir, "report.json"), "rb") as r:
            f.write(f"report_sha256={hashlib.sha256(r.read()).hexdigest()}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a language bundle from approved translations.")
    ap.add_argument("--locale", required=True)
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--till-source", required=True, help="universal-till git checkout holding source_commit")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--text-source", choices=("api", "git"), default="api")
    ap.add_argument("--require-publishable", action="store_true")
    ap.add_argument("--no-scans", action="store_true",
                    help="skip universal-till's guards (run them later with --scan-bundle)")
    ap.add_argument("--scan-bundle", metavar="PATH",
                    help="only run universal-till's guards over this built bundle")
    args = ap.parse_args(argv)
    if args.scan_bundle:
        try:
            scan_bundle(args.repo, args.till_source, args.scan_bundle, args.locale)
        except (BundleError, OSError, ValueError, tarfile.TarError) as e:
            print(f"bundle scan failed: {e}", file=sys.stderr)
            return 1
        print(f"compliance and naming scans passed: {args.scan_bundle}")
        return 0
    try:
        client = None
        url, token = os.environ.get("WEBLATE_URL"), os.environ.get("WEBLATE_TOKEN")
        if url and token:
            client = WeblateClient(url, token)
        report = Builder(args.repo, args.locale, client, args.till_source, args.text_source,
                         scans=not args.no_scans).build(args.out)
    except (BundleError, WeblateError, OSError, ValueError, KeyError) as e:
        print(f"bundle build failed: {e}", file=sys.stderr)
        return 1
    info = report["bundle_json"]
    print(f"built {report['bundle']} sha256 {report['sha256']}: {info['approved_count']}/{info['key_count']} "
          f"strings; omitted {info['omitted']}")
    for p in report["problems"]:
        print(f"not publishable: {p}", file=sys.stderr)
    write_outputs(report, args.out)
    if args.require_publishable and not report["publishable"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
