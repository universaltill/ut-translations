"""The Gemini reviewer backend (ut-docs#5532): the adapter against a fake
Generative Language API on 127.0.0.1, the key lookup, the model family, the
account, and the runner rules re-proved under the Gemini account (legal
stays needs-human, a model only escalates, text is never edited). Nothing
here talks to generativelanguage.googleapis.com or reads a Keychain."""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import build_bundle as bb  # noqa: E402
import review_checks as rc  # noqa: E402
import review_model as rm  # noqa: E402
import review_prompt as rp  # noqa: E402
import review_runner as rr  # noqa: E402
import test_review_runner as trr  # noqa: E402

KEY = "AIza-test-key-0123456789"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GEMINI_MODEL = "gemini/gemini-2.5-pro"


def reply(text, version="gemini-2.5-pro", finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}],
            "modelVersion": version}


class FakeGemini:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def serve(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                fake.calls.append((self.path, self.headers, json.loads(self.rfile.read(n))))
                code, body, headers = fake.responses.pop(0) if fake.responses else (500, {}, {})
                data = json.dumps(body).encode()
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


class GeminiAdapter(unittest.TestCase):
    def start(self, responses, **kw):
        self.fake = FakeGemini(responses)
        srv = self.fake.serve()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.sleeps = []
        return rm.GeminiAdapter(KEY, base_url=f"http://127.0.0.1:{srv.server_address[1]}",
                                sleep=self.sleeps.append, **kw)

    def test_records_the_answering_model_and_sends_the_key_only_in_a_header(self):
        a = self.start([(200, reply('{"verdicts": []}', version="gemini-2.5-pro-001"), {})])
        text, model = a.complete("sys", "user")
        self.assertEqual((text, model), ('{"verdicts": []}', "gemini/gemini-2.5-pro-001"))
        path, headers, body = self.fake.calls[0]
        self.assertEqual(path, "/v1beta/models/gemini-2.5-pro:generateContent")
        self.assertEqual(headers.get("x-goog-api-key"), KEY)
        self.assertNotIn(KEY, path)
        self.assertNotIn(KEY, json.dumps(body))
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "sys")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "user")
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        gc = body["generationConfig"]
        self.assertLess(gc["thinkingConfig"]["thinkingBudget"], gc["maxOutputTokens"])

    def test_504_is_retried_too(self):
        a = self.start([(504, {}, {}), (200, reply("{}"), {})])
        self.assertEqual(a.complete("s", "u")[0], "{}")

    def test_retries_on_429_and_503_honouring_retry_after(self):
        a = self.start([(429, {}, {"retry-after": "3"}), (503, {}, {}), (200, reply("{}"), {})])
        self.assertEqual(a.complete("s", "u")[0], "{}")
        self.assertEqual(self.sleeps[0], 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_gives_up_after_five_tries(self):
        a = self.start([(429, {}, {"retry-after": "1"})] * 6)
        with self.assertRaises(rm.ModelError):
            a.complete("s", "u")
        self.assertEqual(len(self.fake.calls), 5)

    def test_a_truncated_or_blocked_reply_is_an_error_not_a_verdict(self):
        for finish in ("MAX_TOKENS", "SAFETY", "RECITATION", "OTHER"):
            a = self.start([(200, reply("{}", finish=finish), {})])
            with self.assertRaises(rm.ModelError, msg=finish):
                a.complete("s", "u")
        a = self.start([(200, {"promptFeedback": {"blockReason": "SAFETY"}}, {})])
        with self.assertRaises(rm.ModelError):
            a.complete("s", "u")

    def test_a_reply_that_names_no_model_is_an_error(self):
        body = reply("{}")
        del body["modelVersion"]
        a = self.start([(200, body, {})])
        with self.assertRaises(rm.ModelError):
            a.complete("s", "u")

    def test_the_key_is_never_in_an_error(self):
        a = self.start([(403, {"error": {"message": f"bad key {KEY}"}}, {})])
        with self.assertRaises(rm.ModelError) as cm:
            a.complete("s", "u")
        self.assertNotIn(KEY, str(cm.exception))
        self.assertIn("403", str(cm.exception))

    def test_a_model_name_cannot_rewrite_the_path(self):
        for bad in ("../x", "a/b", "m?key=1", ""):
            with self.assertRaises(rm.ModelError, msg=bad):
                rm.GeminiAdapter(KEY, model=bad)

    def test_plain_http_to_another_host_and_a_missing_key_are_refused(self):
        with self.assertRaises(rm.ModelError):
            rm.GeminiAdapter(KEY, base_url="http://generativelanguage.googleapis.com")
        with self.assertRaises(rm.ModelError):
            rm.GeminiAdapter("", base_url="https://generativelanguage.googleapis.com")

    def test_redirects_are_not_followed(self):
        a = self.start([(302, {}, {"Location": "http://127.0.0.1:1/steal"})])
        with self.assertRaises(rm.ModelError):
            a.complete("s", "u")
        self.assertEqual(len(self.fake.calls), 1)


class KeyLookup(unittest.TestCase):
    def test_env_first_then_the_keychain_item(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "from-env", "UTT_NO_KEYCHAIN": "1"}):
            self.assertEqual(rr.gemini_key(), "from-env")
        calls = []

        def fake_check_output(cmd, **kw):
            calls.append(cmd)
            return "from-keychain\n"
        env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "UTT_NO_KEYCHAIN")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(rr.sys, "platform", "darwin"), \
                mock.patch.object(rr.subprocess, "check_output", fake_check_output):
            self.assertEqual(rr.gemini_key(), "from-keychain")
        self.assertIn("weblate-review-gemini", calls[0])

    def test_no_key_anywhere_is_none(self):
        with mock.patch.dict(os.environ, {"UTT_NO_KEYCHAIN": "1"}):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertIsNone(rr.gemini_key())


