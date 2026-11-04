#!/usr/bin/env python3
"""Which language bundles are behind Weblate (ut-docs#5479, ADR-0160 §5).

No secret, no write: reads Weblate's anonymously readable language list
(code, last_change, approved) and the marketplace's public language
catalogue (tag, version "2.<YYYYMMDD>.<n>"), and prints, per Weblate
language, whether its published bundle is stale:

  never    no bundle of that tag in the catalogue
  stale    Weblate changed after the moment the bundle was built (its version
           "2.<YYYYMMDD>.<seconds since 00:00 UTC>", tools/build_bundle.py)
  current  otherwise
  empty    Weblate approved nothing yet (a bundle would hold no string)

A change Weblate records but the bundle omits (an unapproved edit) still costs
one rebuild; a missed change is not possible. The build itself still applies the whole review gate (approved only,
legal/money human-approved, min_coverage); this tool decides only WHEN to
ask for a build, never WHAT goes in one.

`--publish-list` names the languages the owner opted in (repo variable
BUNDLE_AUTO_PUBLISH, comma separated); only those that are never/stale are
printed as `dispatch=` for the workflow to start publish-bundle with.
Everything else is report only.
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import locales  # noqa: E402

PUBLIC_MARKETPLACE_URL = "https://cloud.universaltill.com"
VERSION_RE = re.compile(r"^\d+\.(\d{8})\.(\d{1,5})$")


class RefreshError(Exception):
    pass


def _get_json(url, timeout=30):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" and not (u.scheme == "http" and u.hostname in locales.LOOPBACK):
        raise RefreshError(f"{url!r} must be https")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.build_opener(locales._NoRedirects).open(req, timeout=timeout) as r:
            if r.status != 200:
                raise RefreshError(f"{u.hostname}: HTTP {r.status}")
            return json.loads(r.read())
    except RefreshError:
        raise
    except urllib.error.HTTPError as e:
        e.close()
        raise RefreshError(f"{u.hostname}: HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RefreshError(f"can't read {u.hostname}: {e}") from None


def _parse_time(v):
    if not isinstance(v, str):
        return None
    try:
        t = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def version_time(ver):
    """The build moment a bundle version encodes, or None."""
    m = VERSION_RE.match(ver or "")
    if not m:
        return None
    try:
        d = datetime.datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return d + datetime.timedelta(seconds=int(m.group(2)))


def weblate_rows(base_url, project):
    """[{code, changed (aware UTC datetime or None), approved}] from the public list."""
    data = _get_json(base_url.rstrip("/") + f"/api/projects/{urllib.parse.quote(project)}/languages/")
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise RefreshError("Weblate's language list is empty or not a list")
    out = []
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("code"), str):
            raise RefreshError("Weblate's language list holds an entry without a code")
        ap = r.get("approved")
        out.append({"code": r["code"], "changed": _parse_time(r.get("last_change")),
                    "approved": ap if isinstance(ap, (int, float)) and not isinstance(ap, bool) else 0})
    return out


def published_versions(base_url):
    """{till tag: version} for every language bundle in the public catalogue."""
    versions = {}
    token = ""
    for _ in range(20):
        q = {"capability": "language", "page_size": "100"}
        if token:
            q["page_token"] = token
        data = _get_json(base_url.rstrip("/") + "/api/v1/catalog/plugins?" + urllib.parse.urlencode(q))
        for p in (data.get("plugins") or []):
            ver = p.get("version")
            for loc in p.get("availableLocales") or []:
                if isinstance(loc, str) and isinstance(ver, str):
                    old = version_time(versions.get(loc.lower()))
                    new = version_time(ver)
                    if old is None or (new is not None and new > old):
                        versions[loc.lower()] = ver  # several listings: the newest counts
        token = data.get("nextPageToken") or ""
        if not token:
            return versions
    raise RefreshError("marketplace catalogue paged past 20 pages")


def classify(rows, versions):
    """-> [(code, tag, state, detail)] sorted by code."""
    out = []
    for r in sorted(rows, key=lambda r: r["code"]):
        try:
            tag = locales.till_tag(r["code"])
        except locales.LocaleError:
            continue  # not a code the publish workflow accepts
        ver = versions.get(tag)
        built = version_time(ver)
        if r["approved"] <= 0:
            state = "empty"
        elif built is None:
            state = "never"
        elif r["changed"] is None or r["changed"] > built:
            state = "stale"  # no usable time: err towards a rebuild
        else:
            state = "current"
        out.append((r["code"], tag, state, ver or "-"))
    return out


def to_dispatch(rows, publish_list):
    want = {c.strip() for c in (publish_list or "").split(",") if c.strip()}
    return [code for code, _tag, state, _v in rows if code in want and state in ("never", "stale")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--publish-list", default=os.environ.get("BUNDLE_AUTO_PUBLISH", ""))
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    args = ap.parse_args(argv)
    try:
        cfg = locales.load_config(args.repo)
        wl = os.environ.get("WEBLATE_URL") or locales.PUBLIC_WEBLATE_URL
        mk = os.environ.get("MARKETPLACE_CATALOG_URL") or PUBLIC_MARKETPLACE_URL
        rows = classify(weblate_rows(wl, cfg["project"]), published_versions(mk))
        todo = to_dispatch(rows, args.publish_list)
    except (RefreshError, locales.LocaleError, OSError, ValueError) as e:
        print(f"refresh plan failed: {e}", file=sys.stderr)
        return 1
    for code, tag, state, ver in rows:
        print(f"{code:10} {tag:10} {state:8} published={ver}", file=sys.stderr)
    print("dispatch=" + json.dumps(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
