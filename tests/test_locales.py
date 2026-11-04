"""Which languages the bundle workflow may build (tools/locales.py;
ut-docs#4640): the code -> till tag map, the derived spec, the overrides-only
config, and a language list that fails closed."""

import io
import json
import os
import re
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bundle_checks  # noqa: E402
import locales as L  # noqa: E402
from provision_weblate import WeblateError  # noqa: E402

CFG = L.load_config(ROOT)
ROWS = [{"code": c, "name": c.upper()} for c in ("en", "de", "tr", "pt", "zh_Hans", "fr", "sr_Latn")] + \
    [{"code": "pt_BR", "name": "Portuguese (Brazil)"}]


class FakeClient:
    def __init__(self, data=None, exc=None):
        self.data, self.exc, self.paths = data, exc, []

    def get(self, path):
        self.paths.append(path)
        if self.exc:
            raise self.exc
        return self.data


class Tags(unittest.TestCase):
    def test_till_tag(self):
        for code, tag in (("de", "de"), ("pt", "pt"), ("pt_BR", "pt-br"), ("zh_Hans", "zh-hans"), ("fil", "fil")):
            self.assertEqual(L.till_tag(code), tag)

    def test_malformed_codes_are_refused(self):
        for bad in ("pt-BR", "ca@valencia", "", "PT", "../x", "de;ls", "de\n", "d", "abcd", "pt_", "pt_B",
                    "pt_" + "a" * 9, "dé", "de_BR_x", " de", "de ", None, 5):
            with self.assertRaises(L.LocaleError, msg=repr(bad)):
                L.till_tag(bad)

    def test_the_map_is_injective_over_a_fixture(self):
        codes = [a + b for a in "dpzs" for b in ("e", "t", "h", "r", "n")] + \
                [f"{l}_{r}" for l in ("pt", "zh", "sr", "de", "en") for r in ("BR", "PT", "Hans", "Hant", "Latn", "Cyrl", "AT", "CH", "US", "GB")]
        codes = [c for c in codes if L.CODE_RE.fullmatch(c)]
        self.assertGreaterEqual(len(codes), 50)
        tags = [L.till_tag(c) for c in codes]
        self.assertEqual(len(set(tags)), len(tags))

    def test_collision_in_the_live_list_is_refused(self):
        rows = [{"code": "pt_BR", "name": "a"}, {"code": "pt_br", "name": "b"}, {"code": "de", "name": "de"}]
        for code in ("pt_BR", "pt_br", "de"):  # fails closed for the whole list
            with self.assertRaisesRegex(L.LocaleError, "both map"):
                L.resolve_spec(rows, CFG, code)
        rows = [{"code": "de", "name": "a"}, {"code": "de_DE", "name": "b"}]  # different tags: fine
        self.assertEqual(L.resolve_spec(rows, CFG, "de")["tag"], "de")

    def test_regex_text_equals_python_behaviour(self):
        pat = re.compile(L.CODE_RE_TEXT)
        for s in ("de", "pt_BR", "zh_Hans", "pt-BR", "de\n", "", "PT", "abcd"):
            self.assertEqual(bool(pat.fullmatch(s)), bool(L.CODE_RE.fullmatch(s)))


class Scripts(unittest.TestCase):
    def test_suffix_wins(self):
        self.assertEqual(L.script_for("zh_Hans"), "Hans")
        self.assertEqual(L.script_for("zh_Hant"), "Hant")
        self.assertEqual(L.script_for("sr_Latn"), "Latn")
        self.assertEqual(L.script_for("pt_BR"), "Latn")
        self.assertEqual(L.script_for("ar"), "Arab")

    def test_unknown_or_ambiguous_is_refused_never_latin(self):
        for code in ("zh", "xx", "sr", "tlh"):
            with self.assertRaises(L.LocaleError, msg=code):
                L.script_for(code)

    def test_override_is_validated(self):
        self.assertEqual(L.script_for("zh", "Hans"), "Hans")
        with self.assertRaises(L.LocaleError):
            L.script_for("de", "Klingon")
        with self.assertRaises(L.LocaleError):
            L.script_for("zh_Hans", "Hant")

    def test_every_script_has_a_checker(self):
        for s in set(L.SCRIPTS.values()) | set(L.KNOWN_SCRIPTS):
            self.assertTrue(bundle_checks.supports_script(s), s)

    def test_a_script_without_a_checker_fails_closed(self):
        old = L.KNOWN_SCRIPTS
        try:
            L.KNOWN_SCRIPTS = old | {"Tfng"}
            with self.assertRaisesRegex(L.LocaleError, "no per-string checker"):
                L.script_for("zgh_Tfng")
        finally:
            L.KNOWN_SCRIPTS = old


