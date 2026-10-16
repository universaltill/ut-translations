"""Marketplace upload + approve against a fake marketplace on 127.0.0.1
(ut-docs#4367). Nothing here talks to a real marketplace."""

import contextlib
import email.parser
import email.policy
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import build_bundle as bb  # noqa: E402
import publish_bundle as pb  # noqa: E402

TOKEN = "mlt_language_token_s3cret"
RELEASE = "0b6f2c1e-3c4d-4e5f-8a9b-0c1d2e3f4a5b"


class FakeMarketplace:
    def __init__(self):
        self.calls = []
        self.upload = None
        self.status = {}
        self.location = None

    def handle(self, method, path, headers, body):
        self.calls.append((method, path, headers.get("Authorization")))
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return 401, {"error": "missing or invalid upload token"}
        if path in self.status:
            return self.status[path]
        if path == "/ui/api/vendor/releases/upload":
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
            self.upload = {k: (p.get_content() if k != "artifact" else p.get_payload(decode=True))
                           for k, p in parts.items()}
            return 201, {"data": {"release_id": RELEASE, "status": "uploaded"}}
        if path.startswith(f"/ui/api/admin/releases/{RELEASE}/"):
            return 200, {"data": {"ok": True, "form": {k: v[0] for k, v in parse_qs(body.decode()).items()}}}
        return 404, {"error": "no route"}


def serve(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            if fake.location:
                code, out = 302, {}
            else:
                code, out = fake.handle("POST", self.path, self.headers, body)
            data = json.dumps(out).encode()
            self.send_response(code)
            if fake.location:
                self.send_header("Location", fake.location)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    fake.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Publish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeMarketplace()
        self.srv = serve(self.fake)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.fake.port}"
        manifest = {"id": "com.universaltill.language-tr", "name": "Turkish", "version": "2.20261016.45005"}
        info = {"locale": "tr", "approved_count": 2, "key_count": 7, "source_commit": "a" * 40}
        data = bb.deterministic_targz({"manifest.json": json.dumps(manifest), "bundle.json": json.dumps(info),
                                       "locales/tr.json": "{}"}, 0)
        self.bundle = os.path.join(self.tmp.name, "b.tar.gz")
        with open(self.bundle, "wb") as f:
            f.write(data)
        self.sha = hashlib.sha256(data).hexdigest()
        self.report = {"sha256": self.sha, "plugin_id": manifest["id"], "publishable": True,
                       "problems": [], "bundle_json": info}
        self.report_path = os.path.join(self.tmp.name, "report.json")
        self.write_report()

    def write_report(self):
        with open(self.report_path, "w") as f:
            json.dump(self.report, f)

    def report_sha(self):
        with open(self.report_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def run_main(self, locale="tr", sha=None, base=None, token=TOKEN, report_sha=None):
        env = {"MARKETPLACE_BASE_URL": base or self.base, "MARKETPLACE_LANGUAGE_UPLOAD_TOKEN": token}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = pb.main(["--bundle", self.bundle, "--report", self.report_path,
                                "--sha256", sha or self.sha, "--report-sha256", report_sha or self.report_sha(),
                                "--locale", locale])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return code, out.getvalue() + err.getvalue()

    def test_uploads_then_assigns_and_approves(self):
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual([p for _, p, _ in self.fake.calls], [
            "/ui/api/vendor/releases/upload",
            f"/ui/api/admin/releases/{RELEASE}/assign-review",
            f"/ui/api/admin/releases/{RELEASE}/review-decision"])
        up = self.fake.upload
        self.assertEqual(up["plugin_id"], "com.universaltill.language-tr")
        self.assertEqual(up["version"], "2.20261016.45005")
        self.assertEqual(up["expected_hash"], self.sha)
        self.assertEqual(up["channel"], "stable")
        self.assertEqual(hashlib.sha256(up["artifact"]).hexdigest(), self.sha)
        self.assertEqual(json.loads(up["manifest"])["id"], "com.universaltill.language-tr")

    def test_token_is_never_printed(self):
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, out)
        self.fake.status["/ui/api/vendor/releases/upload"] = (403, {"error": f"echo {TOKEN}"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, out)

    def test_refuses_a_bundle_that_is_not_what_the_build_job_made(self):
        code, out = self.run_main(sha="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("sha256", out)
        self.assertEqual(self.fake.calls, [])

    def test_refuses_a_report_that_is_not_what_the_build_job_wrote(self):
        # Review finding 2(c): the verify job runs till code and could
        # replace the artifact; report.json is covered by the build job's
        # sha256 output like the bundle is.
        built = self.report_sha()
        self.report["problems"] = ["edited after the build"]
        self.write_report()
        code, out = self.run_main(report_sha=built)
        self.assertEqual(code, 1)
        self.assertIn("report.json sha256", out)
        self.assertEqual(self.fake.calls, [])

    def rebuild(self, files):
        data = bb.deterministic_targz(files, 0)
        with open(self.bundle, "wb") as f:
            f.write(data)
        self.sha = hashlib.sha256(data).hexdigest()
        self.report["sha256"] = self.sha
        self.write_report()

    def test_a_bundle_missing_an_entry_fails_with_a_clear_error(self):
        self.rebuild({"manifest.json": json.dumps({"id": "com.universaltill.language-tr"}), "locales/tr.json": "{}"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("bundle has no bundle.json", out)
        self.assertEqual(self.fake.calls, [])

    def test_a_bundle_entry_that_is_not_a_file_fails_with_a_clear_error(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as tf:
            for name in ("manifest.json", "bundle.json"):
                ti = tarfile.TarInfo(name)
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
        with open(self.bundle, "wb") as f:
            f.write(raw.getvalue())
        self.sha = hashlib.sha256(raw.getvalue()).hexdigest()
        self.report["sha256"] = self.sha
        self.write_report()
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("manifest.json is not a regular file", out)
        self.assertNotIn("Traceback", out)

    def test_refuses_another_locale_or_id(self):
        code, out = self.run_main(locale="de")
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.calls, [])

    def test_refuses_an_unpublishable_report(self):
        self.report["publishable"] = False
        self.report["problems"] = ["no approved strings"]
        self.write_report()
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("no approved strings", out)
        self.assertEqual(self.fake.calls, [])

    def test_refuses_plain_http_off_loopback_and_an_empty_token(self):
        with self.assertRaisesRegex(pb.PublishError, "https"):
            pb.Marketplace("http://marketplace.example.com", TOKEN)
        with self.assertRaisesRegex(pb.PublishError, "empty"):
            pb.Marketplace("https://marketplace.example.com", "")
        code, _ = self.run_main(token="")
        self.assertEqual(code, 1)

    def test_never_follows_a_redirect(self):
        self.fake.location = "http://127.0.0.1:1/steal"
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("redirect refused", out)

    def test_refuses_an_unexpected_release_id(self):
        self.fake.status["/ui/api/vendor/releases/upload"] = (201, {"data": {"release_id": "../../x"}})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.fake.calls), 1)

    def test_a_failed_approval_fails_the_run(self):
        self.fake.status[f"/ui/api/admin/releases/{RELEASE}/review-decision"] = (401, {"error": "no"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401", out)


if __name__ == "__main__":
    unittest.main()
