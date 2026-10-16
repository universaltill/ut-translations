"""Mapping: universal-till key metadata -> Weblate explanation/flags/labels,
area assignment and the union across supported till lines (ADR-0160 §2-§5)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import i18n_map as m  # noqa: E402

AREAS = {
    "default_area": "common",
    "legal_namespaces": ["receipt", "fiscal", "tax"],
    "areas": {
        "sale": {"description": "The sale screen.", "namespaces": ["pos", "basket"]},
        "receipts-and-legal": {"description": "Receipts.", "namespaces": ["receipt", "fiscal"]},
        "selforder": {"description": "Kiosk.", "namespaces": ["selforder"]},
        "common": {"description": "Shared.", "namespaces": ["common"]},
    },
}


class PlaceholderTokens(unittest.TestCase):
    def test_printf_verbs_in_order_unique(self):
        self.assertEqual(m.placeholder_tokens("%s of %d (%s)"), ["%s", "%d"])

    def test_percent_literal_is_not_a_placeholder(self):
        self.assertEqual(m.placeholder_tokens("100%% sure, %.2f"), ["%.2f"])

    def test_indexed_go_verbs(self):
        self.assertEqual(m.placeholder_tokens("%[2]s then %[1]s"), ["%[2]s", "%[1]s"])

    def test_template_and_numbered_and_named(self):
        self.assertEqual(
            m.placeholder_tokens("{{.Name}} {0} {item} — {location}"),
            ["{{.Name}}", "{0}", "{item}", "{location}"],
        )

    def test_icu_plural_is_detected_and_its_braces_are_not_placeholders(self):
        text = "{count, plural, one {# item} other {# items}}"
        self.assertTrue(m.is_icu(text, {}))
        self.assertEqual(m.placeholder_tokens(text, icu=True), [])

    def test_icu_key_named_arguments_are_left_to_the_icu_check(self):
        text = "{name} has {n, plural, one {# item} other {# items}}"
        self.assertEqual(m.placeholder_tokens(text, icu=True), [])
        self.assertEqual(m.placeholder_tokens(text), ["{name}"])

    def test_icu_from_metadata_plural_field(self):
        self.assertTrue(m.is_icu("{n} items", {"plural": "n"}))

    def test_simple_named_argument_alone_is_not_icu(self):
        self.assertFalse(m.is_icu("{item} — {location}", {}))

    def test_icu_key_keeps_printf_tokens(self):
        self.assertEqual(
            m.placeholder_tokens("%s: {n, plural, one {# x} other {# xs}}", icu=True), ["%s"]
        )


class Flags(unittest.TestCase):
    def test_format_flags_quotes_values(self):
        self.assertEqual(
            m.format_flags([("max-length", ["24"]), ("placeholders", ["%s", 'a"b'])]),
            'max-length:24, placeholders:"%s":"a\\"b"',
        )

    def test_flag_without_value(self):
        self.assertEqual(m.format_flags([("icu-message-format", [])]), "icu-message-format")


class UnitMetadata(unittest.TestCase):
    def test_full_entry(self):
        u = m.unit_metadata(
            "import.issue.x",
            "Barcode %s twice",
            {"description": "Row detail.", "review": "standard", "max_length": 40,
             "placeholders": ["the barcode"]},
            AREAS["legal_namespaces"],
        )
        self.assertEqual(u["explanation"], "Row detail.\n\nPlaceholders:\n- %s: the barcode")
        self.assertEqual(u["flags"], 'max-length:40, placeholders:"%s"')
        self.assertEqual(u["labels"], [])

    def test_legal_review_gets_legal_label(self):
        u = m.unit_metadata("pos.x", "Total", {"description": "d", "review": "legal"}, ["receipt"])
        self.assertEqual(u["labels"], ["legal"])

    def test_legal_namespace_without_metadata_is_legal_by_default(self):
        u = m.unit_metadata("receipt.footer", "Thanks", None, ["receipt"])
        self.assertEqual(u["labels"], ["legal"])
        self.assertEqual(u["explanation"], "")
        self.assertEqual(u["flags"], "")

    def test_reviewed_exception_in_legal_namespace_stays_standard(self):
        u = m.unit_metadata(
            "receipt.title", "Receipt",
            {"description": "d", "review": "standard", "review_exception": "UI heading"},
            ["receipt"],
        )
        self.assertEqual(u["labels"], [])

    def test_icu_key_gets_icu_flag_and_no_brace_placeholders(self):
        u = m.unit_metadata(
            "cart.items", "{count, plural, one {# item} other {# items}}",
            {"description": "d", "review": "standard", "plural": "count"}, [],
        )
        self.assertEqual(u["flags"], "icu-message-format")

    def test_named_placeholders_get_placeholder_flag(self):
        u = m.unit_metadata("inventory.dialog_context", "{item} — {location}",
                            {"description": "d", "review": "standard"}, [])
        self.assertEqual(u["flags"], 'placeholders:"{item}":"{location}"')

    def test_undescribed_key_with_verbs_still_gets_placeholder_flag(self):
        u = m.unit_metadata("pos.x", "%d left", None, [])
        self.assertEqual(u["flags"], 'placeholders:"%d"')

    def test_key_only_on_an_old_line_says_so(self):
        u = m.unit_metadata("pos.old", "Old", {"description": "d", "review": "standard"}, [],
                            lines=["0.33"])
        self.assertIn("Only used by till line 0.33", u["explanation"])


class Areas(unittest.TestCase):
    def test_namespace_map(self):
        idx = m.namespace_index(AREAS)
        self.assertEqual(m.area_for("basket.total", None, idx, "common"), "sale")

    def test_explicit_area_wins(self):
        idx = m.namespace_index(AREAS)
        self.assertEqual(m.area_for("basket.total", {"area": "selforder"}, idx, "common"), "selforder")

    def test_unknown_namespace_goes_to_default(self):
        idx = m.namespace_index(AREAS)
        self.assertEqual(m.area_for("zzz.x", None, idx, "common"), "common")

    def test_component_names(self):
        self.assertEqual(m.area_name("receipts-and-legal"), "Receipts and legal")
        self.assertEqual(m.area_name("selforder"), "Self-order")
        self.assertEqual(m.area_name("sale"), "Sale")


class AreaIds(unittest.TestCase):
    def test_good_ids(self):
        for a in ("sale", "receipts-and-legal", "selforder", "a1"):
            m.validate_area_id(a)

    def test_bad_ids_rejected(self):
        for a in ("", "../x", "a/b", "Sale", "a b", ".github", "-", "x_y", "sale\n",
                  "meta", "tools", "tests", "config", "scripts"):
            with self.assertRaises(ValueError, msg=repr(a)):
                m.validate_area_id(a)

    def test_render_rejects_a_bad_area(self):
        doc = {"default_area": "common", "areas": {"common": {}, "meta": {"namespaces": ["pos"]}}}
        with self.assertRaises(ValueError):
            m.render(m.build_union([m.Line("main", {"pos.a": "A"}, {})]), doc)

    def test_render_rejects_a_bad_default_area(self):
        doc = {"default_area": "../up", "areas": {"sale": {"namespaces": ["pos"]}}}
        with self.assertRaises(ValueError):
            m.render(m.build_union([m.Line("main", {"zzz.a": "A"}, {})]), doc)


class Union(unittest.TestCase):
    def test_union_keeps_keys_of_every_line_main_text_wins(self):
        main = m.Line("main", {"pos.a": "A new", "pos.b": "B"}, {"pos.a": {"description": "da", "review": "standard"}})
        old = m.Line("0.33", {"pos.a": "A old", "pos.gone": "Gone"}, {})
        u = m.build_union([main, old])
        self.assertEqual(sorted(u), ["pos.a", "pos.b", "pos.gone"])
        self.assertEqual(u["pos.a"].english, "A new")
        self.assertEqual(u["pos.a"].meta["description"], "da")
        self.assertEqual(u["pos.a"].lines, ["main", "0.33"])
        self.assertEqual(u["pos.gone"].english, "Gone")
        self.assertEqual(u["pos.gone"].lines, ["0.33"])

    def test_old_line_metadata_used_when_main_has_none(self):
        main = m.Line("main", {}, {})
        old = m.Line("0.34", {"pos.x": "X"}, {"pos.x": {"description": "dx", "review": "standard"}})
        u = m.build_union([main, old])
        self.assertEqual(u["pos.x"].meta["description"], "dx")

    def test_render_splits_by_area_and_sorts(self):
        main = m.Line("main", {"basket.z": "Z", "basket.a": "A", "receipt.f": "F", "zzz.q": "Q"}, {})
        files = m.render(m.build_union([main]), AREAS)
        self.assertEqual(files["sale/en.json"], '{\n  "basket.a": "A",\n  "basket.z": "Z"\n}\n')
        self.assertEqual(files["receipts-and-legal/en.json"], '{\n  "receipt.f": "F"\n}\n')
        self.assertIn('"zzz.q": "Q"', files["common/en.json"])
        # Every defined area gets a file, even an empty one, so its component has a template.
        self.assertEqual(files["selforder/en.json"], "{}\n")
        self.assertIn("meta/sale.json", files)
        self.assertIn("meta/areas.json", files)

    def test_render_meta_carries_flags_labels_lines(self):
        main = m.Line("main", {"receipt.f": "F %s"}, {"receipt.f": {"description": "d", "review": "legal", "placeholders": ["p"]}})
        files = m.render(m.build_union([main]), AREAS)
        import json
        meta = json.loads(files["meta/receipts-and-legal.json"])
        self.assertEqual(meta["receipt.f"]["labels"], ["legal"])
        self.assertEqual(meta["receipt.f"]["flags"], 'placeholders:"%s"')
        self.assertEqual(meta["receipt.f"]["lines"], ["main"])
        areas = json.loads(files["meta/areas.json"])
        self.assertEqual(areas["receipts-and-legal"], {"name": "Receipts and legal", "description": "Receipts.", "keys": 1})

    def test_non_ascii_written_as_is(self):
        main = m.Line("main", {"common.x": "Größe — ok"}, {})
        files = m.render(m.build_union([main]), AREAS)
        self.assertIn("Größe — ok", files["common/en.json"])


if __name__ == "__main__":
    unittest.main()