class Spec(unittest.TestCase):
    def test_defaults_for_a_new_language(self):
        s = L.resolve_spec(ROWS, CFG, "pt_BR")
        self.assertEqual(s, {"weblate": "pt_BR", "tag": "pt-br", "name": "Portuguese (Brazil)", "script": "Latn",
                             "human_spot_check": False})
        self.assertNotIn("min_coverage", s)
        self.assertNotIn("source", s)

    def test_overrides_apply(self):
        self.assertTrue(L.resolve_spec(ROWS, CFG, "de")["human_spot_check"])
        self.assertEqual(L.resolve_spec(ROWS, CFG, "pt")["min_coverage"], 0.95)
        self.assertTrue(L.resolve_spec(None, CFG, "en")["source"])

    def test_human_locales_are_exactly_en_de_tr(self):
        self.assertEqual(sorted(c for c, o in CFG["overrides"].items() if o.get("human_spot_check")),
                         ["de", "en", "tr"])

    def test_weblate_cannot_set_gate_fields(self):
        rows = [{"code": "fr", "name": "French", "human_spot_check": False, "min_coverage": 0.0, "source": True,
                 "script": "Hans", "tag": "evil"}]
        s = L.resolve_spec(rows, CFG, "fr")
        self.assertEqual((s["script"], s["tag"], s["human_spot_check"]), ("Latn", "fr", False))
        self.assertNotIn("source", s)
        self.assertNotIn("min_coverage", s)

    def test_code_not_in_the_live_list_is_refused(self):
        with self.assertRaisesRegex(L.LocaleError, "not a language"):
            L.resolve_spec(ROWS, CFG, "ja")

    def test_override_for_an_absent_code_is_never_published(self):
        rows = [r for r in ROWS if r["code"] != "de"]
        with self.assertRaisesRegex(L.LocaleError, "not a language"):
            L.resolve_spec(rows, CFG, "de")

    def test_translated_language_needs_weblate(self):
        with self.assertRaisesRegex(L.LocaleError, "WEBLATE_URL"):
            L.resolve_spec(None, CFG, "de")

    def test_name_is_sanitised_and_bounded(self):
        rows = [{"code": "fr", "name": "Fr‮ench <b>\x00\n(Français)</b> \"x\""}]
        name = L.resolve_spec(rows, CFG, "fr")["name"]
        for ch in "<>\"\x00‮\n":
            self.assertNotIn(ch, name)
        self.assertIn("Français", name)
        long = L.clean_name("A" * 500, "fr")
        self.assertLessEqual(len(long), L.NAME_MAX)
        self.assertEqual(L.clean_name("<>", "fr"), "fr")
        self.assertEqual(L.clean_name(None, "fr"), "fr")


class Config(unittest.TestCase):
    def cfg(self, **ov):
        return {"project": "p", "human_reviewers_team": "t", "overrides": ov}

    def test_shipped_config_is_overrides_only(self):
        self.assertEqual(set(CFG), {"note", "project", "human_reviewers_team", "overrides"})

    def test_unknown_override_field_refused(self):
        with self.assertRaisesRegex(L.LocaleError, "unknown field"):
            L.validate_shape(self.cfg(de={"weblate": "de"}))
        with self.assertRaisesRegex(L.LocaleError, "unknown key"):
            L.validate_shape({**self.cfg(), "locales": {}})

    def test_bad_values_refused(self):
        for ov in ({"de": {"min_coverage": 0}}, {"de": {"min_coverage": 2}}, {"de": {"human_spot_check": "yes"}},
                   {"de": {"script": "Zzzz"}}, {"pt-BR": {}}, {"de": []}, {"de": {"name": ""}},
                   {"pt_BR": {}, "pt_br": {}}):
            with self.assertRaises(L.LocaleError, msg=str(ov)):
                L.validate_shape(self.cfg(**ov))


