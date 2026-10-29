"""Which paths the English sync may carry through an auto-merge PR
(ut-docs#4367, owner decision Option A): only <area>/en.json and meta/*.json.
Anything else must refuse, so the auto-merge gate lets no code or config through."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import sync_english as s  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


AREAS = {"sale", "receipts-and-legal", "common"}


class AutoMergePaths(unittest.TestCase):
    def ok(self, *paths):
        allowed, bad = s.auto_merge_allowed(paths, AREAS)
        self.assertEqual(bad, [], paths)
        self.assertTrue(allowed, paths)

    def refuse(self, *paths):
        allowed, bad = s.auto_merge_allowed(paths, AREAS)
        self.assertFalse(allowed, paths)
        self.assertTrue(bad, paths)
        return bad

    def test_sync_owned_paths_pass(self):
        self.ok("sale/en.json", "receipts-and-legal/en.json", "meta/sale.json",
                "meta/areas.json", "meta/source.json", "common/en.json")

    def test_code_and_config_refuse(self):
        for p in ("tools/sync_english.py", ".github/workflows/sync-english.yml", ".github/CODEOWNERS",
                  "config/bundles.json", "config/lines.json", "tools/build_bundle.py", "scripts/ci/x.sh",
                  "tests/test_sync.py", "README.md", "docs/code-reviews/x.md", "sale/fr.json",
                  "tools/en.json", ".github/en.json", "config/en.json", "scripts/en.json"):
            self.refuse(p)

    def test_mixed_diff_refuses_and_names_offenders_only(self):
        bad = self.refuse("sale/en.json", "meta/source.json", "tools/sync_english.py")
        self.assertEqual(bad, ["tools/sync_english.py"])

    def test_look_alikes_refuse(self):
        for p in ("meta-evil/x.json", "meta-evil/en.json", "xx/en.json.bak", "sale/en.json.bak",
                  "sale/en.jsonx", "sale/sub/en.json", "en.json", "meta/sub/x.json", "meta/x.py",
                  "meta/x.json.sh", "meta", "meta/", "Sale/en.json", "sale/EN.json", "sale/en.JSON",
                  "sale /en.json", "sale/en.json ", " sale/en.json", "sale/en.json\n", "sale/en.json\nx"):
            self.refuse(p)

    def test_traversal_and_odd_paths_refuse(self):
        for p in ("../sale/en.json", "sale/../tools/x.py", "meta/../tools/x.py", "meta/..", "./sale/en.json",
                  "/sale/en.json", "/meta/x.json", "sale//en.json", "meta//x.json", "sale\\en.json",
                  "meta\\x.json", "..", ".", "", "sale/en.json/..", "%2e%2e/sale/en.json", ".hidden/en.json",
                  "meta/.x.json", "meta/..json"):
            self.refuse(p)

    def test_unknown_area_refuses_even_with_a_reserved_name(self):
        self.refuse("newarea/en.json")
        allowed, _ = s.auto_merge_allowed(["tools/en.json"], {"tools"})
        self.assertFalse(allowed)

    def test_an_empty_diff_is_not_allowed(self):
        allowed, bad = s.auto_merge_allowed([], AREAS)
        self.assertFalse(allowed)
        self.assertEqual(bad, [])


class Cli(unittest.TestCase):
    def run_check(self, data, areas=None):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "areas.json")
            with open(f, "w") as fh:
                json.dump({a: {} for a in (AREAS if areas is None else areas)}, fh)
            return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_english.py"),
                                   "--check-paths", "--areas-file", f], input=data, capture_output=True)

    def test_missing_areas_file_refuses(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_english.py"), "--check-paths"],
                           input=b"sale/en.json\0", capture_output=True)
        self.assertEqual(r.returncode, 1)

    def test_nul_separated_ok(self):
        r = self.run_check(b"sale/en.json\0meta/source.json\0")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_refusal_exits_nonzero_and_names_the_path(self):
        r = self.run_check(b"sale/en.json\0tools/sync_english.py\0")
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"tools/sync_english.py", r.stderr)

    def test_newline_in_a_name_is_not_a_separator(self):
        # NUL framing: a file literally named "sale/en.json\nx" stays one path.
        r = self.run_check(b"sale/en.json\nx\0")
        self.assertEqual(r.returncode, 1)

    def test_newline_is_not_a_separator_between_two_good_names(self):
        # Two allowed names joined by a newline and ended by NUL are ONE path
        # (not two allowed ones): newline framing would wrongly pass it.
        r = self.run_check(b"sale/en.json\nmeta/source.json\0")
        self.assertEqual(r.returncode, 1)

    def test_empty_input_refuses(self):
        self.assertEqual(self.run_check(b"").returncode, 1)

    def test_areas_file_must_be_an_object(self):
        # a list of areas would make `in` checks pass on substrings/odd shapes
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "areas.json")
            for body in ('["sale"]', '"sale"', "null", "7"):
                with open(f, "w") as fh:
                    fh.write(body)
                r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_english.py"),
                                    "--check-paths", "--areas-file", f], input=b"sale/en.json\0",
                                   capture_output=True)
                self.assertEqual(r.returncode, 1, body)


def raw(*entries):
    """git diff --raw -z --no-renames framing: ':<src> <dst> <sha> <sha> <S>' NUL path NUL."""
    out = b""
    for status, src, dst, path in entries:
        out += f":{src} {dst} {'a' * 40} {'b' * 40} {status}".encode() + b"\0" + path.encode() + b"\0"
    return out


class RawCli(Cli):
    """--check-paths --raw: the same gate over `git diff --raw -z`, which also
    sees the file MODE, so a symlink/typechange/exec bit cannot ride a
    sync-owned name (review MEDIUM-1 / LOW-1)."""

    def run_raw(self, data, areas=None):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "areas.json")
            with open(f, "w") as fh:
                json.dump({a: {} for a in (AREAS if areas is None else areas)}, fh)
            return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_english.py"),
                                   "--check-paths", "--raw", "--areas-file", f], input=data, capture_output=True)

    def test_regular_file_add_modify_delete_pass(self):
        r = self.run_raw(raw(("M", "100644", "100644", "sale/en.json"),
                             ("A", "000000", "100644", "meta/new.json"),
                             ("D", "100644", "000000", "meta/old.json")))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_typechange_symlink_exec_and_submodule_refuse(self):
        for entry in (("T", "100644", "120000", "sale/en.json"),
                      ("M", "100644", "120000", "sale/en.json"),
                      ("A", "000000", "120000", "meta/x.json"),
                      ("M", "100644", "100755", "sale/en.json"),
                      ("A", "000000", "100755", "meta/x.json"),
                      ("A", "000000", "160000", "meta/x.json"),
                      ("M", "120000", "100644", "sale/en.json"),
                      ("D", "120000", "000000", "sale/en.json")):
            r = self.run_raw(raw(entry))
            self.assertEqual(r.returncode, 1, entry)

    def test_rename_copy_unmerged_and_unknown_status_refuse(self):
        for st in ("R", "C", "U", "X", "T"):
            self.assertEqual(self.run_raw(raw((st, "100644", "100644", "sale/en.json"))).returncode, 1, st)

    def test_a_good_mode_does_not_excuse_a_bad_path(self):
        r = self.run_raw(raw(("M", "100644", "100644", "tools/sync_english.py")))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"tools/sync_english.py", r.stderr)

    def test_malformed_and_empty_input_refuse(self):
        for data in (b"", b"sale/en.json\0", b":100644 100644 a b M\0", b":100644 100644 a b M\0sale/en.json",
                     b"garbage\0sale/en.json\0", raw(("M", "100644", "100644", "sale/en.json")) + b"x\0"):
            self.assertEqual(self.run_raw(data).returncode, 1, data)


if __name__ == "__main__":
    unittest.main()
