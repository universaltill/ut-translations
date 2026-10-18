#!/usr/bin/env python3
"""Create or update the Weblate project and one component per area from this
repo's files (ADR-0160 §1-§4; ut-docs#4365). Idempotent: a second run with
nothing changed makes no write call.

  project   universal-till, dedicated reviewers on (translation_review)
  label     `legal` (receipts, fiscal, tax... need a human approver, §4)
  component one per area in meta/areas.json; `common` owns the git clone,
            the others link to it (weblate://universal-till/common) so
            Weblate keeps one checkout of this repo.
            Monolingual JSON: template <area>/en.json, files <area>/*.json,
            English is read-only in Weblate (edited only in universal-till).
            Cleanup add-on drops keys that left the template.
  units     explanation, extra_flags and the `legal` label of every English
            source string, from meta/<area>.json. Other labels people put on
            a string are kept.

A component Weblate hasn't finished importing yet has no units; it is listed
as pending and the next run fills it in.

Env:
  WEBLATE_URL       e.g. https://translate.universaltill.com (https only,
                    plain http only to loopback for tests)
  WEBLATE_TOKEN     API token of a project admin (never committed; pass it
                    from the environment of the person or job running this)
  WEBLATE_REPO_URL  pull URL (default: public https clone of this repo)
  WEBLATE_PUSH_URL  push URL (default: SSH; Weblate's deploy key)

Usage:
  WEBLATE_URL=... WEBLATE_TOKEN=... python3 tools/provision_weblate.py [--dry-run]
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n_map  # noqa: E402

DEFAULT_PROJECT = "universal-till"
DEFAULT_REPO_URL = "https://github.com/universaltill/ut-translations.git"
DEFAULT_PUSH_URL = "git@github.com:universaltill/ut-translations.git"
REPO_OWNER = "common"
LEGAL_LABEL = "legal"
CLEANUP_ADDON = "weblate.cleanup.generic"
# Weblate writes component JSON the same way the sync job and the import
# do (sorted keys, 2-space indent), so its first save doesn't reformat every
# file into one huge diff (ut-docs#4371 review). Weblate 2026.10 holds this
# in the component's file_format_params; there is no add-on for it.
JSON_FORMAT_PARAMS = {"json_sort_keys": "case_sensitive", "json_indent": 2, "json_indent_style": "spaces"}
ADDONS = {CLEANUP_ADDON: {}}
ENFORCED_CHECKS = ["icu_message_format", "max-length", "placeholders"]
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class WeblateError(Exception):
    pass


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """urllib re-sends every header, Authorization included, to whatever
    Location a 3xx names, on any host. Never follow: the 3xx surfaces as an
    HTTPError and the run fails."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WeblateClient:
    def __init__(self, base_url, token, timeout=30):
        if not token:
            raise WeblateError("WEBLATE_TOKEN is empty")
        u = urllib.parse.urlparse(base_url)
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in LOOPBACK):
            raise WeblateError(f"refusing {u.scheme}:// for {u.hostname}: the API token must travel over https")
        self.base = base_url.rstrip("/")
        self._origin = (u.scheme, u.netloc)
        self._token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_RefuseRedirects)

    def _url(self, path):
        return path if path.startswith(("http://", "https://")) else self.base + path

    def next_url(self, nxt):
        """A pagination `next` link, rebased onto WEBLATE_URL: only its path
        and query are kept, so a proxy that reports the wrong scheme can't
        downgrade the token to http. Another host is refused."""
        u = urllib.parse.urlparse(nxt)
        if u.netloc and u.netloc != self._origin[1]:
            raise WeblateError(f"refusing to follow a next link to another host: {nxt}")
        return urllib.parse.urlunparse((*self._origin, u.path, "", u.query, ""))

    def request(self, method, path, body=None):
        """-> (status, json or None). 404 is returned, every other error
        (redirects included) raises."""
        url = self._url(path)
        u = urllib.parse.urlparse(url)
        if (u.scheme, u.netloc) != self._origin:
            raise WeblateError(f"refusing to send the token anywhere but {self.base}: {url}")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Token {self._token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            with e:
                detail = e.read().decode(errors="replace")[:500]
            if e.code == 404:
                return 404, None
            if 300 <= e.code < 400:
                raise WeblateError(f"{method} {path}: HTTP {e.code} redirect to "
                                   f"{e.headers.get('Location')!r} refused (set WEBLATE_URL "
                                   "to the final URL)") from None
            raise WeblateError(f"{method} {path}: HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise WeblateError(f"{method} {path}: {e.reason}") from None

    def get(self, path):
        status, out = self.request("GET", path)
        return None if status == 404 else out

    def list(self, path):
        """All results of a paginated list; None when the list itself is 404."""
        out, nxt = [], path
        while nxt:
            page = self.get(nxt)
            if page is None:
                return None
            out.extend(page.get("results", []))
            nxt = page.get("next")
            if nxt:
                nxt = self.next_url(nxt)
        return out


class Report:
    def __init__(self):
        self.actions = []
        self.pending = []
        self.writes = 0

    def log(self, msg):
        self.actions.append(msg)
        print(msg)


class Provisioner:
    def __init__(self, client, repo, project, repo_url, push_url, dry_run):
        self.c = client
        self.repo = repo
        self.project = project
        self.repo_url = repo_url
        self.push_url = push_url
        self.dry = dry_run
        self.r = Report()

    def write(self, method, path, body, what):
        self.r.log(("[dry-run] " if self.dry else "") + what)
        if self.dry:
            return None
        self.r.writes += 1
        status, out = self.c.request(method, path, body)
        if not 200 <= status < 300:
            raise WeblateError(f"{what}: {method} {path} returned HTTP {status}")
        return out

    def load(self, rel):
        with open(os.path.join(self.repo, rel), encoding="utf-8") as f:
            return json.load(f)

    # --- project + label ---------------------------------------------------

    def project_body(self):
        return {
            "name": "Universal Till",
            "slug": self.project,
            "web": "https://universaltill.com",
            "translation_review": True,  # dedicated reviewers (ADR-0160 §4)
            "source_review": False,  # English is reviewed in universal-till
        }

    def ensure_project(self):
        want = self.project_body()
        cur = self.c.get(f"/api/projects/{self.project}/")
        if cur is None:
            self.write("POST", "/api/projects/", want, f"create project {self.project}")
            return
        diff = {k: v for k, v in want.items() if cur.get(k) != v}
        if diff:
            self.write("PATCH", f"/api/projects/{self.project}/", diff,
                       f"update project {self.project}: {', '.join(sorted(diff))}")

    def ensure_labels(self):
        labels = self.c.list(f"/api/projects/{self.project}/labels/") or []
        self.labels = {lab["name"]: lab["id"] for lab in labels}
        if LEGAL_LABEL not in self.labels:
            out = self.write("POST", f"/api/projects/{self.project}/labels/",
                             {"name": LEGAL_LABEL, "color": "red"}, f"create label {LEGAL_LABEL}")
            if out:
                self.labels[LEGAL_LABEL] = out["id"]
        self.label_names = {v: k for k, v in self.labels.items()}

    # --- components --------------------------------------------------------

    def component_body(self, area, name):
        owner = area == REPO_OWNER
        body = {
            "name": name,
            "slug": area,
            "repo": self.repo_url if owner else f"weblate://{self.project}/{REPO_OWNER}",
            "file_format": "json",
            "filemask": f"{area}/*.json",
            "template": f"{area}/en.json",
            "new_base": "",
            "new_lang": "add",
            "edit_template": False,
            "manage_units": False,
            "enforced_checks": ENFORCED_CHECKS,
            "file_format_params": JSON_FORMAT_PARAMS,
        }
        if owner:
            # Only the component that owns the clone has VCS settings; a
            # weblate:// linked one takes them from it, and Weblate refuses
            # them on it ("not available for linked repositories").
            body.update({
                "vcs": "git",
                "push": self.push_url,
                "branch": "main",
                "push_branch": "",
                "merge_style": "rebase",
                "push_on_commit": True,
            })
        return body

    def ensure_component(self, area, name):
        want = self.component_body(area, name)
        path = f"/api/components/{self.project}/{area}/"
        cur = self.c.get(path)
        if cur is None:
            body = dict(want, source_language={"code": "en"})
            self.write("POST", f"/api/projects/{self.project}/components/", body, f"create component {area}")
            return True
        diff = {}
        for k, v in want.items():
            have = cur.get(k)
            if k == "repo" and v.startswith("weblate://"):
                # Weblate reads a link back resolved: the owner's clone URL in
                # `repo`, the link in `linked_component`.
                lp, lc = v[len("weblate://"):].split("/")
                linked = cur.get("linked_component") or ""
                if linked.rstrip("/").endswith(f"/api/components/{lp}/{lc}"):
                    continue
            if isinstance(v, list):
                same = sorted(have or []) == sorted(v)
            else:
                same = have == v
            if not same:
                diff[k] = v
        lang = (cur.get("source_language") or {}).get("code")
        if lang != "en":
            raise WeblateError(f"component {area} has source language {lang!r}; it must be en "
                               "(Weblate can't change it in place: delete and re-run)")
        if diff:
            self.write("PATCH", path, diff, f"update component {area}: {', '.join(sorted(diff))}")
        return False

    def ensure_addon(self, area):
        # Weblate (2026.10) lists a component's add-ons only as URLs on the
        # component itself; GET on .../addons/ is not allowed (POST only).
        # A component that doesn't exist yet (a dry run) has none.
        comp = self.c.get(f"/api/components/{self.project}/{area}/") or {}
        names = set()
        for url in comp.get("addons") or []:
            addon = self.c.get(self.c.next_url(url))
            if addon:
                names.add(addon.get("name"))
        for name, configuration in ADDONS.items():
            if name not in names:
                self.write("POST", f"/api/components/{self.project}/{area}/addons/",
                           {"name": name, "configuration": configuration},
                           f"install {name} on {area}")

    # --- units -------------------------------------------------------------

    def unit_label_names(self, unit):
        names = set()
        for lab in unit.get("labels") or []:
            if isinstance(lab, dict):
                names.add(lab.get("name"))
            else:
                names.add(self.label_names.get(lab, lab))
        return names

    def ensure_units(self, area, just_created):
        meta = self.load(f"meta/{area}.json")
        units = None if (self.dry and just_created) else self.c.list(
            f"/api/translations/{self.project}/{area}/en/units/")
        if not units:
            self.r.pending.append(area)
            self.r.log(f"pending: {area} has no parsed source strings yet — re-run once Weblate has imported it")
            return
        for unit in units:
            key = unit.get("context")
            want = meta.get(key)
            if want is None:
                continue  # Weblate hasn't picked up the latest sync yet
            diff = {}
            if (unit.get("explanation") or "") != want["explanation"]:
                diff["explanation"] = want["explanation"]
            if (unit.get("extra_flags") or "") != want["flags"]:
                diff["extra_flags"] = want["flags"]
            have = self.unit_label_names(unit)
            target = (have - {LEGAL_LABEL}) | ({LEGAL_LABEL} if LEGAL_LABEL in want["labels"] else set())
            if target != have:
                if any(n not in self.labels for n in target):
                    if not self.dry:
                        raise WeblateError(f"label missing for {key}: {sorted(target - self.labels.keys())}")
                diff["labels"] = sorted(self.labels.get(n, 0) for n in target)
            if diff:
                self.write("PATCH", f"/api/units/{unit['id']}/", diff,
                           f"update {area}:{key}: {', '.join(sorted(diff))}")

    def run(self):
        areas = self.load("meta/areas.json")
        for area in areas:
            try:
                i18n_map.validate_area_id(area)
            except ValueError as e:
                raise WeblateError(f"meta/areas.json: {e}") from None
        if REPO_OWNER not in areas:
            raise WeblateError(f"meta/areas.json has no {REPO_OWNER!r} area to own the clone")
        order = [REPO_OWNER] + [a for a in areas if a != REPO_OWNER]
        self.ensure_project()
        self.ensure_labels()
        for area in order:
            created = self.ensure_component(area, areas[area]["name"])
            self.ensure_addon(area)
            self.ensure_units(area, created)
        self.r.log(f"done: {self.r.writes} write(s), {len(self.r.pending)} component(s) pending import")
        return self.r


def provision(client, repo, project=DEFAULT_PROJECT, repo_url=DEFAULT_REPO_URL,
              push_url=DEFAULT_PUSH_URL, dry_run=False):
    return Provisioner(client, repo, project, repo_url, push_url, dry_run).run()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Create/update Weblate components from this repo.")
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--dry-run", action="store_true", help="read only; print what would change")
    args = ap.parse_args(argv)
    url, token = os.environ.get("WEBLATE_URL"), os.environ.get("WEBLATE_TOKEN")
    if not url or not token:
        print("WEBLATE_URL and WEBLATE_TOKEN must be set", file=sys.stderr)
        return 2
    try:
        client = WeblateClient(url, token)
        provision(client, args.repo, args.project,
                  os.environ.get("WEBLATE_REPO_URL", DEFAULT_REPO_URL),
                  os.environ.get("WEBLATE_PUSH_URL", DEFAULT_PUSH_URL), args.dry_run)
    except (WeblateError, OSError, ValueError) as e:
        print(f"provisioning failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
