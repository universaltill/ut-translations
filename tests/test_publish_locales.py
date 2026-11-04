"""publish_locales.py (ADR-0177 section 2/3; ut-docs#5405 slice 3): the gate,
URL regions, omitted-broken units, legal English-only, collisions, --check.
The Weblate API is stubbed in memory."""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import publish_locales as pl  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COMP = "website"
EN = {"a.one": "Hello {name}", "a.two": "Click <b>here</b>", "a.three": "Plain", "a.legal": "Terms text", "nav.home": "Home"}
META = {"a.legal": {"labels": ["legal"]}, "nav.home": {"core": True, "labels": []}}
ACCOUNTS = {"bot-ai-a": {"model": "m1", "role": "translator"}, "bot-ai-b": {"model": "m2", "role": "reviewer"}}


def unit(uid, key, target, state=30, source=None):
    return {"id": uid, "context": key, "source": [source if source is not None else EN[key]], "target": [target],
            "state": state, "previous_source": ""}


def change(uid, action, user, target, author=None):
    u = lambda n: f"https://x/api/users/{n}/"  # noqa: E731
    return {"unit": f"https://x/api/units/{uid}/", "action": action, "target": target, "user": u(user),
            "author": u(author or user), "timestamp": f"2026-11-0{uid % 9 + 1}T00:00:00Z", "id": uid * 10 + action, "details": {}}


def good_history(uid, target, author="alice", approver="bob"):
    return [change(uid, 2, author, target), change(uid, 36, approver, target)]


class FakeWeblate:
    """get/list over in-memory data, keyed like the real API paths."""

    def __init__(self):
        self.languages = [{"code": "en"}, {"code": "de"}]
        self.dirs = {"en": "ltr", "de": "ltr", "ar": "rtl", "it": "ltr", "xx": "ltr", "ms": "ltr"}
        self.units = {}      # (comp, code) -> [units]
        self.changes = {}    # (comp, code) -> [changes] or None (unreadable)

    def get(self, path):
        if path.startswith("/api/languages/"):
            code = path.split("/")[3]
            return {"code": code, "direction": self.dirs[code]} if code in self.dirs else None
        raise AssertionError(path)

    def list(self, path):
        if path.startswith("/api/projects/"):
            return self.languages
        parts = path.split("?")[0].strip("/").split("/")  # api translations proj comp code units|changes
        comp, code, kind = parts[3], parts[4], parts[5]
        if kind == "units":
            return self.units.get((comp, code))
        c = self.changes.get((comp, code), [])
        if isinstance(c, Exception):
            raise c
        return c


def make_repo(root, en=None, meta=None, policy=None, regions=None):
    for d in ("config", "meta", COMP):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for f in ("ai-accounts.json",):
        shutil.copy(os.path.join(REPO, "config", f), os.path.join(root, "config", f))
    ai = {"accounts": ACCOUNTS}
    json.dump(ai, open(os.path.join(root, "config/ai-accounts.json"), "w"))
    pol = json.load(open(os.path.join(REPO, "config/publish-policy.json")))
    pol.update({"min_coverage": 0.5})
    pol.update(policy or {})
    json.dump(pol, open(os.path.join(root, "config/publish-policy.json"), "w"))
    reg = json.load(open(os.path.join(REPO, "config/locale-regions.json")))
    if regions is not None:
        reg["locales"] = regions
    json.dump(reg, open(os.path.join(root, "config/locale-regions.json"), "w"))
    json.dump({"schema": 1, "surfaces": {"website": {"repo": "https://example.invalid/r.git", "english": "i18n/en.json",
              "metadata": "i18n/en.meta.json", "component": COMP, "name": "Website"}}}, open(os.path.join(root, "config/surfaces.json"), "w"))
    json.dump(en or EN, open(os.path.join(root, COMP, "en.json"), "w"))
    json.dump(meta if meta is not None else META, open(os.path.join(root, "meta", f"{COMP}.json"), "w"))


