"""tools/fill_from_plugin.py: a plugin translation is saved only when it passes
the bundle's own string checks (ut-docs#4371)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import fill_from_plugin as f  # noqa: E402


def unit(source="Total: %s", context="sale.total"):
    return {"id": 1, "state": 0, "target": [""], "context": context, "source": [source], "flags": ""}


class Choose(unittest.TestCase):
    def test_good_plugin_text_is_chosen(self):
        self.assertEqual(f.choose(unit(), {"sale.total": "Gesamt: %s"}, "Latn"), ("Gesamt: %s", None))

    def test_key_the_plugin_lacks_is_absent(self):
        self.assertEqual(f.choose(unit(), {}, "Latn"), (None, "absent"))
        self.assertEqual(f.choose(unit(), {"sale.total": "  "}, "Latn"), (None, "absent"))

    def test_broken_placeholder_is_rejected(self):
        text, why = f.choose(unit(), {"sale.total": "Gesamt: %d"}, "Latn")
        self.assertIsNone(text)
        self.assertIn("placeholders", why)

    def test_wrong_script_is_rejected(self):
        text, why = f.choose(unit("Total"), {"sale.total": "Gesamt"}, "Arab")
        self.assertIsNone(text)
        self.assertIn("script", why)


class Run(unittest.TestCase):
    def test_saves_only_empty_strings_the_plugin_translates(self):
        saved = []

        class Client:
            def __init__(self, units):
                self.units = units

            def list(self, path):
                return [{"slug": "sale"}] if "components" in path else self.units

            def request(self, method, path, body=None):
                saved.append(body)
                return 200, {}

        units = [unit(), unit("Other", "x.other"), dict(unit("Done", "x.done"), state=30, target=["Fertig"])]
        args = f.parse(["--locale", "de", "--plugin", "."])
        stats, rejected = f.run(args, Client(units), Client(units), {"sale.total": "Gesamt: %s"})
        self.assertEqual(saved, [{"target": ["Gesamt: %s"], "state": 20}])
        self.assertEqual((stats["saved"], stats["not_in_plugin"], rejected), (1, 1, []))


class LegalAndApprove(unittest.TestCase):
    def test_legal_key_is_not_filled_from_the_plugin(self):
        saved = []

        class Client:
            def list(self, path):
                return [{"slug": "sale"}] if "components" in path else [unit()]

            def request(self, method, path, body=None):
                saved.append(body)
                return 200, {}

        f.b.LEGAL_KEYS.add("sale.total")
        try:
            stats, _ = f.run(f.parse(["--locale", "de", "--plugin", "."]), Client(), Client(), {"sale.total": "Gesamt: %s"})
        finally:
            f.b.LEGAL_KEYS.discard("sale.total")
        self.assertEqual((saved, stats["saved"], stats["skipped_legal"]), ([], 0, 1))

    def test_plugin_file_name_for_a_script_locale(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "locales"))
            with open(os.path.join(d, "locales", "zh-hans.json"), "w") as fh:
                fh.write('{"a": "b"}')
            self.assertEqual(f.plugin_texts(d, "zh_Hans"), {"a": "b"})


if __name__ == "__main__":
    unittest.main()