class Family(unittest.TestCase):
    def test_gemini_is_its_own_family(self):
        for m in ("gemini", "gemini/gemini-2.5-pro", "gemini-2.5-pro", "google/gemini-2.5-pro"):
            self.assertEqual(rc.family(m), "gemini", m)
        self.assertEqual(len({rc.family(x) for x in ("claude", "codex", "gemini", "local-ollama")}), 4)


class AccountConfig(unittest.TestCase):
    def test_the_gemini_reviewer_is_registered(self):
        with open(os.path.join(ROOT, "config", "ai-accounts.json"), encoding="utf-8") as f:
            a = json.load(f)["accounts"].get("bot-universal-till-ai-reviewer-gemini")
        self.assertIsNotNone(a)
        self.assertEqual((a["model"], a["role"]), ("gemini", "reviewer"))

    def test_its_weblate_token_has_its_own_keychain_item(self):
        with open(os.path.join(ROOT, "config", "ai-accounts.json"), encoding="utf-8") as f:
            accounts = json.load(f)["accounts"]
        self.assertEqual(rr.reviewer_keychain("bot-universal-till-ai-reviewer-gemini", accounts),
                         "weblate-gemini-reviewer")
        self.assertEqual(rr.reviewer_keychain(rr.DEFAULT_ACCOUNT, accounts), "weblate-claude-reviewer")


