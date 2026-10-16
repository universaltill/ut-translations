"""Sync job: reads universal-till (a real git repo fixture, served over a
file path exactly as the public clone URL would be), keeps the union over the
supported lines, and commits only when the generated files change."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import sync_english as s  # noqa: E402

AREAS = {
    "default_area": "common",
    "legal_namespaces": ["receipt"],
    "areas": {
        "sale": {"description": "Sale.", "namespaces": ["pos"]},
        "receipts-and-legal": {"description": "Receipts.", "namespaces": ["receipt"]},
        "common": {"description": "Shared.", "namespaces": ["common"]},
    },
}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def init_repo(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "commit.gpgsign", "false")


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def commit_till(till, en, meta=None, areas=AREAS, tag=None):
    write_json(os.path.join(till, "web/locales/en.json"), en)
    if meta is not None:
        write_json(os.path.join(till, "web/i18n-meta/en.meta.json"), meta)
        write_json(os.path.join(till, "web/i18n-meta/areas.json"), areas)
    git(till, "add", "-A")
    git(till, "commit", "-q", "--allow-empty", "-m", "change")
    if tag:
        git(till, "tag", tag)


class ResolveLines(unittest.TestCase):
    TAGS = ["v0.33.0", "v0.33.2", "v0.33.10", "v0.34.0", "v0.34.1-rc1", "v0.34.1", "v1.0.0", "nightly"]

    def test_highest_final_release_per_line(self):
        self.assertEqual(s.resolve_lines(self.TAGS, ["0.33", "0.34"]), {"0.33": "v0.33.10", "0.34": "v0.34.1"})

    def test_unknown_line_is_an_error(self):
        with self.assertRaises(s.SyncError):
            s.resolve_lines(self.TAGS, ["0.35"])

    def test_bad_line_name_is_an_error(self):
        with self.assertRaises(s.SyncError):
            s.resolve_lines(self.TAGS, ["0.34.1"])


class SyncJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.till = os.path.join(self.tmp.name, "universal-till")
        self.tr = os.path.join(self.tmp.name, "ut-translations")
        init_repo(self.till)
        # 0.33 line: before the metadata existed (no en.meta.json at all).
        commit_till(self.till, {"pos.pay": "Pay", "pos.legacy": "Legacy %d"}, tag="v0.33.1")
        commit_till(self.till, {"pos.pay": "Pay now", "receipt.footer": "Thanks"},
                    {"pos.pay": {"description": "Pay button.", "review": "standard", "max_length": 20}},
                    tag="v0.34.0")
        commit_till(self.till, {"pos.pay": "Pay now", "receipt.footer": "Thanks", "common.ok": "OK"},
                    {"pos.pay": {"description": "Pay button.", "review": "standard", "max_length": 20}})
        init_repo(self.tr)
        write_json(os.path.join(self.tr, "config/lines.json"),
                   {"source": self.till, "branch": "main", "supported_lines": ["0.33", "0.34"]})
        git(self.tr, "add", "-A")
        git(self.tr, "commit", "-q", "-m", "init")
        self.till_head = git(self.till, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self):
        return s.sync(self.tr, s.load_config(self.tr), commit=True, log=lambda *_: None)

    def read(self, rel):
        with open(os.path.join(self.tr, rel), encoding="utf-8") as f:
            return json.load(f)

    def test_first_sync_writes_union_and_commits(self):
        before = git(self.tr, "rev-parse", "HEAD")
        self.assertTrue(self.run_sync())
        self.assertNotEqual(git(self.tr, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.tr, "status", "--porcelain"), "")
        sale = self.read("sale/en.json")
        # pos.legacy only exists on the 0.33 line and must stay translatable.
        self.assertEqual(sale, {"pos.legacy": "Legacy %d", "pos.pay": "Pay now"})
        meta = self.read("meta/sale.json")
        self.assertEqual(meta["pos.legacy"]["lines"], ["0.33"])
        self.assertEqual(meta["pos.pay"]["lines"], ["main", "0.34", "0.33"])
        self.assertEqual(meta["pos.pay"]["flags"], "max-length:20")
        self.assertEqual(self.read("meta/receipts-and-legal.json")["receipt.footer"]["labels"], ["legal"])
        src = self.read("meta/source.json")
        self.assertEqual(src["main"]["commit"], self.till_head)
        self.assertEqual(src["lines"]["0.33"]["tag"], "v0.33.1")
        self.assertEqual(src["lines"]["0.34"]["tag"], "v0.34.0")
        self.assertIn(self.till_head[:12], git(self.tr, "log", "-1", "--format=%B"))

    def test_second_sync_is_a_no_op(self):
        self.assertTrue(self.run_sync())
        head = git(self.tr, "rev-parse", "HEAD")
        self.assertFalse(self.run_sync())
        self.assertEqual(git(self.tr, "rev-parse", "HEAD"), head)

    def test_till_commit_without_english_change_is_a_no_op(self):
        self.run_sync()
        head = git(self.tr, "rev-parse", "HEAD")
        commit_till(self.till, {"pos.pay": "Pay now", "receipt.footer": "Thanks", "common.ok": "OK"},
                    {"pos.pay": {"description": "Pay button.", "review": "standard", "max_length": 20}})
        self.assertFalse(self.run_sync())
        self.assertEqual(git(self.tr, "rev-parse", "HEAD"), head)

    def test_metadata_change_alone_commits(self):
        self.run_sync()
        commit_till(self.till, {"pos.pay": "Pay now", "receipt.footer": "Thanks", "common.ok": "OK"},
                    {"pos.pay": {"description": "The pay button.", "review": "standard", "max_length": 20}})
        self.assertTrue(self.run_sync())
        self.assertEqual(self.read("meta/sale.json")["pos.pay"]["explanation"], "The pay button.")

    def test_translations_written_by_weblate_are_left_alone(self):
        self.run_sync()
        write_json(os.path.join(self.tr, "sale/de.json"), {"pos.pay": "Jetzt zahlen"})
        git(self.tr, "add", "-A")
        git(self.tr, "commit", "-q", "-m", "Translated using Weblate (German)")
        commit_till(self.till, {"pos.pay": "Pay now", "pos.new": "New", "receipt.footer": "Thanks", "common.ok": "OK"},
                    {"pos.pay": {"description": "Pay button.", "review": "standard", "max_length": 20}})
        self.assertTrue(self.run_sync())
        self.assertEqual(self.read("sale/de.json"), {"pos.pay": "Jetzt zahlen"})
        self.assertIn("pos.new", self.read("sale/en.json"))

    def test_dropping_a_line_drops_its_only_keys(self):
        self.run_sync()
        write_json(os.path.join(self.tr, "config/lines.json"),
                   {"source": self.till, "branch": "main", "supported_lines": ["0.34"]})
        self.assertTrue(self.run_sync())
        self.assertNotIn("pos.legacy", self.read("sale/en.json"))

    def test_never_writes_to_the_till_repo(self):
        refs_before = git(self.till, "for-each-ref")
        self.run_sync()
        self.assertEqual(git(self.till, "for-each-ref"), refs_before)
        self.assertEqual(git(self.till, "status", "--porcelain"), "")

    def test_bad_area_id_fails_the_sync_and_writes_nothing(self):
        bad = {"default_area": "common", "legal_namespaces": [],
               "areas": {"common": {"namespaces": ["common"]}, "../escape": {"namespaces": ["pos"]}}}
        commit_till(self.till, {"pos.pay": "Pay now", "common.ok": "OK"}, {}, areas=bad)
        head = git(self.tr, "rev-parse", "HEAD")
        with self.assertRaises(s.SyncError) as cm:
            self.run_sync()
        self.assertIn("../escape", str(cm.exception))
        self.assertEqual(git(self.tr, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.tr, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape")))

    def test_no_commit_mode_leaves_changes_unstaged(self):
        head = git(self.tr, "rev-parse", "HEAD")
        self.assertTrue(s.sync(self.tr, s.load_config(self.tr), commit=False, log=lambda *_: None))
        self.assertEqual(git(self.tr, "rev-parse", "HEAD"), head)
        self.assertNotEqual(git(self.tr, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
