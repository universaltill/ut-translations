"""Component provisioning against a fake Weblate REST API (a real HTTP server
on 127.0.0.1, so the urllib client, auth header, pagination and JSON bodies
are exercised for real). Nothing here talks to translate.universaltill.com."""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import provision_weblate as p  # noqa: E402

TOKEN = "wlu_test_token"
LINKED_FORBIDDEN = ("branch", "push", "push_branch", "merge_style", "push_on_commit", "vcs")


class FakeWeblate:
    """Just enough of Weblate's API, with pages of 2 so pagination is real."""

    PAGE = 2

    def __init__(self, repo_dir):
        self.repo_dir = repo_dir
        self.projects = {}
        self.labels = {}
        self.components = {}
        self.addons = {}
        self.units = {}
        self.writes = []
        self.parse_on_create = True
        self.next_id = 100

    def nid(self):
        self.next_id += 1
        return self.next_id

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def page(self, items, query, path):
        n = int(query.get("page", ["1"])[0])
        chunk = items[(n - 1) * self.PAGE: n * self.PAGE]
        nxt = f"{self.base()}{path}?page={n + 1}" if n * self.PAGE < len(items) else None
        return 200, {"count": len(items), "next": nxt, "results": chunk}

    def parse_units(self, project, comp):
        with open(os.path.join(self.repo_dir, comp["template"]), encoding="utf-8") as f:
            en = json.load(f)
        self.units[(project, comp["slug"])] = [
            {"id": self.nid(), "context": k, "source": [v], "explanation": "", "extra_flags": "", "labels": []}
            for k, v in en.items()
        ]

    def label_obj(self, project, lid):
        for lab in self.labels.get(project, []):
            if lab["id"] == lid:
                return lab
        raise KeyError(lid)

    def handle(self, method, raw_path, body):
        u = urlparse(raw_path)
        path, q = u.path, parse_qs(u.query)
        if method != "GET":
            self.writes.append((method, path, body))
        if (m := re.fullmatch(r"/api/projects/([^/]+)/", path)):
            slug = m.group(1)
            if slug not in self.projects:
                return 404, {"detail": "Not found."}
            if method == "PATCH":
                self.projects[slug].update(body)
            return 200, self.projects[slug]
        if path == "/api/projects/" and method == "POST":
            self.projects[body["slug"]] = dict(body)
            return 201, body
        if (m := re.fullmatch(r"/api/projects/([^/]+)/labels/", path)):
            pr = m.group(1)
            if method == "POST":
                lab = {"id": self.nid(), **body}
                self.labels.setdefault(pr, []).append(lab)
                return 201, lab
            return self.page(self.labels.get(pr, []), q, path)
        if (m := re.fullmatch(r"/api/projects/([^/]+)/components/", path)) and method == "POST":
            pr = m.group(1)
            # As the real API: a weblate:// linked component takes its VCS
            # settings from the linked one and refuses its own.
            if body.get("repo", "").startswith("weblate://"):
                for opt in LINKED_FORBIDDEN:
                    if opt in body:
                        return 400, {"type": "validation_error", "errors": [{"code": "invalid", "attr": opt,
                                     "detail": "Option is not available for linked repositories."}]}
            comp = dict(body)
            comp["source_language"] = {"code": body["source_language"]["code"], "name": "English"}
            self.components[(pr, body["slug"])] = comp
            if self.parse_on_create:
                self.parse_units(pr, comp)
            return 201, comp
        if (m := re.fullmatch(r"/api/components/([^/]+)/([^/]+)/", path)):
            key = (m.group(1), m.group(2))
            if key not in self.components:
                return 404, {"detail": "Not found."}
            if method == "PATCH":
                self.components[key].update(body)
            # Weblate 2026.10 lists a component's add-ons only as URLs here.
            out = dict(self.components[key])
            # A weblate:// link reads back resolved: the owner's clone URL in
            # `repo`, the link itself in `linked_component`.
            if out.get("repo", "").startswith("weblate://"):
                lp, lc = out["repo"][len("weblate://"):].split("/")
                out["linked_component"] = f"{self.base()}/api/components/{lp}/{lc}/"
                out["repo"] = self.components[(lp, lc)]["repo"]
            out["addons"] = [f"{self.base()}/api/addons/{a['id']}/" for a in self.addons.get(key, [])]
            return 200, out
        if (m := re.fullmatch(r"/api/components/([^/]+)/([^/]+)/addons/", path)):
            key = (m.group(1), m.group(2))
            if method == "POST":
                self.addons.setdefault(key, []).append({"id": self.nid(), **body})
                return 201, body
            # As the real API: this endpoint is POST-only.
            return 405, {"type": "client_error", "errors": [{"code": "method_not_allowed"}]}
        if (m := re.fullmatch(r"/api/addons/(\d+)/", path)):
            aid = int(m.group(1))
            for addons in self.addons.values():
                for a in addons:
                    if a["id"] == aid:
                        return 200, a
            return 404, {"detail": "Not found."}
        if (m := re.fullmatch(r"/api/translations/([^/]+)/([^/]+)/en/units/", path)):
            key = (m.group(1), m.group(2))
            if key not in self.units:
                return 404, {"detail": "Not found."}
            return self.page(self.units[key], q, path)
        if (m := re.fullmatch(r"/api/units/(\d+)/", path)) and method == "PATCH":
            uid = int(m.group(1))
            for (pr, _), units in self.units.items():
                for unit in units:
                    if unit["id"] == uid:
                        for k, v in body.items():
                            unit[k] = [self.label_obj(pr, i) for i in v] if k == "labels" else v
                        return 200, unit
            return 404, {"detail": "Not found."}
        return 404, {"detail": f"no fake route {method} {path}"}


