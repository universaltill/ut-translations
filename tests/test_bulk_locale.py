"""tools/bulk_locale.py: what gets translated, what gets approved, and that a
model answer that breaks a placeholder is never saved (ut-docs#4371)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import bulk_locale as b  # noqa: E402


def unit(state, target, source="Total: %s", labels=(), plural=False):
    return {"id": 1, "state": state, "target": target, "context": "sale.total",
            "source": [source, source] if plural else [source], "labels": [{"name": n} for n in labels]}


class Selection(unittest.TestCase):
    def test_empty_string_needs_translation(self):
        self.assertTrue(b.needs_translation(unit(0, [""])))

    def test_translated_string_is_left_alone(self):
        self.assertFalse(b.needs_translation(unit(20, ["Gesamt: %s"])))

    def test_plural_is_left_for_a_human(self):
        self.assertFalse(b.needs_translation(unit(0, [""], plural=True)))

    def test_waiting_string_is_approved(self):
        self.assertTrue(b.needs_approval(unit(20, ["Gesamt: %s"])))

    def test_imported_approved_string_only_with_the_flag(self):
        u = unit(30, ["Gesamt: %s"])
        self.assertFalse(b.needs_approval(u))
        self.assertTrue(b.needs_approval(u, include_approved=True))

    def test_legal_key_is_never_approved_here(self):
        self.assertFalse(b.needs_approval(unit(20, ["x"], labels=["legal"]), include_approved=True))

    def test_empty_target_is_never_approved(self):
        self.assertFalse(b.needs_approval(unit(20, [""])))


class Translate(unittest.TestCase):
    def test_good_answer_is_kept(self):
        text, why = b.translate_one(lambda m: "Gesamt: %s", "de", unit(0, [""]), "Latn")
        self.assertEqual((text, why), ("Gesamt: %s", None))

    def test_quotes_around_the_answer_are_stripped(self):
        text, _ = b.translate_one(lambda m: '"Gesamt: %s"', "de", unit(0, [""]), "Latn")
        self.assertEqual(text, "Gesamt: %s")

    def test_broken_placeholder_is_rejected_after_one_retry(self):
        calls = []

        def chat(m):
            calls.append(m)
            return "Gesamt: %d"

        text, why = b.translate_one(chat, "de", unit(0, [""]), "Latn")
        self.assertIsNone(text)
        self.assertIn("placeholders", why)
        self.assertEqual(len(calls), 2)
        self.assertIn("rejected", calls[1][-1]["content"])

    def test_english_echo_is_rejected_for_a_non_latin_language(self):
        text, why = b.translate_one(lambda m: "Total", "fa", unit(0, [""], source="Total"), "Arab")
        self.assertIsNone(text)
        self.assertIn("untranslated", why)

    def test_wrong_script_is_rejected(self):
        text, why = b.translate_one(lambda m: "Gesamt", "fa", unit(0, [""], source="Total"), "Arab")
        self.assertIsNone(text)
        self.assertIn("script", why)

    def test_english_echo_is_fine_for_a_latin_language(self):
        text, _ = b.translate_one(lambda m: "Bluetooth", "de", unit(0, [""], source="Bluetooth"), "Latn")
        self.assertEqual(text, "Bluetooth")

    def test_a_model_that_explains_instead_of_translating_is_rejected(self):
        for answer in ("Okay, the user wants me to translate 'Aftershave' into French.",
                       "<think>hmm</think>Après-rasage", "Après-rasage\nNote: common term",
                       "Here is the translation: Après-rasage"):
            text, why = b.translate_one(lambda m, a=answer: a, "fr", unit(0, [""], source="Aftershave"), "Latn")
            self.assertIsNone(text, answer)
            self.assertIn("chatter", why)

    def test_model_failure_is_reported_not_raised(self):
        def chat(m):
            raise OSError("down")

        text, why = b.translate_one(chat, "de", unit(0, [""]), "Latn")
        self.assertIsNone(text)
        self.assertIn("model error", why)


class KeepAsIs(unittest.TestCase):
    def test_codes_and_product_names_are_copied_not_translated(self):
        for src in ("SKU", "EAN-13", "QR", "Code 128", "Local Plus", "PIN"):
            self.assertTrue(b.keep_as_is(unit(0, [""], source=src)), src)

    def test_ordinary_text_is_translated(self):
        for src in ("Total", "Local shop", "Scan", "Sale not completed: %s", "TOTAL", "VAT", "TAX INVOICE",
                    "CLEARED BEFORE PAYMENT", "020 7946 0000"):
            self.assertFalse(b.keep_as_is(unit(0, [""], source=src)), src)

    def test_kept_string_is_saved_without_asking_the_model(self):
        saved = []

        class Fake:
            def request(self, method, path, body=None):
                saved.append(body)
                return 200, {}

        def chat(m):
            raise AssertionError("the model must not be asked")

        run = b.Run(b.parse(["translate", "--locale", "ar"]), Fake(), None, chat)
        run.translate_unit(unit(0, [""], source="SKU"))
        self.assertEqual(saved, [{"target": ["SKU"], "state": 20}])
        self.assertEqual(run.stats["kept"], 1)


class LegalByAHuman(unittest.TestCase):
    def run_legal(self, u):
        calls = []

        class Fake:
            def __init__(self, name):
                self.name = name

            def request(self, method, path, body=None):
                calls.append((self.name, body["state"], body["target"]))
                return 200, {}

        run = b.Run(b.parse(["approve-legal", "--locale", "de"]), Fake("translator"), Fake("human"), None)
        run.approve_legal_unit(u)
        return calls, run

    def test_every_legal_string_is_re_authored_by_the_translator_then_approved_by_the_human(self):
        for state in (20, 30):
            calls, run = self.run_legal(unit(state, ["Beleg"], labels=["legal"]))
            self.assertEqual([c[:2] for c in calls],
                             [("translator", 20), ("translator", 20), ("human", 30)])
            self.assertNotEqual(calls[0][2], ["Beleg"])
            self.assertEqual(calls[1][2], ["Beleg"])
            self.assertEqual(run.stats["legal_approved"], 1)

    def test_non_legal_and_empty_strings_are_left_alone(self):
        for u in (unit(30, ["x"]), unit(20, [""], labels=["legal"]), unit(20, ["x", "y"], labels=["legal"], plural=True)):
            calls, _ = self.run_legal(u)
            self.assertEqual(calls, [])


class LegalKeys(unittest.TestCase):
    def test_keys_marked_legal_in_the_meta_files_are_found(self):
        keys = b.load_legal_keys()
        self.assertIn("fiscal.banner.override_active", keys)
        self.assertGreater(len(keys), 100)

    def test_a_key_in_that_set_counts_as_legal_without_a_label(self):
        b.LEGAL_KEYS.add("sale.total")
        try:
            self.assertTrue(b.is_legal(unit(20, ["x"])))
            self.assertFalse(b.needs_approval(unit(20, ["x"])))
        finally:
            b.LEGAL_KEYS.discard("sale.total")


class Reauthor(unittest.TestCase):
    def test_translator_edits_and_restores_then_approver_approves(self):
        calls = []

        class Fake:
            def __init__(self, name):
                self.name = name

            def request(self, method, path, body=None):
                calls.append((self.name, body["state"], body["target"]))
                return 200, {}

        args = b.parse(["approve", "--locale", "de", "--include-approved"])
        run = b.Run(args, Fake("translator"), Fake("approver"), None)
        u = unit(30, ["Gesamt: %s"])
        self.assertTrue(run.reauthor(u))
        self.assertTrue(run.write(run.approver, u, {"target": u["target"], "state": 30}, "approve"))
        self.assertEqual([c[0] for c in calls], ["translator", "translator", "approver"])
        self.assertNotEqual(calls[0][2], u["target"])
        self.assertEqual(calls[1][2], u["target"])


if __name__ == "__main__":
    unittest.main()
