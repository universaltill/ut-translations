#!/usr/bin/env python3
"""Fill the empty strings of one Weblate language from the old language
plugin's locale file (ut-docs#4371).

The plugins (`ut-plugin-language-<code>/locales/<code>.json`) already hold
translations for keys Weblate's import predates. Each empty single-form
string (not a `review: legal` one) whose key the plugin translates is saved as "translated" by the
translator bot, after the same placeholder / tag / script checks the bundle
build runs (tools/bundle_checks.check_string). A text that fails is listed in
the report, not saved. Approval is a separate step (tools/bulk_locale.py
approve), so a different account approves.

  python3 tools/fill_from_plugin.py --locale de --plugin ../ut-plugin-language-de
  python3 tools/fill_from_plugin.py --locale es --plugin ../ut-plugin-language-es --approve

--dry-run reads and reports; it writes nothing to Weblate.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bulk_locale as b  # noqa: E402
import bundle_checks  # noqa: E402
from provision_weblate import WeblateClient  # noqa: E402


def plugin_texts(plugin_dir, locale):
    """key -> text from the plugin's locale file (locales/<code>.json; the
    code is lower-case with a dash, e.g. zh-hans)."""
    names = [locale, locale.lower(), locale.replace("_", "-"), locale.replace("_", "-").lower()]
    for n in names:
        path = os.path.join(plugin_dir, "locales", f"{n}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    sys.exit(f"no locales/{locale}.json in {plugin_dir}")


def choose(unit, texts, script):
    """-> (text, None) to save, (None, 'absent') or (None, reason)."""
    t = texts.get(unit.get("context"))
    if not isinstance(t, str) or not t.strip():
        return None, "absent"
    bad = bundle_checks.check_string(unit["source"][0], t, {"flags": unit.get("flags") or ""}, script)
    return (None, f"{bad[0]}: {bad[1]}") if bad else (t, None)


def run(args, reader, translator, texts):
    script = args.script or b.SCRIPTS.get(args.locale, "Latn")
    stats = {"saved": 0, "not_in_plugin": 0, "rejected": 0, "failed": 0, "skipped_legal": 0}
    rejected, streak = [], 0
    for comp in b.components(reader, args.component):
        for u in b.units_of(reader, comp, args.locale, "state%3A%3Ctranslated"):
            if not b.needs_translation(u):
                continue
            if b.is_legal(u):
                # the plugin has no English baseline, so its legal wording may
                # be out of date: legal strings are translated and reviewed on purpose
                stats["skipped_legal"] += 1
                continue
            text, why = choose(u, texts, script)
            if text is None:
                if why == "absent":
                    stats["not_in_plugin"] += 1
                else:
                    stats["rejected"] += 1
                    rejected.append({"unit": u["id"], "key": u.get("context"), "why": why})
                continue
            if args.dry_run:
                stats["saved"] += 1
                continue
            try:
                b.patch(translator, u["id"], {"target": [text], "state": b.TRANSLATED})
                stats["saved"] += 1
                streak = 0
            except b.WeblateError as e:
                stats["failed"] += 1
                streak += 1
                if streak >= b.MAX_CONSECUTIVE_FAILURES:
                    print(f"stopping: {streak} writes failed in a row", file=sys.stderr)
                    return stats, rejected
                rejected.append({"unit": u["id"], "key": u.get("context"), "why": f"write failed: {str(e)[:120]}"})
        print(f"fill {comp}: {stats}", flush=True)
    return stats, rejected


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--locale", required=True, help="Weblate language code, e.g. de, es, zh_Hans")
    p.add_argument("--plugin", required=True, help="the ut-plugin-language-<code> checkout")
    p.add_argument("--weblate-url", default=os.environ.get("WEBLATE_URL", b.WEBLATE_URL))
    p.add_argument("--script", help="ISO 15924 script (default from the locale)")
    p.add_argument("--component", action="append")
    p.add_argument("--approve", action="store_true", help="then approve what was saved (tools/bulk_locale.py approve)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report", help="where to write the JSON report")
    return p.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    b.LEGAL_KEYS.update(b.load_legal_keys())
    reader = WeblateClient(a.weblate_url, b.secret(b.APPROVER_KEYCHAIN, "WEBLATE_APPROVER_TOKEN"))
    translator = WeblateClient(a.weblate_url, b.secret(b.TRANSLATOR_KEYCHAIN, "WEBLATE_TRANSLATOR_TOKEN"))
    stats, rejected = run(a, reader, translator, plugin_texts(a.plugin, a.locale))
    print("summary:", json.dumps(stats))
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump({"locale": a.locale, "stats": stats, "rejected": rejected}, f, ensure_ascii=False, indent=2)
    if a.approve and not a.dry_run and stats["saved"]:
        # approves every waiting non-legal string of the language (and component)
        argv = ["approve", "--locale", a.locale, "--weblate-url", a.weblate_url]
        for c in a.component or []:
            argv += ["--component", c]
        if a.report:
            argv += ["--report", a.report + ".approve.json"]
        return b.main(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
