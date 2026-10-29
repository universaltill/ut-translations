"""Shared fixtures for the bundle tests: a fake Weblate that serves units,
change history, groups and users (a real HTTP server on 127.0.0.1, pages of
2 so pagination is real), a fixture repo, and a throwaway universal-till git
repo holding copies of the real compliance and naming guards."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import parse_qs, urlparse

from test_provision import TOKEN, serve  # noqa: F401  (re-exported)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
GUARDS = ("scripts/ci/guard-compliance-claims.sh", "scripts/ci/guard-competitor-naming.sh")
PROJECT = "universal-till"
TEAM_ID = 7


def till_source():
    """A universal-till checkout to take the real guards (and, for the
    verifier test, the real installer) from: $UTT_TILL_SOURCE, else the
    sibling checkout. None when neither exists."""
    p = os.environ.get("UTT_TILL_SOURCE") or os.path.join(REPO, "..", "universal-till")
    return os.path.abspath(p) if os.path.isdir(os.path.join(p, ".git")) or os.path.isfile(os.path.join(p, ".git")) else None


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", *args], cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return r.stdout.decode().strip()


def guard_text(rel):
    src = till_source()
    if src is None:
        raise RuntimeError("no universal-till checkout: set UTT_TILL_SOURCE")
    return subprocess.run(["git", "-C", src, "show", f"HEAD:{rel}"], stdout=subprocess.PIPE, check=True).stdout


def till_english():
    """The English the fixture repo ships, as universal-till holds it (the
    repo's files are synced from it, so the English equality check holds)."""
    return ({**SALE_EN, **LEGAL_EN}, {"pos.short": {"max_length": 6}}, TILL_AREAS)


def make_till(root, edit=None):
    """A git repo with the two guards at commit 1 -> (path, sha1). `edit`
    (bytes -> bytes) is applied to the compliance guard in a second commit
    on top, so HEAD differs from the pinned commit."""
    path = os.path.join(root, "till")
    os.makedirs(os.path.join(path, "scripts", "ci"))
    git(path, "init", "-q", "-b", "main")
    en, meta, areas = till_english()
    for rel, data in (("web/locales/en.json", en), ("web/i18n-meta/en.meta.json", meta),
                      ("web/i18n-meta/areas.json", areas)):
        write(os.path.join(path, rel), data)
    for rel in GUARDS:
        with open(os.path.join(path, rel), "wb") as f:
            f.write(guard_text(rel))
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "guards")
    sha = git(path, "rev-parse", "HEAD")
    git(path, "tag", "v0.34.7")
    if edit:
        p = os.path.join(path, GUARDS[0])
        with open(p, "rb") as f:
            data = edit(f.read())
        with open(p, "wb") as f:
            f.write(data)
        git(path, "commit", "-q", "-am", "edit")
    publish_main(path)
    return path, sha


def publish_main(path):
    """What `git fetch origin main` leaves in a real checkout: HEAD is
    universal-till's main."""
    git(path, "update-ref", "refs/remotes/origin/main", "HEAD")


def side_commit(path, name="side"):
    """A commit that is not on main (a fork's commit, fetchable by sha) ->
    its sha. HEAD and main are left where they were."""
    head = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "-q", "-b", name)
    with open(os.path.join(path, "fork.txt"), "w") as f:
        f.write("not on main\n")
    git(path, "add", "fork.txt")
    git(path, "commit", "-q", "-m", "fork")
    sha = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "-q", head)
    git(path, "branch", "-q", "-D", name)
    return sha


TILL_EN = {"pos.total": "Total", "pos.items": "%d items", "receipt.vat": "VAT included"}
TILL_META = {"pos.items": {"description": "Item count", "placeholders": ["number of items"]}}
TILL_AREAS = {"areas": {"sale": {"namespaces": ["pos"]}, "receipts-and-legal": {"namespaces": ["receipt"]}},
              "default_area": "common", "legal_namespaces": ["receipt"]}


def add_english(till, en=None, meta=None, areas=None, tag="v0.34.7"):
    """Commit universal-till's English files on main, tag the commit as the
    supported line's release -> its sha."""
    for rel, data in (("web/locales/en.json", en or TILL_EN), ("web/i18n-meta/en.meta.json", meta or TILL_META),
                      ("web/i18n-meta/areas.json", areas or TILL_AREAS)):
        write(os.path.join(till, rel), data)
    git(till, "add", "web")
    git(till, "commit", "-q", "-m", "english")
    if tag:
        git(till, "tag", tag)
    publish_main(till)
    return git(till, "rev-parse", "HEAD")


def sync_repo(repo, till):
    """The repo's area files and meta/source.json as tools/sync_english.py
    writes them from `till` (main + line 0.34)."""
    import sync_english
    sync_english.sync(repo, {"source": till, "supported_lines": ["0.34"], "branch": "main"},
                      commit=False, log=lambda *a: None)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


SALE_EN = {
    "pos.total": "Total",
    "pos.items": "%d items",
    "pos.short": "Pay",
    "pos.link": 'See <a href="/help/sale">help</a>',
    "pos.name": "Hello {name}",
}
SALE_META = {
    "pos.total": {"explanation": "", "flags": "", "labels": [], "lines": ["main"]},
    "pos.items": {"explanation": "", "flags": 'placeholders:"%d"', "labels": [], "lines": ["main"]},
    "pos.short": {"explanation": "", "flags": "max-length:6", "labels": [], "lines": ["main"]},
    "pos.link": {"explanation": "", "flags": "", "labels": [], "lines": ["main"]},
    "pos.name": {"explanation": "", "flags": 'placeholders:"{name}"', "labels": [], "lines": ["main"]},
}
LEGAL_EN = {"receipt.vat": "VAT included", "receipt.thanks": "Thank you"}
LEGAL_META = {k: {"explanation": "", "flags": "", "labels": ["legal"], "lines": ["main"]} for k in LEGAL_EN}


