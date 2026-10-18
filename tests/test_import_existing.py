"""Import of existing translations (ut-docs#4371, #4372): sources are real
git repo fixtures read at a ref, the area split follows this repo's
<area>/en.json, text is copied byte for byte, and Weblate's files are never
overwritten without --force."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import i18n_map  # noqa: E402
import import_existing as imp  # noqa: E402

EN = {
    "sale": {"pos.pay": "Pay %s", "pos.total": "Total {{.Amount}}", "pos.sku": "SKU"},
    "common": {"common.ok": "OK", "common.hello": "Hello {name}"},
}
BUNDLES = {"locales": {
    "en": {"weblate": "en", "source": True},
    "de": {"weblate": "de"},
    "tr": {"weblate": "tr"},
}}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def source_repo(path, files):
    """A git repo with `files` committed on main and pushed to a bare
    remote, so origin/main exists — the ref the import reads."""
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        write(os.path.join(path, rel), text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "fixture")
    bare = path + ".git"
    git(os.path.dirname(path), "init", "-q", "--bare", bare)
    git(path, "remote", "add", "origin", bare)
    git(path, "push", "-q", "origin", "main")
    git(path, "fetch", "-q", "origin")


class Fixture(unittest.TestCase):
    DE = {
        "pos.pay": "Bezahlen %s",
        "pos.total": "Summe {{.Amount}}",
        "pos.sku": "SKU",               # same as English, on the pack's list
        "common.ok": "OK",              # same as English, NOT on the list
        "common.hello": "",             # empty
        "old.removed": "Weg",           # not in any en.json: stale
    }
    TR = {
        "pos": {"pay": "Öde %s", "total": "Toplam {{.Amount}}"},  # nested
        "pos.sku": "SKU",               # same as English: core has no list
        "common.ok": "Tamam",
        # common.hello missing
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name
        self.repo = os.path.join(self.ws, "ut-translations")
        for area, keys in EN.items():
            write(os.path.join(self.repo, area, "en.json"), i18n_map.dumps(keys))
        write(os.path.join(self.repo, "meta", "areas.json"),
              i18n_map.dumps({a: {"keys": len(k)} for a, k in EN.items()}, sort=False))
        write(os.path.join(self.repo, "config", "bundles.json"), json.dumps(BUNDLES))
        source_repo(os.path.join(self.ws, "ut-plugin-language-de"), {
            "locales/de.json": json.dumps(self.DE, ensure_ascii=False),
            "i18n-baseline/de.same-as-en.txt": "# header\n#\npos.sku\n",
        })
        source_repo(os.path.join(self.ws, "universal-till"), {
            "web/locales/tr.json": json.dumps(self.TR, ensure_ascii=False),
        })

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = imp.main(["--repo", self.repo, "--workspace", self.ws, *args])
        return code, out.getvalue(), err.getvalue()

    def read(self, rel):
        with open(os.path.join(self.repo, rel), encoding="utf-8") as f:
            return f.read()


class Import(Fixture):
    def test_area_split_and_rules(self):
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.read("sale/de.json")),
                         {"pos.pay": "Bezahlen %s", "pos.total": "Summe {{.Amount}}", "pos.sku": "SKU"})
        # common.ok equals English and isn't listed; common.hello is empty.
        self.assertEqual(json.loads(self.read("common/de.json")), {})
        # Nested source flattened; identical-to-English skipped without a list.
        self.assertEqual(json.loads(self.read("sale/tr.json")),
                         {"pos.pay": "Öde %s", "pos.total": "Toplam {{.Amount}}"})
        # A locale missing a key just omits it.
        self.assertEqual(json.loads(self.read("common/tr.json")), {"common.ok": "Tamam"})
        self.assertIn("stale: old.removed", out)

    def test_same_format_as_en_json(self):
        self.run_main()
        text = self.read("sale/de.json")
        self.assertEqual(text, i18n_map.dumps(json.loads(text)))
        self.assertTrue(text.endswith("}\n"))
        self.assertIn("Öde", self.read("sale/tr.json"))  # not \u-escaped

    def test_keys_absent_from_en_dropped(self):
        self.run_main()
        for area in EN:
            for code in ("de", "tr"):
                self.assertLessEqual(set(json.loads(self.read(f"{area}/{code}.json"))), set(EN[area]))

    def test_placeholders_byte_identical(self):
        self.run_main()
        de = json.loads(self.read("sale/de.json"))
        self.assertEqual(de["pos.total"], self.DE["pos.total"])
        self.assertEqual(de["pos.pay"], self.DE["pos.pay"])

    def test_placeholder_mismatch_reported_not_fixed(self):
        files, stats = imp.split({"sale": {"a": "Pay %s to {name}"}}, {"a": "Zahle {name}"}, None)
        self.assertEqual(files["sale"], {"a": "Zahle {name}"})
        self.assertEqual(stats["placeholders"], ["a"])

    def test_rerun_is_idempotent(self):
        self.run_main()
        before = {p: self.read(p) for p in ("sale/de.json", "common/tr.json")}
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("wrote 0 file(s), 4 unchanged", out)
        self.assertEqual(before, {p: self.read(p) for p in before})

    def test_refuses_weblate_edits_without_force(self):
        self.run_main()
        edited = i18n_map.dumps({"pos.pay": "Jetzt bezahlen %s"})
        write(os.path.join(self.repo, "sale/de.json"), edited)
        tr_before = self.read("sale/tr.json")
        os.remove(os.path.join(self.repo, "sale/tr.json"))
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("sale/de.json", err)
        self.assertEqual(self.read("sale/de.json"), edited)
        # Nothing at all is written when one file is refused.
        self.assertFalse(os.path.exists(os.path.join(self.repo, "sale/tr.json")))
        code, _, err = self.run_main("--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.read("sale/de.json"))["pos.pay"], "Bezahlen %s")
        self.assertEqual(self.read("sale/tr.json"), tr_before)

    def test_dry_run_writes_nothing(self):
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would write 4 file(s)", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "sale/de.json")))

    def test_reads_the_ref_not_the_working_tree(self):
        write(os.path.join(self.ws, "ut-plugin-language-de", "locales/de.json"),
              json.dumps({"pos.pay": "UNCOMMITTED %s"}))
        self.run_main()
        self.assertEqual(json.loads(self.read("sale/de.json"))["pos.pay"], "Bezahlen %s")

    def test_locale_filter_and_unknown_locale(self):
        code, _, _ = self.run_main("--locale", "tr")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "sale/de.json")))
        code, _, err = self.run_main("--locale", "en")
        self.assertEqual(code, 1)
        self.assertIn("en", err)

    def test_report_json(self):
        path = os.path.join(self.ws, "report.json")
        self.run_main("--report", path)
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        self.assertEqual(r["de"]["imported"], 3)
        self.assertEqual(r["de"]["en"], 5)
        self.assertEqual(r["de"]["same_as_en"], ["common.ok"])
        self.assertEqual(r["de"]["empty"], ["common.hello"])
        self.assertEqual(r["tr"]["areas"]["common"], {"imported": 1, "en": 2})


class Helpers(unittest.TestCase):
    def test_parse_key_list(self):
        self.assertEqual(imp.parse_key_list("# c\n\n a.b \n#x\nc\n"), {"a.b", "c"})
        self.assertIsNone(imp.parse_key_list(None))

    def test_flatten(self):
        self.assertEqual(imp.flatten({"a": {"b": "x", "c": {"d": "y"}}, "e": "z"}),
                         {"a.b": "x", "a.c.d": "y", "e": "z"})


if __name__ == "__main__":
    unittest.main()