class Fetch(unittest.TestCase):
    def test_client_list_ok_and_shapes(self):
        self.assertEqual(L.fetch_languages(FakeClient([{"code": "de", "name": "German"}]), "p"), {"de": "German"})
        self.assertEqual(L.fetch_languages(FakeClient({"results": [{"code": "de"}]}), "p"), {"de": ""})

    def test_fails_closed(self):
        for client in (FakeClient(None), FakeClient([]), FakeClient({"results": []}), FakeClient("x"),
                       FakeClient([{"name": "no code"}]), FakeClient([1]), FakeClient({}),
                       FakeClient(exc=WeblateError("HTTP 500")), FakeClient(exc=OSError("down"))):
            with self.assertRaises(L.LocaleError):
                L.fetch_languages(client, "p")

    def test_api_failure_means_no_spec(self):
        for client in (FakeClient(None), FakeClient([]), FakeClient(exc=WeblateError("x"))):
            with self.assertRaises(L.LocaleError):
                L.resolve_spec(client, CFG, "de")

    def test_public_fetch_needs_https_no_redirect(self):
        with self.assertRaises(L.LocaleError):
            L.fetch_languages_public("http://example.com", "p")
        with self.assertRaises(L.LocaleError):
            L.fetch_languages_public("", "p")
        with self.assertRaises(L.LocaleError):
            L.fetch_languages_public("ftp://x", "p")

    def serve(self, status, body, location=None):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen["auth"] = self.headers.get("Authorization")
                seen["path"] = self.path
                data = json.dumps(body).encode()
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}", seen

    def test_public_fetch_anonymous(self):
        url, seen = self.serve(200, [{"code": "de", "name": "German"}])
        self.assertEqual(L.fetch_languages_public(url, "universal-till"), {"de": "German"})
        self.assertIsNone(seen["auth"])
        self.assertEqual(seen["path"], "/api/projects/universal-till/languages/")

    def test_public_fetch_refuses_redirect_error_and_empty(self):
        for status, body, loc in ((302, {}, "http://evil.example/"), (404, {}, None), (500, {}, None), (200, [], None),
                                  (200, "junk", None)):
            url, _ = self.serve(status, body, loc)
            with self.assertRaises(L.LocaleError, msg=str(status)):
                L.fetch_languages_public(url, "p")


class Cli(unittest.TestCase):
    def run_cli(self, *args, env=None):
        out, err = io.StringIO(), io.StringIO()
        old = dict(os.environ)
        os.environ.update(env or {})
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = L.main(["--repo", ROOT, *args])
        finally:
            os.environ.clear()
            os.environ.update(old)
        return code, out.getvalue(), err.getvalue()

    def test_check_prints_tag_and_human_from_overrides_only(self):
        self.assertEqual(self.run_cli("--check", "de")[:2], (0, "tag=de\nhuman=true\n"))
        self.assertEqual(self.run_cli("--check", "tr")[1], "tag=tr\nhuman=true\n")
        self.assertEqual(self.run_cli("--check", "pt_BR")[1], "tag=pt-br\nhuman=false\n")
        self.assertEqual(self.run_cli("--check", "zh_Hans")[1], "tag=zh-hans\nhuman=false\n")

    def test_check_refuses_with_nothing_on_stdout(self):
        for bad in ("pt-BR", "de;ls", "zh", "xx"):
            code, out, err = self.run_cli("--check", bad)
            self.assertEqual((code, out), (1, ""), bad)
            self.assertIn("refused", err)

    def test_live_check(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = json.dumps([{"code": "pt_BR", "name": "Portuguese (Brazil)"}]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        env = {"WEBLATE_URL": f"http://127.0.0.1:{srv.server_address[1]}"}
        self.assertEqual(self.run_cli("--check", "pt_BR", "--live", env=env)[:2], (0, "tag=pt-br\nhuman=false\n"))
        self.assertEqual(self.run_cli("--check", "fr", "--live", env=env)[:2], (1, ""))
        # de has a human override but is not in this (fake) live list: not publishable.
        self.assertEqual(self.run_cli("--check", "de", "--live", env=env)[:2], (1, ""))
        self.assertEqual(self.run_cli("--check", "pt_BR", "--live", env={"WEBLATE_URL": ""})[:2], (1, ""))


if __name__ == "__main__":
    unittest.main()