def make_repo(root, till_sha, ai_accounts=None):
    r = os.path.join(root, "repo")
    os.makedirs(os.path.join(r, "config"))
    till = os.path.join(root, "till")
    shutil.copyfile(os.path.join(REPO, "config", "bundles.json"), os.path.join(r, "config", "bundles.json"))
    write(f"{r}/config/ai-accounts.json", {"accounts": ai_accounts or {}})
    # Synced from the till, as the sync job writes them; source.json then
    # pins main to the guards commit (the till may hold a later commit).
    sync_repo(r, till)
    src = os.path.join(r, "meta", "source.json")
    with open(src, encoding="utf-8") as f:
        doc = json.load(f)
    doc["main"]["commit"] = till_sha
    write(src, doc)
    return r


class FakeWeblate:
    """Units, changes, groups and users of Weblate's REST API (2026.10
    shapes: users/authors as /api/users/<name>/ URLs, units as
    /api/units/<id>/ URLs, target/source as lists)."""

    PAGE = 2

    def __init__(self):
        self.units = {}     # (area, lang) -> [unit]
        self.changes = {}   # (area, lang) -> [change]
        self.users = {}     # name -> user
        self.groups = [{"id": TEAM_ID, "name": "human-reviewers"}, {"id": 1, "name": "Users"}]
        self.hide_groups = False
        self.requests = []
        self.next_id = 1000
        self.ts = 0

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def user_url(self, name):
        return f"{self.base()}/api/users/{name}/"

    def add_user(self, name, team=False, bot=False):
        groups = [f"{self.base()}/api/groups/1/"] + ([f"{self.base()}/api/groups/{TEAM_ID}/"] if team else [])
        self.users[name] = {"username": name, "full_name": name, "is_bot": bot, "groups": groups}

    def add_unit(self, area, lang, key, source, target, state=30, history=(), previous_source="", labels=()):
        """history: (action, user, target[, author[, details]]) oldest first."""
        self.next_id += 1
        uid = self.next_id
        self.units.setdefault((area, lang), []).append({
            "id": uid, "context": key, "source": [source], "target": [target], "state": state,
            "previous_source": previous_source, "labels": [{"id": 1, "name": n} for n in labels],
        })
        for h in history:
            action, user, tgt = h[0], h[1], h[2]
            author = h[3] if len(h) > 3 else user
            details = h[4] if len(h) > 4 else {}
            self.ts += 1
            self.changes.setdefault((area, lang), []).append({
                "id": self.ts, "unit": f"{self.base()}/api/units/{uid}/", "action": action,
                "user": self.user_url(user) if user else None,
                "author": self.user_url(author) if author else None,
                "target": tgt, "old": "", "details": details,
                "timestamp": f"2026-10-16T10:{self.ts // 60:02d}:{self.ts % 60:02d}Z",
            })
        return uid

    def page(self, items, q, path):
        n = int(q.get("page", ["1"])[0])
        chunk = items[(n - 1) * self.PAGE: n * self.PAGE]
        keep = "&".join(f"{k}={v[0]}" for k, v in q.items() if k != "page")
        nxt = f"{self.base()}{path}?{keep}&page={n + 1}" if n * self.PAGE < len(items) else None
        return 200, {"count": len(items), "next": nxt, "results": chunk}

    def handle(self, method, raw_path, body):
        u = urlparse(raw_path)
        path, q = u.path, parse_qs(u.query)
        self.requests.append((method, raw_path))
        if method != "GET":
            return 405, {"detail": "read only"}
        if (m := re.fullmatch(rf"/api/translations/{PROJECT}/([^/]+)/([^/]+)/units/", path)):
            key = (m.group(1), m.group(2))
            if key not in self.units:
                return 404, {"detail": "Not found."}
            items = self.units[key]
            if q.get("q") == ["state:approved"]:
                items = [x for x in items if x["state"] >= 30]
            return self.page(items, q, path)
        if (m := re.fullmatch(rf"/api/translations/{PROJECT}/([^/]+)/([^/]+)/changes/", path)):
            key = (m.group(1), m.group(2))
            if key not in self.units:
                return 404, {"detail": "Not found."}
            # Weblate lists newest first.
            return self.page(list(reversed(self.changes.get(key, []))), q, path)
        if path == "/api/groups/":
            return self.page(self.groups, q, path)
        if (m := re.fullmatch(r"/api/users/([^/]+)/", path)):
            usr = self.users.get(m.group(1))
            if usr is None:
                return 404, {"detail": "Not found."}
            if self.hide_groups:
                return 200, {"username": usr["username"], "full_name": usr["full_name"]}
            return 200, usr
        return 404, {"detail": f"no fake route {method} {path}"}


class BundleCase:
    """Mixin: tmp dir, fake Weblate, fixture repo, throwaway till."""

    def make(self, ai_accounts=None, till_edit=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.till, self.sha = make_till(self.tmp.name, till_edit)
        self.repo = make_repo(self.tmp.name, self.sha, ai_accounts)
        self.out = os.path.join(self.tmp.name, "dist")
        self.fake = FakeWeblate()
        self.srv = serve(self.fake)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        # bob and rita are in human-reviewers; carol is a Weblate user in no
        # reviewing role (approvals are deny-by-default).
        for name, team, bot in (("alice", False, False), ("bob", True, False), ("rita", True, False),
                                ("carol", False, False),
                                ("ai-tr-opus", False, True), ("ai-rv-qwen", False, True),
                                ("ai-rv-opus", False, True), ("ai-unknown", False, True)):
            self.fake.add_user(name, team=team, bot=bot)