def serve(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self, method):
            if self.headers.get("Authorization") != f"Token {TOKEN}":
                code, out = 401, {"detail": "Invalid token."}
            else:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                code, out = fake.handle(method, self.path, body)
            data = json.dumps(out).encode()
            self.send_response(code)
            if 300 <= code < 400:
                self.send_header("Location", getattr(fake, "location", "") + self.path)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")

        def do_PATCH(self):
            self._do("PATCH")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    fake.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class Provision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.repo = self.tmp.name
        write(f"{r}/meta/areas.json", {
            "sale": {"name": "Sale", "description": "Sale.", "keys": 3},
            "common": {"name": "Common", "description": "Shared.", "keys": 1},
            "receipts-and-legal": {"name": "Receipts and legal", "description": "R.", "keys": 1},
        })
        write(f"{r}/sale/en.json", {"pos.a": "A", "pos.b": "B %s", "pos.c": "C"})
        write(f"{r}/meta/sale.json", {
            "pos.a": {"explanation": "", "flags": "", "labels": [], "lines": ["main"]},
            "pos.b": {"explanation": "B row.", "flags": 'placeholders:"%s"', "labels": [], "lines": ["main"]},
            "pos.c": {"explanation": "", "flags": "max-length:5", "labels": [], "lines": ["main"]},
        })
        write(f"{r}/common/en.json", {"common.ok": "OK"})
        write(f"{r}/meta/common.json", {"common.ok": {"explanation": "OK button.", "flags": "", "labels": [], "lines": ["main"]}})
        write(f"{r}/receipts-and-legal/en.json", {"receipt.f": "Thanks"})
        write(f"{r}/meta/receipts-and-legal.json", {"receipt.f": {"explanation": "", "flags": "", "labels": ["legal"], "lines": ["main"]}})
        self.fake = FakeWeblate(r)
        self.srv = serve(self.fake)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def run_it(self, dry_run=False, token=TOKEN):
        client = p.WeblateClient(self.fake.base(), token)
        with contextlib.redirect_stdout(io.StringIO()):
            return p.provision(client, self.repo, project="universal-till",
                               repo_url="https://github.com/universaltill/ut-translations.git",
                               push_url="git@github.com:universaltill/ut-translations.git",
                               dry_run=dry_run)

    def test_fresh_instance_creates_everything(self):
        self.run_it()
        proj = self.fake.projects["universal-till"]
        self.assertIs(proj["translation_review"], True)
        self.assertEqual([lab["name"] for lab in self.fake.labels["universal-till"]], ["legal"])
        # common owns the clone; every other component links to it.
        posts = [b["slug"] for m, path, b in self.fake.writes if path.endswith("/components/")]
        self.assertEqual(posts[0], "common")
        common = self.fake.components[("universal-till", "common")]
        sale = self.fake.components[("universal-till", "sale")]
        self.assertEqual(common["repo"], "https://github.com/universaltill/ut-translations.git")
        self.assertEqual(common["push"], "git@github.com:universaltill/ut-translations.git")
        self.assertEqual(sale["repo"], "weblate://universal-till/common")
        self.assertEqual(sale["filemask"], "sale/*.json")
        self.assertEqual(sale["template"], "sale/en.json")
        self.assertEqual(sale["file_format"], "json")
        self.assertIs(sale["edit_template"], False)
        self.assertEqual(sale["source_language"]["code"], "en")
        self.assertEqual(sale["slug"], "sale")
        self.assertEqual(self.fake.addons[("universal-till", "sale")][0]["name"], "weblate.cleanup.generic")
        units = {u["context"]: u for u in self.fake.units[("universal-till", "sale")]}
        self.assertEqual(units["pos.b"]["explanation"], "B row.")
        self.assertEqual(units["pos.b"]["extra_flags"], 'placeholders:"%s"')
        self.assertEqual(units["pos.c"]["extra_flags"], "max-length:5")
        legal = self.fake.units[("universal-till", "receipts-and-legal")][0]
        self.assertEqual([lab["name"] for lab in legal["labels"]], ["legal"])
        # Keys with nothing to set are not touched.
        patched = {path for m, path, _ in self.fake.writes if path.startswith("/api/units/")}
        self.assertNotIn(f"/api/units/{units['pos.a']['id']}/", patched)

    def test_second_run_writes_nothing(self):
        self.run_it()
        self.fake.writes.clear()
        report = self.run_it()
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(report.writes, 0)

    def test_drift_is_patched_and_only_drift(self):
        self.run_it()
        self.fake.components[("universal-till", "sale")]["filemask"] = "wrong/*.json"
        self.fake.units[("universal-till", "sale")][2]["extra_flags"] = "max-length:99"
        self.fake.writes.clear()
        self.run_it()
        self.assertEqual(self.fake.writes, [
            ("PATCH", "/api/components/universal-till/sale/", {"filemask": "sale/*.json"}),
            ("PATCH", f"/api/units/{self.fake.units[('universal-till', 'sale')][2]['id']}/", {"extra_flags": "max-length:5"}),
        ])

    def test_metadata_change_in_repo_is_pushed(self):
        self.run_it()
        write(f"{self.repo}/meta/sale.json", {
            "pos.a": {"explanation": "Now described.", "flags": "", "labels": [], "lines": ["main"]},
            "pos.b": {"explanation": "B row.", "flags": 'placeholders:"%s"', "labels": [], "lines": ["main"]},
            "pos.c": {"explanation": "", "flags": "max-length:5", "labels": [], "lines": ["main"]},
        })
        self.fake.writes.clear()
        self.run_it()
        self.assertEqual(len(self.fake.writes), 1)
        self.assertEqual(self.fake.writes[0][2], {"explanation": "Now described."})

    def test_other_labels_on_a_unit_are_kept(self):
        self.run_it()
        other = {"id": self.fake.nid(), "name": "needs-screenshot", "color": "blue"}
        self.fake.labels["universal-till"].append(other)
        legal_unit = self.fake.units[("universal-till", "receipts-and-legal")][0]
        legal_unit["labels"] = [other]
        self.fake.writes.clear()
        self.run_it()
        names = sorted(lab["name"] for lab in legal_unit["labels"])
        self.assertEqual(names, ["legal", "needs-screenshot"])

    def test_linked_components_read_back_resolved_are_not_rewritten(self):
        self.run_it()
        self.fake.writes.clear()
        self.run_it()
        self.assertEqual([w for w in self.fake.writes if w[1].startswith("/api/components/")], [])

    def test_dry_run_writes_nothing_but_reports_the_plan(self):
        report = self.run_it(dry_run=True)
        self.assertEqual(self.fake.writes, [])
        self.assertTrue(any("create project universal-till" in a for a in report.actions))
        self.assertTrue(any("create component sale" in a for a in report.actions))
        # A component that doesn't exist yet has no add-ons to read (the
        # real API's GET .../addons/ is 405): the plan still lists the install.
        self.assertTrue(any("install weblate.cleanup.generic on sale" in a for a in report.actions))

    def test_units_not_parsed_yet_is_reported_not_fatal(self):
        self.fake.parse_on_create = False
        report = self.run_it()
        self.assertEqual(len(report.pending), 3)
        self.fake.parse_units("universal-till", self.fake.components[("universal-till", "sale")])
        report = self.run_it()
        self.assertEqual(len(report.pending), 2)
        units = {u["context"]: u for u in self.fake.units[("universal-till", "sale")]}
        self.assertEqual(units["pos.b"]["explanation"], "B row.")

    def test_write_that_404s_fails_loudly(self):
        orig = self.fake.handle

        def handle(method, raw_path, body):
            if method == "POST" and raw_path == "/api/projects/":
                self.fake.writes.append((method, raw_path, body))
                return 404, {"detail": "Not found."}
            return orig(method, raw_path, body)
        self.fake.handle = handle
        with self.assertRaises(p.WeblateError) as cm:
            self.run_it()
        self.assertIn("404", str(cm.exception))

    def test_pagination_next_is_rebased_onto_the_base_url(self):
        # A proxy-confused Weblate hands out next links with the wrong
        # scheme; the token must still only travel to WEBLATE_URL's
        # scheme+host, so the client follows the path+query on the base.
        orig_page = self.fake.page

        def page(items, query, path):
            code, out = orig_page(items, query, path)
            if out["next"]:
                out["next"] = out["next"].replace("http://", "https://", 1)
            return code, out
        self.fake.page = page
        self.run_it()
        units = {u["context"]: u for u in self.fake.units[("universal-till", "sale")]}
        self.assertEqual(units["pos.c"]["extra_flags"], "max-length:5")  # unit on page 2

    def test_bad_token_fails_loudly(self):
        with self.assertRaises(p.WeblateError) as cm:
            self.run_it(token="wrong")
        self.assertIn("401", str(cm.exception))
        self.assertNotIn("wrong", str(cm.exception))