def de_data(fake, targets, history_by_key=None):
    """German units for every key in `targets`, all approved by alice->bob."""
    fake.units[(COMP, "de")] = []
    fake.changes[(COMP, "de")] = []
    for i, (k, t) in enumerate(targets.items(), 1):
        fake.units[(COMP, "de")].append(unit(i, k, t))
        fake.changes[(COMP, "de")] += (history_by_key or {}).get(k) or good_history(i, t)


GOOD_DE = {"a.one": "Hallo {name}", "a.two": "Klicke <b>hier</b>", "a.three": "Einfach", "a.legal": "AGB", "nav.home": "Start"}


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        make_repo(self.tmp)
        self.fake = FakeWeblate()

    def run_compute(self, **kw):
        cfg = pl.Config(self.tmp)
        return pl.compute(self.tmp, cfg, self.fake, **kw)

    def loc(self, doc, code):
        return next(l for l in doc["locales"] if l["weblate"] == code)

    def test_publishes_with_regions_and_flags(self):
        de_data(self.fake, GOOD_DE)
        doc, files = self.run_compute()
        de = self.loc(doc, "de")
        self.assertEqual((de["url"], de["bcp47"], de["dir"], de["native_name"]), ("de-de", "de-DE", "ltr", "Deutsch"))
        self.assertTrue(de["published"])
        self.assertTrue(de["machine_label"])
        self.assertFalse(de["human_reviewed"])
        self.assertFalse(de["legal_approved"])
        self.assertTrue(de["review_verified"])
        self.assertEqual(self.loc(doc, "en")["url"], "en-gb")
        strings = json.loads(files["published/website/de.json"])
        self.assertEqual(strings["a.one"], "Hallo {name}")
        self.assertIn("published/website/en.json", files)

    def test_broken_placeholder_and_tag_units_are_omitted_not_blocking(self):
        make_repo(self.tmp, policy={"min_coverage": 0.3, "max_broken_ratio": 0.5})
        de_data(self.fake, dict(GOOD_DE, **{"a.one": "Hallo {namen}", "a.two": "Klicke hier"}))
        doc, files = self.run_compute()
        de = self.loc(doc, "de")
        s = de["surfaces"]["website"]
        self.assertTrue(de["published"], s)
        strings = json.loads(files["published/website/de.json"])
        self.assertNotIn("a.one", strings)
        self.assertNotIn("a.two", strings)
        self.assertEqual(s["omitted_broken"], 2)

    def test_too_many_broken_withdraws_the_surface(self):
        de_data(self.fake, {"a.one": "Hallo", "a.two": "x", "a.three": "Einfach", "nav.home": "Start"})
        make_repo(self.tmp, policy={"min_coverage": 0.1, "max_broken_ratio": 0.2})
        doc, files = self.run_compute()
        self.assertFalse(self.loc(doc, "de")["published"])
        self.assertNotIn("published/website/de.json", files)
        self.assertIn("broken", self.loc(doc, "de")["surfaces"]["website"]["reason"])

    def test_legal_key_is_english_only(self):
        de_data(self.fake, GOOD_DE)
        doc, files = self.run_compute()
        self.assertNotIn("a.legal", json.loads(files["published/website/de.json"]))
        self.assertEqual(self.loc(doc, "de")["surfaces"]["website"]["omitted"].get("legal_english_only"), 1)

    def test_legal_ships_with_a_listed_human_reviewer(self):
        make_repo(self.tmp, policy={"human_reviewers": ["bob"]})
        de_data(self.fake, GOOD_DE)
        doc, files = self.run_compute()
        de = self.loc(doc, "de")
        self.assertIn("a.legal", json.loads(files["published/website/de.json"]))
        self.assertTrue(de["legal_approved"])
        self.assertTrue(de["human_reviewed"])
        self.assertFalse(de["machine_label"])

    def test_self_approval_and_unapproved_are_omitted(self):
        hist = {"a.three": good_history(3, "Einfach", author="alice", approver="alice")}
        de_data(self.fake, GOOD_DE, hist)
        doc, files = self.run_compute()
        self.assertNotIn("a.three", json.loads(files["published/website/de.json"]))
        self.assertEqual(self.loc(doc, "de")["surfaces"]["website"]["omitted"]["review_self_approved"], 1)

    def test_same_model_ai_approval_is_omitted(self):
        hist = {"a.three": good_history(3, "Einfach", author="bot-ai-a", approver="bot-ai-a")}
        de_data(self.fake, GOOD_DE, hist)
        _, files = self.run_compute()
        self.assertNotIn("a.three", json.loads(files["published/website/de.json"]))

    def test_unreadable_history_ships_on_state_and_marks_unverified(self):
        de_data(self.fake, GOOD_DE)
        self.fake.changes[(COMP, "de")] = pl.AuthRequired("401")
        doc, files = self.run_compute()
        de = self.loc(doc, "de")
        self.assertFalse(de["review_verified"])
        self.assertTrue(de["published"])
        self.assertNotIn("a.legal", json.loads(files["published/website/de.json"]))

    def test_other_history_errors_fail_the_run_not_the_gate(self):
        de_data(self.fake, GOOD_DE)
        self.fake.changes[(COMP, "de")] = pl.PublishError("HTTP 429")
        with self.assertRaisesRegex(pl.PublishError, "429"):
            self.run_compute()

    def test_added_attribute_or_js_url_is_omitted(self):
        make_repo(self.tmp, en=dict(EN, **{"a.two": 'Click <a href="/x">here</a>'}), policy={"max_broken_ratio": 0.5})
        de_data(self.fake, dict(GOOD_DE, **{"a.two": 'Klicke <a href="/x" onclick="e()">hier</a>'}))
        self.fake.units[(COMP, "de")][1]["source"] = ['Click <a href="/x">here</a>']
        doc, files = self.run_compute()
        self.assertNotIn("a.two", json.loads(files["published/website/de.json"]))
        self.assertEqual(self.loc(doc, "de")["surfaces"]["website"]["omitted"]["broken_html_attributes"], 1)

    def test_stale_source_is_omitted(self):
        de_data(self.fake, GOOD_DE)
        self.fake.units[(COMP, "de")][2]["source"] = ["Old English"]
        _, files = self.run_compute()
        self.assertNotIn("a.three", json.loads(files["published/website/de.json"]))

    def test_below_coverage_not_published_and_file_absent(self):
        de_data(self.fake, {"a.three": "Einfach"})
        doc, files = self.run_compute()
        self.assertFalse(self.loc(doc, "de")["published"])
        self.assertNotIn("published/website/de.json", files)

    def test_core_key_missing_blocks(self):
        make_repo(self.tmp, policy={"min_coverage": 0.1})
        de_data(self.fake, {k: v for k, v in GOOD_DE.items() if k != "nav.home"})
        doc, _ = self.run_compute()
        self.assertFalse(self.loc(doc, "de")["published"])
        self.assertIn("core", self.loc(doc, "de")["surfaces"]["website"]["reason"])

    def test_unpublished_language_absent_and_overrides(self):
        make_repo(self.tmp, policy={"overrides": {"de": {"min_coverage": 0.1}}})
        de_data(self.fake, {"a.three": "Einfach", "nav.home": "Start"})
        doc, _ = self.run_compute()
        self.assertTrue(self.loc(doc, "de")["published"])

    def test_withdrawn_is_sticky_and_leaves_when_back(self):
        de_data(self.fake, GOOD_DE)
        doc1, _ = self.run_compute()
        self.fake.units[(COMP, "de")] = []
        doc2, files = self.run_compute(previous=doc1)
        self.assertIn({"url": "de-de", "surface": "website"}, doc2["withdrawn"])
        doc3, _ = self.run_compute(previous=doc2)
        self.assertIn({"url": "de-de", "surface": "website"}, doc3["withdrawn"])
        de_data(self.fake, GOOD_DE)
        doc4, _ = self.run_compute(previous=doc3)
        self.assertEqual(doc4["withdrawn"], [])

    def test_collision_fails(self):
        regions = json.load(open(os.path.join(REPO, "config/locale-regions.json")))["locales"]
        regions["xx"] = dict(regions["de"])
        make_repo(self.tmp, regions=regions)
        self.fake.languages.append({"code": "xx"})
        with self.assertRaisesRegex(pl.PublishError, "both map to url locale"):
            self.run_compute()

    def test_unknown_language_without_babel_fails_asking_for_override(self):
        if pl_has_babel():
            self.skipTest("Babel installed: derives instead")
        self.fake.languages.append({"code": "xx"})
        with self.assertRaisesRegex(pl.PublishError, "locale-regions.json"):
            self.run_compute()

    def test_missing_direction_fails(self):
        self.fake.dirs.pop("de")
        with self.assertRaisesRegex(pl.PublishError, "direction"):
            self.run_compute()

    def test_rtl_from_weblate(self):
        self.fake.languages.append({"code": "ar"})
        doc, _ = self.run_compute()
        self.assertEqual(self.loc(doc, "ar")["dir"], "rtl")
        self.assertEqual(self.loc(doc, "ar")["url"], "ar-sa")

    def test_all_owner_regions(self):
        want = {"en": "en-gb", "de": "de-de", "tr": "tr-tr", "zh_Hans": "zh-cn", "fa": "fa-ir", "pt": "pt-pt", "es": "es-es",
                "fr": "fr-fr", "ar": "ar-sa", "it": "it-it", "ms": "ms-my"}
        reg = json.load(open(os.path.join(REPO, "config/locale-regions.json")))["locales"]
        self.assertEqual({k: v["url"] for k, v in reg.items()}, want)

    def test_surface_without_english_is_skipped(self):
        os.remove(os.path.join(self.tmp, COMP, "en.json"))
        doc, files = self.run_compute()
        self.assertEqual(files, {})
        self.assertFalse(self.loc(doc, "de")["published"])

    def test_unchanged_rerun_keeps_generated_at_and_writes_nothing(self):
        de_data(self.fake, GOOD_DE)
        doc, files = self.run_compute()
        self.assertTrue(pl.write(self.tmp, doc, files, None))
        prev = json.load(open(os.path.join(self.tmp, "published/locales.json")))
        doc2, files2 = self.run_compute(source_commit="abc")
        self.assertFalse(pl.write(self.tmp, doc2, files2, prev))

    def test_write_removes_stale_strings_file(self):
        de_data(self.fake, GOOD_DE)
        doc, files = self.run_compute()
        pl.write(self.tmp, doc, files, None)
        self.fake.units[(COMP, "de")] = []
        prev = json.load(open(os.path.join(self.tmp, "published/locales.json")))
        doc2, files2 = self.run_compute(previous=prev)
        pl.write(self.tmp, doc2, files2, prev)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "published/website/de.json")))

    def test_check_passes_then_fails_on_tampering(self):
        de_data(self.fake, GOOD_DE)
        doc, files = self.run_compute()
        pl.write(self.tmp, doc, files, None)
        self.assertEqual(pl.check(self.tmp), [])
        p = os.path.join(self.tmp, "published/website/de.json")
        s = json.load(open(p))
        s["a.one"] = "Hallo"
        s["zz.extra"] = "x"
        json.dump(s, open(p, "w"))
        problems = "\n".join(pl.check(self.tmp))
        self.assertIn("a.one", problems)
        self.assertIn("zz.extra", problems)

    def test_check_flags_file_of_unpublished_locale(self):
        de_data(self.fake, GOOD_DE)
        doc, files = self.run_compute()
        pl.write(self.tmp, doc, files, None)
        os.makedirs(os.path.join(self.tmp, "published/website"), exist_ok=True)
        json.dump({}, open(os.path.join(self.tmp, "published/website/ar.json"), "w"))
        self.assertTrue(any("ar.json" in p for p in pl.check(self.tmp)))

    def test_committed_config_and_published_tree_are_valid(self):
        self.assertEqual(pl.check(REPO), [])