class GeminiRunner(trr.RunnerCase):
    """The runner rules under the Gemini account."""

    @classmethod
    def setUpClass(cls):
        trr.ACCOUNTS["rv-gemini"] = {"model": "gemini", "role": "reviewer", "keychain": "weblate-gemini-reviewer",
                                      "token_env": "WEBLATE_GEMINI_REVIEWER_TOKEN"}

    @classmethod
    def tearDownClass(cls):
        trr.ACCOUNTS.pop("rv-gemini", None)

    def setUp(self):
        super().setUp()
        self.fake.add_user("rv-gemini", team=False, bot=True)
        self.fake.actor = "rv-gemini"
        # The Claude reviewer's generic token must NOT be what the Gemini account writes with.
        patcher = mock.patch.dict(os.environ, {"WEBLATE_GEMINI_REVIEWER_TOKEN": trr.TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)

    def apply_g(self, *extra):
        return self.apply(*extra, account="rv-gemini")

    def edits(self):
        """Writes that change a unit's text (a PATCH whose target differs)."""
        return [w for w in self.fake.writes if w[0] == "PATCH" and isinstance(w[2], dict)
                and w[2].get("target") not in (None, ["Somme"])]

    def test_review_command_with_the_gemini_provider_records_the_gemini_model(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        fake = FakeGemini([])
        path = self.batches()[0]
        batch = bb.load_json(path)
        out = {"schema": 1, "batch_sha": batch["batch_sha"], "prompt_version": rp.PROMPT_VERSION,
               "verdicts": [{"unit": u["id"], "tgt_sha": u["tgt_sha"], "verdict": "approve", "reasons": [],
                             "correction": None, "note": ""} for u in batch["units"]]}
        fake.responses.append((200, reply(json.dumps(out)), {}))
        srv = fake.serve()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        env = {"GEMINI_API_KEY": KEY, "UTT_NO_KEYCHAIN": "1"}
        with mock.patch.dict(os.environ, env):
            code = self.run_cli("review", "--out", self.out, "--provider", "gemini",
                                "--api-base", f"http://127.0.0.1:{srv.server_address[1]}")
        self.assertEqual(code, 0, self.stderr)
        doc = bb.load_json(os.path.join(self.out, "verdicts", os.path.basename(path)))
        self.assertEqual(doc["model"], GEMINI_MODEL)
        self.assertNotIn(KEY, self.stdout + self.stderr + json.dumps(doc))
        self.assertEqual(self.apply_g("--write"), 0, self.stderr)
        self.assertEqual(self.by_key()["pos.total"]["action"], "approved")
        self.assertEqual(self.by_key()["pos.total"]["reviewer_model"], GEMINI_MODEL)

    def test_review_command_without_a_key_fails_closed(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        with mock.patch.dict(os.environ, {"UTT_NO_KEYCHAIN": "1"}):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertEqual(self.run_cli("review", "--out", self.out, "--provider", "gemini"), 2)
        self.assertIn("GEMINI_API_KEY", self.stderr)

    def test_approve_changes_state_only_never_the_text(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        self.verdicts(model=GEMINI_MODEL)
        self.assertEqual(self.apply_g("--write"), 0, self.stderr)
        self.assertEqual(self.edits(), [])
        self.assertTrue(self.non_get())

    def test_legal_money_and_receipt_strings_stay_needs_human(self):
        self.unit("receipt.vat", "TVA incluse", [(trr.NEW, "tr-local", "TVA incluse"), (trr.APPROVE, trr.BOOT, "TVA incluse")],
                  area="receipts-and-legal")
        self.bootstrap("pos.tax_rate", "Taux", source="Rate")
        self.bootstrap("pos.total", "Montant", source="Enter the amount")
        self.export()
        self.verdicts(model=GEMINI_MODEL)
        decided = bb.load_json(os.path.join(self.out, "decided.json"))
        forged = {"schema": 1, "batch_sha": "x", "prompt_version": rp.PROMPT_VERSION, "model": GEMINI_MODEL,
                  "verdicts": [{"unit": u["id"], "tgt_sha": u["tgt_sha"], "verdict": "approve", "reasons": [],
                                "correction": None, "note": ""} for u in decided["units"]]}
        os.makedirs(os.path.join(self.out, "verdicts"), exist_ok=True)
        with open(os.path.join(self.out, "verdicts", "decided.json"), "w") as f:
            json.dump(forged, f)
        self.assertEqual(self.apply_g("--write"), 0, self.stderr)
        by = self.by_key()
        for key in ("receipt.vat", "pos.tax_rate", "pos.total"):
            self.assertEqual(by[key]["verdict"], "needs-human", key)
        self.assertEqual(self.non_get(), [])

    def test_a_gemini_needs_human_escalates_and_writes_nothing(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        self.verdicts("needs-human", model=GEMINI_MODEL)
        self.assertEqual(self.apply_g("--write"), 0, self.stderr)
        self.assertEqual(self.by_key()["pos.total"]["verdict"], "needs-human")
        self.assertEqual(self.non_get(), [])

    def test_a_gemini_needs_fix_is_a_suggestion_never_an_edit(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        self.verdicts("needs-fix", model=GEMINI_MODEL, overrides={"pos.total": {"correction": ["Totale"]}})
        self.assertEqual(self.apply_g("--write"), 0, self.stderr)
        self.assertEqual(self.edits(), [])
        self.assertEqual([w[0] for w in self.fake.writes if w[0] == "PATCH"], [])
        self.assertTrue(any(w[1].endswith("/suggestions/") and w[2]["target"] == ["Totale"]
                            for w in self.fake.writes))

    def test_a_claude_verdict_file_cannot_be_recorded_under_the_gemini_account(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        self.verdicts(model=trr.MODEL)
        self.assertEqual(self.apply_g("--write"), 2)
        self.assertEqual(self.non_get(), [])

    def test_a_gemini_verdict_file_cannot_be_recorded_under_the_claude_account(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        self.verdicts(model=GEMINI_MODEL)
        self.assertEqual(self.apply("--write", account="rv-claude"), 2)
        self.assertEqual(self.non_get(), [])

    def test_a_string_a_gemini_account_wrote_is_not_reviewed_by_gemini(self):
        self.unit("pos.total", "Somme", [(trr.NEW, "rv-gemini", "Somme")])
        self.export()
        self.verdicts(model=GEMINI_MODEL)
        self.assertEqual(self.apply_g("--write"), 0, self.stderr)
        self.assertEqual(self.non_get(), [])

    def test_the_write_token_comes_from_the_gemini_keychain_item(self):
        seen = []
        orig = rr.secret
        with mock.patch.object(rr, "secret", lambda svc, *e: (seen.append((svc, e)), orig(svc, *e))[1]):
            self.bootstrap("pos.total", "Somme")
            self.export()
            self.verdicts(model=GEMINI_MODEL)
            self.apply_g("--write")
        self.assertIn(("weblate-gemini-reviewer", ("WEBLATE_GEMINI_REVIEWER_TOKEN",)), seen)

    def test_the_generic_claude_reviewer_token_is_never_used_for_the_gemini_account(self):
        self.bootstrap("pos.total", "Somme")
        self.export()
        self.verdicts(model=GEMINI_MODEL)
        os.environ.pop("WEBLATE_GEMINI_REVIEWER_TOKEN")  # only the generic one is set now
        self.assertEqual(self.apply_g("--write"), 2)
        self.assertIn("no Weblate reviewer token", self.stderr + self.stdout)
        self.assertEqual(self.non_get(), [])


if __name__ == "__main__":
    unittest.main()
