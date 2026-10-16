#!/usr/bin/env python3
"""Upload a built language bundle to the marketplace and approve it
(ADR-0160 §5; ut-docs#4367). The same two calls each ut-plugin-language-*
release.yml makes (scripts/publish.sh, scripts/approve.sh), with the scoped
language-publisher token:

  POST <base>/ui/api/vendor/releases/upload      multipart: artifact,
       manifest, plugin_id, version, channel, expected_hash, release_notes
  POST <base>/ui/api/admin/releases/<id>/assign-review
  POST <base>/ui/api/admin/releases/<id>/review-decision   decision=approved
       (the marketplace signs it on approval)

ut-cloud accepts MARKETPLACE_LANGUAGE_UPLOAD_TOKEN for
com.universaltill.language-* only, on the upload route and those release
routes only (ut-docs#4368, #4473).

Before any call it re-checks what the build job handed over: the bundle's
sha256 equals --sha256 and report.json's equals --report-sha256 (both the
build job's outputs: the verify job in between runs universal-till's code
and could replace the artifact), the id is a language id for --locale, and
report.json says publishable. review.md is not read here.

The token comes only from the environment, is sent only to
MARKETPLACE_BASE_URL's own scheme and host over https (plain http only to
loopback, for tests), never in argv, never printed; redirects are refused
(urllib would re-send it to any Location).

Env: MARKETPLACE_BASE_URL, MARKETPLACE_LANGUAGE_UPLOAD_TOKEN
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

ID_PREFIX = "com.universaltill.language-"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
RELEASE_ID_RE = re.compile(r"[0-9a-fA-F-]{36}")
REVIEWER = "translation-publisher"


class PublishError(Exception):
    pass


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Marketplace:
    def __init__(self, base_url, token, timeout=120):
        if not token:
            raise PublishError("MARKETPLACE_LANGUAGE_UPLOAD_TOKEN is empty")
        u = urllib.parse.urlparse(base_url or "")
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in LOOPBACK):
            raise PublishError(f"refusing {u.scheme or '?'}:// for {u.hostname}: the token must travel over https")
        if u.query or u.fragment or u.username or u.password:
            raise PublishError("MARKETPLACE_BASE_URL must be a plain scheme://host[/path]")
        self.base = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_RefuseRedirects)

    def post(self, path, body, content_type):
        url = self.base + path
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", content_type)
        req.add_header("Accept", "application/json")
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                raw = r.read()
                return r.status, raw
        except urllib.error.HTTPError as e:
            with e:
                detail = e.read().decode(errors="replace")[:800]
            if 300 <= e.code < 400:
                raise PublishError(f"POST {path}: HTTP {e.code} redirect refused (set MARKETPLACE_BASE_URL "
                                   "to the final URL)") from None
            raise PublishError(f"POST {path}: HTTP {e.code}: {detail.replace(self._token, '***')}") from None
        except urllib.error.URLError as e:
            raise PublishError(f"POST {path}: {e.reason}") from None

    def post_form(self, path, fields):
        return self.post(path, urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded")

    def post_multipart(self, path, fields, file_field, file_name, file_bytes, file_type):
        boundary = "utt-" + uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
                         + v.encode() + b"\r\n")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                     f'filename="{file_name}"\r\nContent-Type: {file_type}\r\n\r\n'.encode() + file_bytes + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return self.post(path, b"".join(parts), f"multipart/form-data; boundary={boundary}")


def _member(tf, name):
    try:
        f = tf.extractfile(name)
    except KeyError:
        raise PublishError(f"bundle has no {name}") from None
    if f is None:
        raise PublishError(f"bundle {name} is not a regular file")
    return f.read()


def load_report(path, sha256):
    with open(path, "rb") as f:
        raw = f.read()
    got = hashlib.sha256(raw).hexdigest()
    if got != (sha256 or "").lower():
        raise PublishError(f"report.json sha256 {got} != the build job's {sha256}")
    return json.loads(raw)


def read_bundle(path, sha256, locale, report):
    with open(path, "rb") as f:
        data = f.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256.lower() or got != report.get("sha256"):
        raise PublishError(f"bundle sha256 {got} != the build job's {sha256} / report {report.get('sha256')}")
    if not report.get("publishable"):
        raise PublishError("report.json says not publishable: " + "; ".join(report.get("problems") or []))
    with tarfile.open(path) as tf:
        manifest_raw = _member(tf, "manifest.json")
        info = json.loads(_member(tf, "bundle.json"))
    manifest = json.loads(manifest_raw)
    if manifest.get("id") != ID_PREFIX + locale or report.get("plugin_id") != manifest.get("id"):
        raise PublishError(f"bundle id {manifest.get('id')!r} is not {ID_PREFIX}{locale}")
    if info.get("locale") != locale or not info.get("approved_count"):
        raise PublishError("bundle.json locale/approved_count do not allow a publish")
    return data, manifest_raw.decode(), manifest


def publish(mp, path, sha256, locale, report, channel):
    data, manifest_raw, manifest = read_bundle(path, sha256, locale, report)
    info = report.get("bundle_json") or {}
    fields = {
        "manifest": manifest_raw,
        "plugin_id": manifest["id"],
        "version": manifest["version"],
        "channel": channel,
        "expected_hash": sha256,
        "release_notes": (f"Language bundle {manifest['version']}: {info.get('approved_count')} of "
                          f"{info.get('key_count')} strings, source {info.get('source_commit', '')[:12]} "
                          "(ADR-0160)."),
    }
    status, raw = mp.post_multipart("/ui/api/vendor/releases/upload", fields, "artifact",
                                    os.path.basename(path), data, "application/gzip")
    try:
        release_id = json.loads(raw)["data"]["release_id"]
    except (ValueError, KeyError, TypeError):
        raise PublishError(f"upload answered HTTP {status} without data.release_id") from None
    if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
        raise PublishError(f"upload returned an unexpected release id {release_id!r}")
    print(f"uploaded {manifest['id']} {manifest['version']} ({channel}): release {release_id}")
    base = f"/ui/api/admin/releases/{release_id}"
    mp.post_form(f"{base}/assign-review", {"reviewer_id": REVIEWER, "priority": "P1"})
    mp.post_form(f"{base}/review-decision", {
        "decision": "approved", "reviewed_by": REVIEWER,
        "comments": "approved by the ut-translations publish workflow: every string passed the ADR-0160 review gate"})
    print(f"approved release {release_id}")
    return release_id


def main(argv=None):
    ap = argparse.ArgumentParser(description="Upload and approve a language bundle.")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--sha256", required=True, help="the build job's sha256 output")
    ap.add_argument("--report-sha256", required=True, help="the build job's report_sha256 output")
    ap.add_argument("--locale", required=True)
    ap.add_argument("--channel", choices=("stable", "beta", "alpha"), default="stable")
    args = ap.parse_args(argv)
    try:
        report = load_report(args.report, args.report_sha256)
        mp = Marketplace(os.environ.get("MARKETPLACE_BASE_URL", ""),
                         os.environ.get("MARKETPLACE_LANGUAGE_UPLOAD_TOKEN", ""))
        publish(mp, args.bundle, args.sha256, args.locale, report, args.channel)
    except (PublishError, OSError, ValueError, KeyError, tarfile.TarError) as e:
        print(f"publish failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
