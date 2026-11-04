"""Deterministic pre-checks of the AI reviewer runner (ut-docs#5420): one
pass and one fail test per check, the needs-human rule, terminology hints,
and the real ADR-0040 / ADR-0106 guards (skipped without a universal-till
checkout, unless UTT_REQUIRE_TILL=1)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import review_checks as rc  # noqa: E402
from bundle_fixtures import make_till, till_source  # noqa: E402

REQUIRE = os.environ.get("UTT_REQUIRE_TILL") == "1"


def unit(source, target, key="pos.total", flags=""):
    src = source if isinstance(source, list) else [source]
    tgt = target if isinstance(target, list) else [target]
    return {"key": key, "source": src, "target": tgt, "flags": flags}


def checks(source, target, lang="de", **kw):
    return rc.check_unit(unit(source, target, key=kw.pop("key", "pos.total"), flags=kw.pop("flags", "")), lang, **kw)


class Base(unittest.TestCase):
    def assertPass(self, result, code):
        self.assertEqual(result[code], "pass", result)

    def assertFail(self, result, code):
        self.assertNotEqual(result[code], "pass", result)
        self.assertIn(code, rc.failed(result))


class Placeholders(Base):
    def test_pass(self):
        self.assertPass(checks("Hello {name}, %d items", "Hallo {name}, %d Artikel"), "placeholders")

    def test_fail_missing_placeholder(self):
        self.assertFail(checks("Hello {name}", "Hallo"), "placeholders")

    def test_fail_dropped_tag(self):
        self.assertFail(checks('See <a href="/help">help</a>', "Siehe Hilfe"), "dropped")


class IcuForms(Base):
    SRC = "{count, plural, one {# item} other {# items}}"

    def test_pass_german(self):
        self.assertPass(checks(self.SRC, "{count, plural, one {# Artikel} other {# Artikel}}"), "icu_forms")

    def test_pass_arabic_six_categories(self):
        tgt = ("{count, plural, zero {لا عناصر} one {عنصر واحد} two {عنصران} few {# عناصر} "
               "many {# عنصرًا} other {# عنصر}}")
        self.assertPass(checks(self.SRC, tgt, "ar"), "icu_forms")

    def test_fail_arabic_missing_categories(self):
        r = checks(self.SRC, "{count, plural, one {عنصر واحد} other {# عنصر}}", "ar")
        self.assertFail(r, "icu_forms")
        self.assertIn("zero", r["icu_forms"])

    def test_fail_renamed_argument(self):
        self.assertFail(checks(self.SRC, "{n, plural, one {# Artikel} other {# Artikel}}"), "icu_forms")

    def test_fail_extra_category(self):
        self.assertFail(checks(self.SRC, "{count, plural, one {# Artikel} few {# A} other {# Artikel}}"), "icu_forms")

    def test_fail_empty_branch(self):
        self.assertFail(checks(self.SRC, "{count, plural, one {} other {# Artikel}}"), "icu_forms")

    def test_fail_select_keys_changed(self):
        src = "{g, select, male {He} female {She} other {They}}"
        self.assertFail(checks(src, "{g, select, male {Er} other {Sie}}"), "icu_forms")

    def test_fail_unbalanced(self):
        self.assertFail(checks(self.SRC, "{count, plural, one {# Artikel} other {# Artikel}"), "icu_forms")

    def test_weblate_plural_unit_pass_and_fail(self):
        src = ["%d item", "%d items"]
        self.assertPass(checks(src, ["%d Artikel", "%d Artikel"], plural_number=2), "plural_forms")
        r = checks(src, ["%d عنصر", "%d عنصر"], "ar", plural_number=6)
        self.assertFail(r, "plural_forms")
        self.assertFail(checks(src, ["%d Artikel", ""], plural_number=2), "plural_forms")


class Script(Base):
    def test_pass(self):
        self.assertPass(checks("Total", "合计", "zh_Hans"), "script")

    def test_fail_latin_in_chinese_slot_untranslated(self):
        self.assertFail(checks("Open drawer", "Открыть ящик", "zh_Hans"), "script")


class Rtl(Base):
    def test_pass_brand_and_placeholder_allowed(self):
        self.assertPass(checks("Pair over Bluetooth with %s", "الاقتران عبر Bluetooth مع %s", "ar"), "rtl")

    def test_fail_ltr_mark(self):
        self.assertFail(checks("Total", "‎المجموع", "ar"), "rtl")

    def test_fail_stray_latin_words(self):
        self.assertFail(checks("Open the drawer", "افتح the drawer", "fa"), "rtl")

    def test_not_applied_to_ltr_languages(self):
        self.assertPass(checks("Total", "Summe", "de"), "rtl")


class Length(Base):
    def test_pass(self):
        self.assertPass(checks("Cancel", "Abbrechen"), "length_ratio")

    def test_fail_far_longer(self):
        self.assertFail(checks("Save the current basket now", "x" * 90), "length_ratio")

    def test_fail_max_length_flag(self):
        self.assertFail(checks("Pay", "Bezahlen", flags="max-length:6"), "max_length")


class Brand(Base):
    def test_pass(self):
        self.assertPass(checks("Welcome to Universal Till", "Willkommen bei Universal Till"), "brand")

    def test_fail_translated_brand(self):
        r = checks("Welcome to Universal Till", "Willkommen bei Universelle Kasse")
        self.assertFail(r, "brand")
        self.assertIn("Universal Till", r["brand"])

    def test_fail_brand_in_cjk(self):
        self.assertFail(checks("Scan the QR code", "扫描二维码", "zh_Hans"), "brand")


class Untranslated(Base):
    def test_pass_brand_only(self):
        self.assertPass(checks("Bluetooth", "Bluetooth"), "untranslated")

    def test_pass_keep_name(self):
        self.assertPass(checks("EAN-13", "EAN-13"), "untranslated")

    def test_pass_glossary_same_as_source(self):
        g = {"same_as_source": {"fr": ["Total"]}}
        self.assertPass(checks("Total", "Total", "fr", glossary=g), "untranslated")

    def test_fail_copied_english(self):
        self.assertFail(checks("Open the drawer", "Open the drawer"), "untranslated")


class Glossary(Base):
    G = {"terms": {"de": [{"source": "receipt", "target": ["Beleg", "Bon"], "forbid": ["Quittung"]}]}}

    def test_pass(self):
        self.assertPass(checks("Print receipt", "Beleg drucken", glossary=self.G), "glossary")

    def test_fail_missing_term(self):
        self.assertFail(checks("Print receipt", "Rechnung drucken", glossary=self.G), "glossary")

    def test_fail_forbidden_term(self):
        self.assertFail(checks("Print receipt", "Bon/Quittung drucken", glossary=self.G), "glossary")

    def test_no_entry_for_language_passes(self):
        self.assertPass(checks("Print receipt", "Imprimer le ticket", "fr", glossary=self.G), "glossary")

    def test_shipped_glossary_loads(self):
        g = rc.load_glossary()
        self.assertEqual(g.get("schema"), 1)
        self.assertIsInstance(g.get("terms"), dict)


class Terminology(Base):
    def test_hint_when_the_same_source_is_translated_differently(self):
        idx = rc.term_index([unit("Refund", "Erstattung", key="a"), unit("Refund", "Erstattung", key="b")])
        self.assertEqual(rc.terminology_hints(unit("Refund", "Rückgabe", key="c"), idx),
                         ["'Refund' is translated as 'Erstattung' in 2 approved string(s)"])

    def test_no_hint_when_consistent(self):
        idx = rc.term_index([unit("Refund", "Erstattung", key="a")])
        self.assertEqual(rc.terminology_hints(unit("Refund", "Erstattung", key="c"), idx), [])


class NeedsHuman(Base):
    def test_plain_key_is_not_human(self):
        self.assertIsNone(rc.needs_human("pos.open_drawer", "sale", {}, set(), "Open the drawer"))

    def test_legal_meta_label(self):
        self.assertIn("legal", rc.needs_human("pos.x", "sale", {"labels": ["legal"]}, set(), "Hi"))

    def test_weblate_label(self):
        self.assertIn("legal", rc.needs_human("pos.x", "sale", {}, {"legal"}, "Hi"))

    def test_receipts_and_legal_component(self):
        self.assertIn("receipts-and-legal", rc.needs_human("x.y", "receipts-and-legal", {}, set(), "Hi"))

    def test_tax_fiscal_money_keys_and_words(self):
        for key, src in (("admin.group.fiscal", "Group"), ("pos.tax_rate", "Rate"), ("pos.x", "VAT included"),
                         ("pos.y", "Enter the amount"), ("pos.z", "Print invoice"), ("pos.w", "Currency")):
            self.assertIsNotNone(rc.needs_human(key, "sale", {}, set(), src), key)


class Family(unittest.TestCase):
    def test_family_normalisation(self):
        for m in ("claude", "anthropic/claude-opus-5-5", "claude-opus-5.5", "Anthropic"):
            self.assertEqual(rc.family(m), "claude", m)
        self.assertEqual(rc.family("codex"), "openai")
        self.assertEqual(rc.family("openai/gpt-6-luna"), "openai")
        self.assertEqual(rc.family("local-ollama"), "local-ollama")
        self.assertNotEqual(rc.family("claude"), rc.family("codex"))


class Scans(unittest.TestCase):
    """The real universal-till guards over a throwaway till repo."""

    def setUp(self):
        if not till_source():
            if REQUIRE:
                raise AssertionError("UTT_REQUIRE_TILL=1 but no universal-till checkout")
            raise unittest.SkipTest("needs a universal-till checkout (UTT_TILL_SOURCE)")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.till, self.sha = make_till(self.tmp.name)

    def test_clean_strings_pass(self):
        self.assertEqual(rc.run_scans(self.till, self.sha, [(1, "Summe"), (2, "Beleg drucken")]), {})

    def test_adr_0040_compliance_wording_hit_maps_to_its_unit(self):
        hits = rc.run_scans(self.till, self.sha, [(1, "Summe"), (2, "Diese Kasse ist finanzamtskonform"), (3, "ok")])
        self.assertEqual(list(hits), [2])
        self.assertIn("compliance", hits[2])

    def test_adr_0106_competitor_naming_hit_maps_to_its_unit(self):
        hits = rc.run_scans(self.till, self.sha, [(7, "Ein Layout wie Shopify"), (8, "Summe")])
        self.assertEqual(list(hits), [7])
        self.assertIn("competitor", hits[7])

    def test_a_term_split_by_a_newline_is_still_caught(self):
        hits = rc.run_scans(self.till, self.sha, [(4, "fully\ncompliant")])
        self.assertEqual(list(hits), [4])


if __name__ == "__main__":
    unittest.main()
