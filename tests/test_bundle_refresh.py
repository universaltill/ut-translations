"""When a bundle is rebuilt (tools/bundle_refresh.py, ut-docs#5479): the
stale/never/empty/current verdict, the opt-in list, and the workflow's shape
(no secret, publish-bundle is only started for the opted-in languages)."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bundle_refresh as R  # noqa: E402
import retry_server  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "refresh-bundles.yml")
PUBLISH_WF = os.path.join(ROOT, ".github", "workflows", "publish-bundle.yml")


def T(ymd, secs=0):
    return R.version_time(f"2.{ymd}.{secs}")


def row(code, when, approved=10):
    return {"code": code, "changed": when, "approved": approved}


class Classify(unittest.TestCase):
    def test_verdicts(self):
        rows = [row("de", T("20261030")), row("pt", T("20261104", 1)), row("tr", T("20261104", 4)),
                row("fa", T("20261104")), row("it", T("20261104"), 0), row("zh_Hans", T("20261101"))]
        versions = {"de": "2.20261104.1", "pt": "2.20261104.2", "tr": "2.20261104.3"}
        got = {c: s for c, _t, s, _v in R.classify(rows, versions)}
        self.assertEqual(got, {"de": "current", "pt": "current", "tr": "stale", "fa": "never",
                               "it": "empty", "zh_Hans": "never"})

    def test_unusable_inputs_err_towards_a_rebuild(self):
        got = R.classify([row("de", None)], {"de": "2.20261104.1"})
        self.assertEqual(got[0][2], "stale")
        got = R.classify([row("de", T("20261001"))], {"de": "garbage"})
        self.assertEqual(got[0][2], "never")

    def test_zh_hans_tag_matches_catalogue(self):
        got = R.classify([row("zh_Hans", T("20261101"))], {"zh-hans": "2.20261102.1"})
        self.assertEqual(got[0][:3], ("zh_Hans", "zh-hans", "current"))

    def test_only_opted_in_and_behind_languages_dispatch(self):
        rows = [("de", "de", "stale", "-"), ("tr", "tr", "never", "-"), ("fa", "fa", "current", "-"),
                ("it", "it", "empty", "-"), ("pt", "pt", "stale", "-")]
        self.assertEqual(R.to_dispatch(rows, "de, fa,it"), ["de"])
        self.assertEqual(R.to_dispatch(rows, ""), [])
        self.assertEqual(R.to_dispatch(rows, None), [])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if "/languages/" in self.path:
            body = {"results": [{"code": "tr", "last_change": "2026-11-04T06:39:26Z", "approved": 5}]}
        else:
            body = {"plugins": [{"version": "2.20261030.9", "availableLocales": ["tr"]}], "nextPageToken": ""}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TwoListings(Handler):
    def do_GET(self):
        body = {"plugins": [{"version": "2.20261104.5", "availableLocales": ["tr"]},
                            {"version": "2.20261030.9", "availableLocales": ["tr"]}]}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Live(unittest.TestCase):
    def test_reads_both_public_lists(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            rows = R.weblate_rows(base, "universal-till")
            self.assertEqual(rows, [{"code": "tr", "changed": R.version_time("2.20261104.23966"), "approved": 5}])
            self.assertEqual(R.published_versions(base), {"tr": "2.20261030.9"})
            self.assertEqual(R.classify(rows, R.published_versions(base))[0][2], "stale")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_newest_listing_of_a_tag_counts(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), TwoListings)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.assertEqual(R.published_versions(f"http://127.0.0.1:{srv.server_address[1]}"),
                             {"tr": "2.20261104.5"})
        finally:
            srv.shutdown()
            srv.server_close()

    def test_same_day_edit_after_the_build_is_stale_before_it_is_not(self):
        v = {"de": "2.20261104.43200"}  # built 12:00 UTC
        self.assertEqual(R.classify([row("de", T("20261104", 43199))], v)[0][2], "current")
        self.assertEqual(R.classify([row("de", T("20261104", 43201))], v)[0][2], "stale")

    def test_non_numeric_approved_is_empty_not_a_crash(self):
        old = R._get_json
        R._get_json = lambda url, timeout=30: [{"code": "de", "last_change": "x", "approved": "12"}]
        try:
            rows = R.weblate_rows("https://w.example", "p")
        finally:
            R._get_json = old
        self.assertEqual(R.classify(rows, {})[0][2], "empty")

    def test_main_prints_one_dispatch_line(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        old = {k: os.environ.get(k) for k in ("WEBLATE_URL", "MARKETPLACE_CATALOG_URL")}
        os.environ["WEBLATE_URL"] = os.environ["MARKETPLACE_CATALOG_URL"] = base
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(R.main(["--publish-list", "tr,fa"]), 0)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            srv.shutdown()
            srv.server_close()
        self.assertEqual(out.getvalue(), 'dispatch=["tr"]\n')

    def test_refuses_plain_http_off_loopback(self):
        with self.assertRaises(R.RefreshError):
            R.published_versions("http://example.com")


class GetJsonRetry(unittest.TestCase):
    def test_one_timeout_then_ok(self):
        url, hits = retry_server.serve(self, ["timeout", 200], {"a": 1})
        sleeps = []
        with redirect_stderr(io.StringIO()):
            self.assertEqual(R._get_json(url, timeout=0.2, sleep=sleeps.append), {"a": 1})
        self.assertEqual((len(hits), sleeps), (2, [5]))

    def test_4xx_not_retried(self):
        url, hits = retry_server.serve(self, [403])
        sleeps = []
        with self.assertRaises(R.RefreshError):
            R._get_json(url, timeout=0.2, sleep=sleeps.append)
        self.assertEqual((len(hits), sleeps), (1, []))

    def test_always_timeout_keeps_the_error_text(self):
        url, hits = retry_server.serve(self, ["timeout"])
        with redirect_stderr(io.StringIO()), self.assertRaises(R.RefreshError) as cm:
            R._get_json(url, timeout=0.2, sleep=lambda s: None)
        self.assertIn("can't read 127.0.0.1", str(cm.exception))
        self.assertEqual(len(hits), 4)


class WorkflowShape(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as f:
            self.text = f.read()

    def test_no_secret_and_least_privilege(self):
        self.assertNotIn("secrets.", self.text)
        self.assertIn("\npermissions: {}\n", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertEqual(len(re.findall(r"^\s+actions: write$", self.text, re.M)), 1)
        self.assertNotIn("self-hosted", self.text)

    def test_actions_are_pinned(self):
        for m in re.finditer(r"uses: (\S+)", self.text):
            self.assertRegex(m.group(1), r"@[0-9a-f]{40}$")

    def test_no_expression_in_a_script(self):
        for body in re.findall(r"run: \|\n((?:\s{10}.*\n?)+)", self.text):
            self.assertNotIn("${{", body)

    def test_dispatches_only_publish_bundle_on_main(self):
        self.assertIn("gh workflow run publish-bundle.yml", self.text)
        self.assertIn("--ref main", self.text)
        self.assertIn("vars.BUNDLE_AUTO_PUBLISH", self.text)

    def job_block(self, name):
        m = re.search(rf"^  {name}:\n((?:    .*\n|\n)+)", self.text, re.M)
        self.assertIsNotNone(m, name)
        return m.group(1)

    def test_dispatch_is_serial_and_spaced(self):
        d = self.job_block("dispatch")
        self.assertRegex(d, r"max-parallel: 1\n")
        self.assertIn("fail-fast: false", d)
        gap = int(re.search(r'DISPATCH_GAP: "(\d+)"', d).group(1))
        self.assertGreaterEqual(gap, 60)
        self.assertLess(d.index("gh workflow run"), d.index('sleep "$DISPATCH_GAP"'))
        timeout = int(re.search(r"timeout-minutes: (\d+)", d).group(1))
        self.assertGreater(timeout * 60, gap + 60)

    def test_plan_budget_covers_retries(self):
        timeout = int(re.search(r"timeout-minutes: (\d+)", self.job_block("plan")).group(1))
        self.assertGreaterEqual(timeout, 10)


# A stand-in for the gh CLI: `run list` prints $FAKE_RUNS, `workflow run`
# records its arguments in $GH_CALLS. Anything else fails loudly.
FAKE_GH = """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "run list" ]]; then
  args=" $* "
  for need in " --workflow publish-bundle.yml " " --status " "displayTitle" "status" "url"; do
    [[ "$args" == *"$need"* ]] || { echo "fake gh: run list without $need" >&2; exit 64; }
  done
  st=""; prev=""; for a in "$@"; do [[ "$prev" == "--status" ]] && st="$a"; prev="$a"; done
  jq --arg s "$st" '[.[] | select(.status == $s)]' "$FAKE_RUNS"; exit 0
