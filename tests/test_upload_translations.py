"""tools/upload_translations.py: validates build-time translations and writes
them to Weblate only as 'needs review' (state 20), never approved, never legal
(ut-docs#5421)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import upload_translations as up  # noqa: E402
import bulk_locale  # noqa: E402


class FakeClient:
    def __init__(self, units):
        self.units = units
        self.patches = []

    def list(self, path):
        return self.units

    def request(self, method, path, body=None):
        self.patches.append((method, path, body))
        return 200, {}


def unit(uid, key, source, state=0, target=""):
    return {"id": uid, "context": key, "source": [source], "target": [target], "state": state, "labels": []}


META = {("sale", "a"): {"flags": 'placeholders:"%s", max-length:12', "explanation": "", "labels": []},
        ("sale", "legal.k"): {"flags": "", "explanation": "", "labels": ["legal"]}}


class Problems(unittest.TestCase):
    def p(self, en, it, flags=""):
        return up.problems(en, it, {"flags": flags})

    def test_good_translation_passes(self):
        self.assertIsNone(self.p("Total: %s", "Totale: %s"))

    def test_dropped_placeholder_is_rejected(self):
        self.assertEqual(self.p("Total: %s", "Totale")[0], "placeholders")

    def test_over_max_length_is_rejected(self):
        self.assertEqual(self.p("Total", "Totale generale", "max-length:8")[0], "max_length")

    def test_identical_to_english_is_rejected(self):
        self.assertEqual(self.p("Close the drawer", "Close the drawer")[0], "identical")

    def test_known_international_word_may_stay(self):
        self.assertIsNone(self.p("Bluetooth", "Bluetooth"))
        self.assertIsNone(self.p("EAN-13", "EAN-13"))

    def test_total_and_vat_must_be_translated(self):
        self.assertEqual(self.p("Total", "Total")[0], "identical")
        self.assertEqual(self.p("VAT", "VAT")[0], "identical")

    def test_edge_space_must_match_english(self):
        self.assertIsNone(self.p("Edit ", "Modifica "))
        self.assertEqual(self.p("Edit", "Modifica ")[0], "whitespace")

    def test_reviewed_same_list_allows_identical(self):
        probs = up.validate_map("sale", {"a": "Close"}, {"a": "Close"}, {}, same={"sale:a"})
        self.assertEqual(probs, [])
        self.assertEqual(len(up.validate_map("sale", {"a": "Close"}, {"a": "Close"}, {})), 1)

    def test_empty_is_rejected(self):
        self.assertEqual(self.p("Total", "  ")[0], "empty")


class Writing(unittest.TestCase):
    def run_up(self, units, tr, **kw):
        c = FakeClient(units)
        stats = up.upload_component(c, "sale", "it", tr, META, **kw)
        return c, stats

    def test_writes_state_20_only(self):
        c, s = self.run_up([unit(1, "a", "Total: %s")], {"a": "Totale: %s"})
        self.assertEqual(c.patches[0][2], {"target": ["Totale: %s"], "state": 20})
        self.assertEqual(s["written"], 1)

    def test_never_sends_approved_state(self):
        c, _ = self.run_up([unit(1, "a", "Total: %s")], {"a": "Totale: %s"})
        self.assertTrue(all(b.get("state") == 20 for _, _, b in c.patches if b and "state" in b))
        with self.assertRaises(ValueError):
            up.patch_body("x", 30)

    def test_approved_unit_is_never_touched(self):
        c, s = self.run_up([unit(1, "a", "Total: %s", 30, "Tot %s")], {"a": "Totale: %s"})
        self.assertEqual(c.patches, [])
        self.assertEqual(s["skipped_approved"], 1)

    def test_legal_unit_is_never_written(self):
        c, s = self.run_up([unit(2, "legal.k", "VAT")], {"legal.k": "IVA"})
        self.assertEqual(c.patches, [])
        self.assertEqual(s["skipped_legal"], 1)

    def test_rerun_changes_nothing(self):
        c, s = self.run_up([unit(1, "a", "Total: %s", 20, "Totale: %s")], {"a": "Totale: %s"})
        self.assertEqual(c.patches, [])
        self.assertEqual(s["unchanged"], 1)

    def test_existing_different_text_kept_without_overwrite(self):
        c, s = self.run_up([unit(1, "a", "Total: %s", 20, "Somma: %s")], {"a": "Totale: %s"})
        self.assertEqual(c.patches, [])
        self.assertEqual(s["skipped_differs"], 1)

    def test_invalid_text_is_not_written(self):
        c, s = self.run_up([unit(1, "a", "Total: %s")], {"a": "Totale"})
        self.assertEqual(c.patches, [])
        self.assertEqual(len(s["rejected"]), 1)

    def test_dry_run_writes_nothing(self):
        c, s = self.run_up([unit(1, "a", "Total: %s")], {"a": "Totale: %s"}, dry_run=True)
        self.assertEqual(c.patches, [])
        self.assertEqual(s["written"], 1)


class Missing404(FakeClient):
    def request(self, method, path, body=None):
        self.patches.append((method, path, body))
        return 404, None


class NotFound(unittest.TestCase):
    def test_404_patch_is_a_failure_not_a_write(self):
        c = Missing404([unit(1, "a", "Total: %s")])
        s = up.upload_component(c, "sale", "it", {"a": "Totale: %s"}, META)
        self.assertEqual(s["written"], 0)
        self.assertEqual(len(s["failed"]), 1)

    def test_404_comment_is_not_counted_and_stops(self):
        class C(FakeClient):
            def request(self, method, path, body=None):
                self.patches.append((method, path, body))
                return (404, None) if path.endswith("/comments/") else (200, {})
        c = C([unit(1, "a", "Total: %s"), unit(2, "b", "Total: %s")])
        s = up.upload_component(c, "sale", "it", {"a": "Totale: %s", "b": "Totale: %s"}, META, note="n")
        self.assertEqual((s["written"], s["commented"]), (2, 0))
        self.assertEqual(sum(1 for m, pa, b in c.patches if pa.endswith("/comments/")), 1)


class Files(unittest.TestCase):
    def test_validate_file_reports_unknown_and_missing_keys(self):
        en = {"a": "Total: %s", "b": "Hello"}
        probs = up.validate_map("sale", {"a": "Totale: %s", "zzz": "x"}, en, META, require_all={"a", "b"})
        kinds = sorted(p[1] for p in probs)
        self.assertEqual(kinds, ["missing", "unknown_key"])

    def test_italian_notes_are_informal(self):
        self.assertIn("'tu'", bulk_locale.LANGUAGE_NOTES["it"])
        self.assertIn("cassa", bulk_locale.LANGUAGE_NOTES["it"])


if __name__ == "__main__":
    unittest.main()