class LegalTierPublishTest(unittest.TestCase):
    """Owner 2026-11-04 / ADR-0160 s4: an unapproved legal key is English by
    design: omitted, outside the coverage denominator and the core test."""

    LANGS = ["de", "fr", "es", "it", "pt", "tr", "ar", "fa", "zh_Hans"]

    def build(self, policy=None, drop=None, break_legal=False):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        en = {f"s.{i}": f"Text {i}" for i in range(360)}
        en.update({f"legal.{i}": f"Legal {i}" for i in range(97)})
        meta = {f"legal.{i}": {"labels": ["legal"]} for i in range(97)}
        meta["legal.0"]["core"] = True  # a core key that is legal-tier
        meta["s.0"] = {"core": True, "labels": []}
        make_repo(tmp, en=en, meta=meta, policy=dict({"min_coverage": 0.9}, **(policy or {})))
        fake = FakeWeblate()
        fake.languages = [{"code": "en"}] + [{"code": c} for c in self.LANGS]
        for c in self.LANGS:
            fake.dirs[c] = ("rtl" if c in ("ar", "fa") else "ltr")
            fake.units[(COMP, c)], fake.changes[(COMP, c)] = [], []
            for i, (k, t) in enumerate(en.items(), 1):
                if c == "de" and drop and drop(k):
                    continue
                if c == "de" and break_legal and k == "legal.0":
                    t_bad = "x {oops}"
                    fake.units[(COMP, c)].append({"id": i, "context": k, "source": [t], "target": [t_bad], "state": 30, "previous_source": ""})
                    fake.changes[(COMP, c)] += good_history(i, t_bad)
                    continue
                fake.units[(COMP, c)].append({"id": i, "context": k, "source": [t], "target": ["x " + t], "state": 30, "previous_source": ""})
                fake.changes[(COMP, c)] += good_history(i, "x " + t)
        doc, files = pl.compute(tmp, pl.Config(tmp), fake)
        return doc, files

    def test_nine_languages_publish_with_legal_omitted(self):
        doc, files = self.build()
        for c in self.LANGS:
            l = next(x for x in doc["locales"] if x["weblate"] == c)
            w = l["surfaces"]["website"]
            self.assertTrue(l["published"], (c, l["reason"]))
            self.assertEqual((w["omitted_legal"], w["total"], w["approved"]), (97, 457, 360))
            self.assertEqual(w["coverage"], 1.0)
            self.assertTrue(w["core_ok"])
            strings = json.loads(files[f"published/website/{c}.json"])
            self.assertFalse([k for k in strings if k.startswith("legal.")])
            self.assertFalse(l["legal_approved"])

    def test_human_reviewer_ships_the_legal_keys(self):
        doc, files = self.build({"human_reviewers": ["bob"]})
        w = next(x for x in doc["locales"] if x["weblate"] == "de")["surfaces"]["website"]
        self.assertEqual((w["omitted_legal"], w["approved"]), (0, 457))
        self.assertIn("legal.5", json.loads(files["published/website/de.json"]))

    def test_non_legal_gaps_still_block(self):
        doc, _ = self.build(drop=lambda k: k.startswith("s.") and int(k[2:]) % 5 == 0)
        self.assertFalse(next(x for x in doc["locales"] if x["weblate"] == "de")["published"])

    def test_missing_core_key_blocks_even_with_legal_omitted(self):
        doc, _ = self.build(drop=lambda k: k == "s.0")
        de = next(x for x in doc["locales"] if x["weblate"] == "de")
        self.assertFalse(de["published"])
        self.assertIn("core", de["surfaces"]["website"]["reason"])

    def test_broken_legal_key_is_a_defect_not_an_omission(self):
        doc, _ = self.build({"human_reviewers": ["bob"]}, break_legal=True)
        w = next(x for x in doc["locales"] if x["weblate"] == "de")["surfaces"]["website"]
        self.assertEqual(w["omitted_legal"], 0)
        self.assertEqual(w["omitted_broken"], 1)
        self.assertFalse(w["core_ok"])


class WorkflowTest(unittest.TestCase):
    def test_workflow_has_no_secrets_and_least_privilege(self):
        text = open(os.path.join(REPO, ".github/workflows/publish-locales.yml"), encoding="utf-8").read()
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("secrets.", body)
        self.assertIn("permissions: {}", body)
        self.assertEqual(body.count("contents: write"), 1)
        for trig in ("schedule:", "workflow_dispatch:", "workflow_run:", "workflows: [sync-english]"):
            self.assertIn(trig, body)
        self.assertNotIn("${{", "\n".join(l for l in body.splitlines() if l.strip().startswith(("run:", "python3"))))


def pl_has_babel():
    try:
        import babel  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    unittest.main()