fi
if [[ "$1 $2" == "workflow run" ]]; then shift 2; echo "$*" >> "$GH_CALLS"; exit 0; fi
echo "fake gh: unexpected $*" >&2; exit 64
"""


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs bash and jq (GitHub runners have both)")
class DispatchSkipsPendingRuns(unittest.TestCase):
    """ut-docs#6114: the real `dispatch` step script, run against a fake gh."""

    def setUp(self):
        with open(WF, encoding="utf-8") as f:
            text = f.read()
        dispatch = re.search(r"^  dispatch:\n((?:    .*\n|\n)+)", text, re.M).group(1)
        bodies = [textwrap.dedent(b) for b in re.findall(r"run: \|\n((?:\s{10}.*\n?|\n)+)", dispatch)]
        self.script = next(b for b in bodies if "gh workflow run" in b)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        gh = os.path.join(self.tmp, "gh")
        with open(gh, "w", encoding="utf-8") as f:
            f.write(FAKE_GH)
        os.chmod(gh, 0o755)

    def dispatch(self, locale, runs):
        runs_file = os.path.join(self.tmp, "runs.json")
        calls = os.path.join(self.tmp, "calls")
        with open(runs_file, "w", encoding="utf-8") as f:
            json.dump(runs, f)
        if os.path.exists(calls):
            os.remove(calls)
        env = dict(os.environ, PATH=self.tmp + os.pathsep + os.environ["PATH"], FAKE_RUNS=runs_file,
                   GH_CALLS=calls, LOCALE=locale, REPO="universaltill/ut-translations",
                   DISPATCH_GAP="0", GH_TOKEN="dummy")
        p = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", self.script],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        started = []
        if os.path.exists(calls):
            with open(calls, encoding="utf-8") as f:
                started = f.read().splitlines()
        return started, p.stdout

    @staticmethod
    def run_(title, status, n=1):
        return {"databaseId": n, "status": status, "displayTitle": title,
                "url": f"https://github.com/universaltill/ut-translations/actions/runs/{n}"}

    def test_waiting_run_is_not_dispatched_again_and_is_named(self):
        runs = [self.run_("publish-bundle de stable", "waiting", 41969338316)]
        started, out = self.dispatch("de", runs)
        self.assertEqual(started, [])
        self.assertIn("::notice::", out)
        self.assertIn("actions/runs/41969338316", out)

    def test_every_active_status_skips(self):
        for status in ("requested", "waiting", "pending", "queued", "in_progress"):
            with self.subTest(status=status):
                started, _ = self.dispatch("tr", [self.run_("publish-bundle tr stable", status)])
                self.assertEqual(started, [])

    def test_other_languages_still_dispatch(self):
        runs = [self.run_("publish-bundle de stable", "waiting"),
                self.run_("publish-bundle pt_BR stable", "waiting", 2)]
        for locale in ("fr", "pt"):  # pt must not match pt_BR's run
            with self.subTest(locale=locale):
                started, _ = self.dispatch(locale, runs)
                self.assertEqual(started, [f"publish-bundle.yml --repo universaltill/ut-translations --ref main "
                                           f"-f locale={locale} -f channel=stable -f publish=true"])

    def test_match_is_found_anywhere_in_a_mixed_list(self):
        runs = [self.run_("publish-bundle fr stable", "completed", 9),
                self.run_("publish-bundle ar stable", "queued", 8),
                self.run_("publish-bundle de stable", "completed", 7),
                self.run_("publish-bundle de stable", "waiting", 3)]
        started, out = self.dispatch("de", runs)
        self.assertEqual(started, [])
        self.assertIn("actions/runs/3", out)

    def test_finished_runs_do_not_block(self):
        runs = [self.run_("publish-bundle de stable", "completed")]
        started, _ = self.dispatch("de", runs)
        self.assertEqual(len(started), 1)

    def test_no_runs_at_all_dispatches(self):
        started, _ = self.dispatch("ar", [])
        self.assertEqual(len(started), 1)

    def test_publish_run_title_carries_the_locale(self):
        with open(PUBLISH_WF, encoding="utf-8") as f:
            text = f.read()
        self.assertRegex(text, r"(?m)^run-name: publish-bundle \$\{\{ inputs\.locale \}\} ")


if __name__ == "__main__":
    unittest.main()