class Catcher:
    """A second loopback server that records every request it receives."""

    def __init__(self):
        self.seen = []
        catcher = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                catcher.seen.append((self.path, self.headers.get("Authorization")))
                data = b'{"results": [], "next": null}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Redirects(unittest.TestCase):
    """urllib re-sends Authorization on a 3xx to any Location; the client
    must refuse to follow redirects at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write(f"{self.tmp.name}/meta/areas.json", {"common": {"name": "Common", "description": "", "keys": 0}})
        self.catcher = Catcher()
        self.fake = FakeWeblate(self.tmp.name)
        self.fake.handle = lambda method, raw_path, body: (302, {"detail": "moved"})
        self.fake.location = self.catcher.url
        self.srv = serve(self.fake)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.catcher.close()
        self.tmp.cleanup()

    def test_cross_host_redirect_does_not_leak_the_token(self):
        client = p.WeblateClient(self.fake.base(), TOKEN)
        with self.assertRaises(p.WeblateError) as cm:
            client.get("/api/projects/universal-till/")
        self.assertIn("302", str(cm.exception))
        self.assertEqual(self.catcher.seen, [])

    def test_redirect_fails_the_script(self):
        env = {"WEBLATE_URL": self.fake.base(), "WEBLATE_TOKEN": TOKEN}
        err = io.StringIO()
        with unittest.mock.patch.dict(os.environ, env), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.main(["--repo", self.tmp.name]), 1)
        self.assertIn("302", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())
        self.assertEqual(self.catcher.seen, [])


class ClientSafety(unittest.TestCase):
    def test_absolute_url_must_match_base_scheme_and_host(self):
        c = p.WeblateClient("https://translate.universaltill.com", TOKEN)
        for bad in ("http://translate.universaltill.com/api/x/",
                    "https://evil.example/api/x/",
                    "https://translate.universaltill.com:8443/api/x/"):
            with self.assertRaises(p.WeblateError, msg=bad):
                c.request("GET", bad)

    def test_next_link_rebased_keeps_path_and_query_only(self):
        c = p.WeblateClient("https://translate.universaltill.com", TOKEN)
        self.assertEqual(c.next_url("http://translate.universaltill.com/api/units/?page=2"),
                         "https://translate.universaltill.com/api/units/?page=2")
        with self.assertRaises(p.WeblateError):
            c.next_url("https://evil.example/api/units/?page=2")

    def test_area_ids_from_meta_are_validated(self):
        with tempfile.TemporaryDirectory() as d:
            write(f"{d}/meta/areas.json", {"common": {"name": "C"}, "../x": {"name": "X"}})
            class NoNetwork:
                def __getattr__(self, name):
                    raise AssertionError(f"client.{name} called before the area ids were checked")
            with self.assertRaises(p.WeblateError) as cm, contextlib.redirect_stdout(io.StringIO()):
                p.provision(NoNetwork(), d)
            self.assertIn("../x", str(cm.exception))

    def test_plain_http_refused_off_loopback(self):
        with self.assertRaises(p.WeblateError):
            p.WeblateClient("http://translate.universaltill.com", TOKEN)

    def test_https_and_loopback_http_allowed(self):
        p.WeblateClient("https://translate.universaltill.com/", TOKEN)
        p.WeblateClient("http://127.0.0.1:8080", TOKEN)

    def test_empty_token_refused(self):
        with self.assertRaises(p.WeblateError):
            p.WeblateClient("https://translate.universaltill.com", "")

    def test_main_requires_env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("WEBLATE_")}
        with unittest.mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(p.main(["--repo", "."]), 2)


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
