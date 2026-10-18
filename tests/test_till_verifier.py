"""A built bundle goes through the till's real verifier and locale loader
(ut-docs#4367 AC: a tampered bundle is rejected by the till's verifier).

tools/till_verify.py runs tools/till_harness/utt_bundle_harness_test.go
inside a throwaway copy of universal-till: the till's own
MarketplaceInstaller.installBundleFile and Manager.syncLocales, not a
re-implementation. Needs Go and a universal-till checkout ($UTT_TILL_SOURCE,
else ../universal-till). CI sets UTT_REQUIRE_TILL=1, so a missing checkout
fails there instead of skipping."""

import contextlib
import datetime as dt
import gzip
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import build_bundle as bb  # noqa: E402
import till_verify  # noqa: E402
from bundle_fixtures import TOKEN, BundleCase, git, make_till, side_commit, till_source  # noqa: E402
from provision_weblate import WeblateClient  # noqa: E402

REQUIRE = os.environ.get("UTT_REQUIRE_TILL") == "1"
AVAILABLE = bool(till_source()) and shutil.which("go") is not None
NOW = dt.datetime(2026, 10, 16, 12, 30, 5, tzinfo=dt.timezone.utc)


def rewrite(path, name, edit):
    """Copy of the bundle at `path` with one entry edited (build_bundle's
    own deterministic writer, so only that entry differs)."""
    with tarfile.open(path) as tf:
        files = {m.name: tf.extractfile(m).read().decode() for m in tf.getmembers()}
    files[name] = edit(files[name])
    out = path.replace(".tar.gz", ".edited.tar.gz")
    with open(out, "wb") as f:
        f.write(bb.deterministic_targz(files, 0))
    return out


class TillVerifier(BundleCase, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AVAILABLE:
            if REQUIRE:
                raise AssertionError("UTT_REQUIRE_TILL=1 but no universal-till checkout or no go")
            raise unittest.SkipTest("needs go and a universal-till checkout (UTT_TILL_SOURCE)")

    def setUp(self):
        self.make({"ai-tr-opus": {"model": "claude-opus-5.5", "role": "translator"}})
        self.fake.add_unit("sale", "tr", "pos.total", "Total", "Toplam",
                           history=[(bb.A_NEW, "alice", "Toplam"), (bb.A_APPROVE, "bob", "Toplam")])
        self.fake.add_unit("sale", "tr", "pos.items", "%d items", "%d ürün",
                           history=[(bb.A_NEW, "alice", "%d ürün"), (bb.A_APPROVE, "bob", "%d ürün")])
        client = WeblateClient(self.fake.base(), TOKEN)
        self.report = bb.Builder(self.repo, "tr", client, self.till, now=NOW).build(self.out)
        self.bundle = os.path.join(self.out, self.report["bundle"])
        self.assertEqual(self.report["bundle_json"]["approved_count"], 2)

    def verify(self, bundle, locale="tr"):
        return till_verify.verify(till_source(), "HEAD", bundle, locale)

    def test_bundle_installs_loads_and_tampered_copies_are_refused(self):
        ok, out = self.verify(self.bundle)
        self.assertTrue(ok, out[-3000:])
        for sub in ("signed_bundle_installs_and_loads_as_a_fresh_first-party_layer",
                    "unsigned_bundle_is_refused",
                    "tampered_locales/tr.json_is_refused",
                    "tampered_bundle.json_is_refused",
                    "tampered_manifest_is_refused_even_with_a_matching_checksum",
                    "swapped_locale_with_a_matching_checksum_is_refused_by_the_content_digest",
                    "signature_from_another_key_is_refused"):
            self.assertIn(f"--- PASS: TestUTTBundleTillVerifier/{sub}", out)

    def test_harness_fails_a_bundle_the_till_would_not_load_as_fresh(self):
        # Negative control: the harness is not a tautology. A bundle.json
        # without an RFC 3339 generated_at is never "fresh" on a till.
        bad = rewrite(self.bundle, "bundle.json",
                      lambda t: json.dumps({**json.loads(t), "generated_at": "16/10/2026"}))
        ok, out = self.verify(bad)
        self.assertFalse(ok)
        self.assertIn("generated_at", out)

    def test_harness_fails_a_manifest_the_till_refuses(self):
        bad = rewrite(self.bundle, "manifest.json",
                      lambda t: json.dumps({**json.loads(t), "canonical_type": "lang"}))
        ok, out = self.verify(bad)
        self.assertFalse(ok)
        self.assertIn("refused the untampered signed bundle", out)

    def test_harness_fails_when_the_locale_file_name_does_not_match(self):
        ok, out = self.verify(self.bundle, locale="de")
        self.assertFalse(ok)

    def test_deterministic_tar_is_a_plain_gzip(self):
        with open(self.bundle, "rb") as f:
            raw = f.read()
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(raw))) as tf:
            self.assertEqual(len(tf.getmembers()), 3)


@unittest.skipUnless(till_source(), "needs a universal-till checkout for the guard copies")
class ReleasedCommit(unittest.TestCase):
    """Review finding 2(a): GitHub serves a fork's commit by sha from the
    parent repo, so the commit the till code is taken from must be on
    universal-till main or be a v* release tag's commit."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.till, self.first = make_till(tmp.name)
        self.main = git(self.till, "rev-parse", "HEAD")

    def test_main_commits_pass(self):
        self.assertEqual(till_verify.require_released_commit(self.till, self.main), self.main)
        self.assertEqual(till_verify.require_released_commit(self.till, "HEAD"), self.main)

    def test_a_commit_off_main_is_refused(self):
        fork = side_commit(self.till)
        with self.assertRaisesRegex(till_verify.VerifyError, "not on universal-till main"):
            till_verify.require_released_commit(self.till, fork)

    def test_a_release_tag_commit_off_main_passes(self):
        rel = side_commit(self.till)
        git(self.till, "tag", "-a", "-m", "release", "v0.34.8", rel)
        self.assertEqual(till_verify.require_released_commit(self.till, rel), rel)

    def test_a_non_release_tag_does_not_count(self):
        fork = side_commit(self.till)
        git(self.till, "tag", "fork-v1", fork)
        git(self.till, "tag", "v0.35.0-rc1", fork)
        with self.assertRaises(till_verify.VerifyError):
            till_verify.require_released_commit(self.till, fork)

    def test_no_origin_main_fails_closed(self):
        git(self.till, "update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaisesRegex(till_verify.VerifyError, "origin/main"):
            till_verify.require_released_commit(self.till, self.main)

    def test_verify_refuses_before_running_anything(self):
        fork = side_commit(self.till)
        with self.assertRaisesRegex(till_verify.VerifyError, "not on universal-till main"):
            till_verify.verify(self.till, fork, "/nonexistent.tar.gz", "tr")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = till_verify.main(["--till", self.till, "--commit", fork, "--bundle", "/x", "--locale", "tr"])
        self.assertEqual(code, 2)
        self.assertIn("not on universal-till main", err.getvalue())


if __name__ == "__main__":
    unittest.main()
