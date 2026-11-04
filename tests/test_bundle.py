"""Bundle export, review rules, per-string checks, scans and packaging
(ut-docs#4367) against a fake Weblate on 127.0.0.1 and copies of
universal-till's real compliance and naming guards. Nothing here talks to
translate.universaltill.com or the marketplace."""

import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import sys
import tarfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import build_bundle as bb  # noqa: E402
import bundle_checks as bc  # noqa: E402
from bundle_fixtures import TILL_EN, language_rows, TOKEN, BundleCase, add_english, side_commit, sync_repo, till_source  # noqa: E402
from provision_weblate import WeblateClient  # noqa: E402

NOW = dt.datetime(2026, 10, 16, 12, 30, 5, tzinfo=dt.timezone.utc)
AI = {"ai-tr-opus": {"model": "claude-opus-5.5", "role": "translator"},
      "ai-rv-qwen": {"model": "qwen3.8-27b", "role": "reviewer"},
      "ai-rv-opus": {"model": "claude-opus-5.5", "role": "reviewer"}}

NEW, APPROVE, ACCEPT, CHANGE, SOURCE_CHANGE = bb.A_NEW, bb.A_APPROVE, bb.A_ACCEPT, bb.A_CHANGE, bb.A_SOURCE_CHANGE


@unittest.skipUnless(till_source(), "needs a universal-till checkout (UTT_TILL_SOURCE) for the real guards")
class Export(BundleCase, unittest.TestCase):
    def setUp(self):
        self.make(AI)

    def build(self, locale="de", text_source="api"):
        client = WeblateClient(self.fake.base(), TOKEN)
        b = bb.Builder(self.repo, locale, client, self.till, text_source, now=NOW)
        return b, b.build(self.out)

    def unit(self, key, target, history, area="sale", lang="de", **kw):
        en = {**bb.load_json(f"{self.repo}/sale/en.json"), **bb.load_json(f"{self.repo}/receipts-and-legal/en.json")}
        return self.fake.add_unit(area, lang, key, kw.pop("source", en[key]), target, history=history, **kw)

    def reviewed(self, key, target, area="sale", lang="de", **kw):
        return self.unit(key, target, [(NEW, "alice", target), (APPROVE, "bob", target)], area, lang, **kw)

    def shipped(self, b):
        return b.strings

    # --- review rules ------------------------------------------------------

    def test_string_approved_by_another_human_ships(self):
        self.reviewed("pos.total", "Summe")
        b, rep = self.build()
        self.assertEqual(self.shipped(b), {"pos.total": "Summe"})
        self.assertEqual(rep["bundle_json"]["approved_count"], 1)

    def test_translated_but_unapproved_string_is_not_exported(self):
        self.unit("pos.total", "Summe", [(NEW, "alice", "Summe")], state=20)
        b, rep = self.build()
        self.assertEqual(self.shipped(b), {})
        self.assertEqual(rep["bundle_json"]["omitted"]["not_approved"], len(bb.load_json(f"{self.repo}/sale/en.json")) + 2)

    def test_self_approved_string_is_omitted(self):
        self.unit("pos.total", "Summe", [(NEW, "alice", "Summe"), (APPROVE, "alice", "Summe")])
        b, rep = self.build()
        self.assertNotIn("pos.total", self.shipped(b))
        self.assertEqual(rep["omitted"]["pos.total"], "self_approved")

    def test_reviewer_rewriting_and_approving_in_one_step_is_self_approval(self):
        # Weblate records an edit-and-approve by a reviewer as one APPROVE
        # change carrying the new text: the reviewer wrote what they approved.
        self.unit("pos.total", "Gesamt", [(NEW, "alice", "Summe"), (APPROVE, "bob", "Gesamt")])
        b, rep = self.build()
        self.assertNotIn("pos.total", self.shipped(b))
        self.assertEqual(rep["omitted"]["pos.total"], "self_approved")

    def test_text_changed_after_approval_needs_a_new_approval(self):
        self.unit("pos.total", "Gesamt", [(NEW, "alice", "Summe"), (APPROVE, "bob", "Summe"),
                                          (CHANGE, "bob", "Gesamt")])
        b, rep = self.build()
        self.assertNotIn("pos.total", self.shipped(b))

    def test_suggestion_accepted_straight_into_approved_counts_as_approval(self):
        self.unit("pos.total", "Summe", [(ACCEPT, "bob", "Summe", "alice", {"state": 30})])
        b, _ = self.build()
        self.assertEqual(self.shipped(b), {"pos.total": "Summe"})

    def test_bulk_approved_string_without_an_approval_event_is_omitted(self):
        self.unit("pos.total", "Summe", [(NEW, "alice", "Summe"), (bb.A_BULK_EDIT, "bob", "Summe")])
        b, rep = self.build()
        self.assertEqual(rep["omitted"]["pos.total"], "no_approval")

    def test_ai_translation_reviewed_by_the_same_model_is_omitted(self):
        self.unit("pos.total", "Summe", [(NEW, "ai-tr-opus", "Summe"), (APPROVE, "ai-rv-opus", "Summe")])
        b, rep = self.build()
        self.assertEqual(rep["omitted"]["pos.total"], "same_model")

    def test_ai_translation_reviewed_by_a_different_model_ships(self):
        self.unit("pos.total", "Summe", [(NEW, "ai-tr-opus", "Summe"), (APPROVE, "ai-rv-qwen", "Summe")])
        b, _ = self.build()
        self.assertEqual(self.shipped(b), {"pos.total": "Summe"})

    def test_approval_by_an_unlisted_bot_is_omitted(self):
        self.unit("pos.total", "Summe", [(NEW, "alice", "Summe"), (APPROVE, "ai-unknown", "Summe")])
        b, rep = self.build()
        self.assertEqual(rep["omitted"]["pos.total"], "unknown_ai_account")

    def test_legal_string_without_a_human_approval_is_omitted(self):
        # AC (ut-docs#4367): a legal key approved by an AI reviewer on a
        # different model, or by a human outside human-reviewers, is omitted.
        self.unit("receipt.vat", "inkl. MwSt.", [(NEW, "ai-tr-opus", "inkl. MwSt."), (APPROVE, "ai-rv-qwen", "inkl. MwSt.")],
                  area="receipts-and-legal")
        self.unit("receipt.thanks", "Danke", [(NEW, "alice", "Danke"), (APPROVE, "carol", "Danke")],
                  area="receipts-and-legal")
        b, rep = self.build()
        self.assertNotIn("receipt.vat", self.shipped(b))
        self.assertNotIn("receipt.thanks", self.shipped(b))
        self.assertEqual(rep["omitted"]["receipt.vat"], "legal_needs_human")
        self.assertEqual(rep["omitted"]["receipt.thanks"], "legal_needs_human")
        with tarfile.open(os.path.join(self.out, rep["bundle"])) as tf:
            shipped = json.load(tf.extractfile("locales/de.json"))
        self.assertNotIn("receipt.vat", shipped)
        self.assertNotIn("VAT included", shipped.values())  # never written as English

    def test_approval_by_an_account_in_no_reviewing_role_is_omitted(self):
        # Deny by default (review finding 3): an approver must be a listed AI
        # account or a human-reviewers member. carol is neither.
        self.unit("pos.total", "Summe", [(NEW, "alice", "Summe"), (APPROVE, "carol", "Summe")])
        self.unit("pos.short", "Zahl", [(NEW, "ai-tr-opus", "Zahl"), (APPROVE, "carol", "Zahl")])
        b, rep = self.build()
        self.assertEqual(self.shipped(b), {})
        self.assertEqual(rep["omitted"]["pos.total"], "unknown_approver")
        self.assertEqual(rep["omitted"]["pos.short"], "unknown_approver")

    def test_translated_locale_fails_while_no_ai_account_is_listed(self):
        self.tmp.cleanup()
        self.make({})
        self.reviewed("pos.total", "Summe")
        with self.assertRaisesRegex(bb.BundleError, "#4366"):
            self.build()

    def test_legal_string_approved_by_a_human_reviewer_ships(self):
        self.unit("receipt.vat", "inkl. MwSt.", [(NEW, "ai-tr-opus", "inkl. MwSt."), (APPROVE, "rita", "inkl. MwSt.")],
                  area="receipts-and-legal")
        b, _ = self.build()
        self.assertEqual(self.shipped(b), {"receipt.vat": "inkl. MwSt."})

    def test_legal_label_from_weblate_alone_also_needs_a_human(self):
        self.unit("pos.total", "Summe", [(NEW, "alice", "Summe"), (APPROVE, "carol", "Summe")], labels=["legal"])
        b, rep = self.build()
        self.assertEqual(rep["omitted"]["pos.total"], "legal_needs_human")

    def test_human_approval_before_an_english_change_does_not_count(self):
        self.unit("receipt.vat", "inkl. MwSt.", [(NEW, "alice", "inkl. MwSt."), (APPROVE, "rita", "inkl. MwSt."),
                                                (SOURCE_CHANGE, None, "inkl. MwSt.")], area="receipts-and-legal")
        b, rep = self.build()
        self.assertEqual(rep["omitted"]["receipt.vat"], "no_approval")

    def test_approval_against_old_english_is_omitted(self):
        self.reviewed("pos.total", "Summe", source="Sum")
        self.reviewed("pos.short", "Zahl", previous_source="Pay now")
        b, rep = self.build()
        self.assertEqual(self.shipped(b), {})
        self.assertTrue(rep["omitted"]["pos.total"].startswith("stale_source"))
        self.assertTrue(rep["omitted"]["pos.short"].startswith("stale_source"))

    def test_token_that_cannot_see_groups_fails_the_build(self):
        self.reviewed("pos.total", "Summe")
        self.fake.hide_groups = True
        with self.assertRaisesRegex(bb.BundleError, "user.view"):
            self.build()

    def test_history_is_read_across_pages(self):
        for key, text in (("pos.total", "Summe"), ("pos.short", "Zahlen"), ("pos.items", "%d Artikel")):
            self.reviewed(key, text)
        b, _ = self.build()
        self.assertEqual(set(self.shipped(b)), {"pos.total", "pos.short", "pos.items"})
        self.assertTrue(any("page=3" in p for _, p in self.fake.requests), "fake never paginated")

    # --- per-string checks ---------------------------------------------------

    def test_failing_strings_are_omitted_never_written_as_english(self):
        self.reviewed("pos.items", "Artikel")              # %d dropped
        self.reviewed("pos.short", "Bezahlen")             # 8 > max-length 6
        self.reviewed("pos.link", "Siehe Hilfe")           # link dropped
        self.reviewed("pos.name", "Hallo {nom}")           # wrong placeholder
        self.reviewed("pos.total", " ")                    # blank
        b, rep = self.build()
        self.assertEqual(self.shipped(b), {})
        reasons = {k: v.split(":")[0] for k, v in rep["omitted"].items()}
        self.assertEqual(reasons, {"pos.items": "placeholders", "pos.short": "max_length",
                                   "pos.link": "dropped", "pos.name": "placeholders", "pos.total": "empty"})
        with tarfile.open(os.path.join(self.out, rep["bundle"])) as tf:
            self.assertEqual(json.load(tf.extractfile("locales/de.json")), {})

    def test_passing_strings_with_placeholders_and_markup_ship(self):
        self.reviewed("pos.items", "%d Artikel")
        self.reviewed("pos.link", 'Siehe <a href="/help/sale">Hilfe</a>')
        self.reviewed("pos.name", "Hallo {name}")
        b, _ = self.build()
        self.assertEqual(len(self.shipped(b)), 3)

    def test_wrong_script_is_omitted(self):
        self.reviewed("pos.total", "Toplam", lang="fa")    # Latin, and not the English, in Persian
        self.reviewed("pos.short", "پرداخت", lang="fa")
        b, rep = self.build("fa")
        self.assertEqual(self.shipped(b), {"pos.short": "پرداخت"})
        self.assertTrue(rep["omitted"]["pos.total"].startswith("script"))

    # --- scans -------------------------------------------------------------

    def test_gobd_konform_fails_the_publish(self):
        # AC (ut-docs#4367): a string containing "GoBD-konform" fails the
        # bundle — no tarball is written, main() exits 1.
        self.reviewed("pos.total", "Kasse GoBD-konform")
        with self.assertRaisesRegex(bb.BundleError, "guard-compliance-claims"):
            self.build()
        self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))
        env = {"WEBLATE_URL": self.fake.base(), "WEBLATE_TOKEN": TOKEN}
        with patched_env(env), contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()):
            code = bb.main(["--locale", "de", "--repo", self.repo, "--till-source", self.till, "--out", self.out])
        self.assertEqual(code, 1)
        self.assertIn("gobd-konform", err.getvalue().lower())

    def test_compliance_term_hidden_by_a_unicode_hyphen_still_fails(self):
        self.reviewed("pos.total", "GoBD‑konform")
        with self.assertRaisesRegex(bb.BundleError, "guard-compliance-claims"):
            self.build()

    def test_competitor_imitation_wording_fails_the_publish(self):
        self.reviewed("pos.total", "wie SumUp")
        with self.assertRaisesRegex(bb.BundleError, "guard-competitor-naming"):
            self.build()

    def test_guards_are_read_at_source_commit_not_head(self):
        # HEAD of the till checkout dropped the term; the bundle's
        # source_commit still has it, and that copy is what runs.
        self.tmp.cleanup()
        self.make(AI, till_edit=lambda b: b.replace(b'  "gobd-konform"\n', b""))
        self.reviewed("pos.total", "Kasse GoBD-konform")
        with self.assertRaisesRegex(bb.BundleError, "(?i)gobd-konform"):
            self.build()
        # Control: pinned to HEAD (the edited guard) the same string passes,
        # so the failure above really came from the source_commit copy.
        head = bb.subprocess.run(["git", "-C", self.till, "rev-parse", "HEAD"], stdout=bb.subprocess.PIPE,
                                 check=True).stdout.decode().strip()
        src = bb.load_json(f"{self.repo}/meta/source.json")
        src["main"]["commit"] = head
        with open(f"{self.repo}/meta/source.json", "w") as f:
            json.dump(src, f)
        b, _ = self.build()
        self.assertEqual(b.strings, {"pos.total": "Kasse GoBD-konform"})

    def test_guards_run_without_the_weblate_token_in_their_env(self):
        self.tmp.cleanup()
        self.make(AI, till_edit=lambda b: b.replace(
            b"set -euo pipefail", b'echo "leak=${WEBLATE_TOKEN:-none}"; exit 1', 1))
        head = bb.subprocess.run(["git", "-C", self.till, "rev-parse", "HEAD"], stdout=bb.subprocess.PIPE,
                                 check=True).stdout.decode().strip()
        src = bb.load_json(f"{self.repo}/meta/source.json")
        src["main"]["commit"] = head
        with open(f"{self.repo}/meta/source.json", "w") as f:
            json.dump(src, f)
        self.reviewed("pos.total", "Summe")
        with patched_env({"WEBLATE_TOKEN": TOKEN}):
            with self.assertRaises(bb.BundleError) as cm:
                self.build()
        self.assertIn("leak=none", str(cm.exception))
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_scans_need_the_till_source(self):
        client = WeblateClient(self.fake.base(), TOKEN)
        with self.assertRaisesRegex(bb.BundleError, "till-source"):
            bb.Builder(self.repo, "de", client, None, now=NOW).build(self.out)

    # --- packaging ---------------------------------------------------------

    def test_bundle_format(self):
        self.reviewed("pos.total", "Summe")
        b, rep = self.build()
        path = os.path.join(self.out, rep["bundle"])
        with tarfile.open(path) as tf:
            self.assertEqual(sorted(tf.getnames()), ["bundle.json", "locales/de.json", "manifest.json"])
            for m in tf.getmembers():
                self.assertTrue(m.isfile())
                self.assertFalse(m.name.startswith(("./", "/")))
            manifest = json.load(tf.extractfile("manifest.json"))
            info = json.load(tf.extractfile("bundle.json"))
        self.assertEqual(manifest["id"], "com.universaltill.language-de")
        self.assertEqual(manifest["version"], "2.20261016.45005")
        self.assertEqual({k: manifest[k] for k in ("canonical_type", "runtime", "device_arch", "locales", "permissions")},
                         {"canonical_type": "language", "runtime": "none", "device_arch": "any",
                          "locales": ["de"], "permissions": ["ui.locale"]})
        self.assertEqual(manifest["min_pos_version"], "0.33.0")
        self.assertEqual(info["locale"], "de")
        self.assertEqual(info["pos_lines"], ["0.33", "0.34"])
        self.assertEqual(info["source_commit"], self.sha)
        self.assertEqual(info["generated_at"], "2026-10-16T12:30:05Z")
        self.assertEqual(info["key_count"], 7)
        self.assertEqual(info["approved_count"], 1)
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rep["sha256"])
        with open(path + ".sha256") as f:
            self.assertEqual(f.read(), f"{rep['sha256']}  {rep['bundle']}\n")

    def test_same_input_same_bytes(self):
        self.reviewed("pos.total", "Summe")
        _, a = self.build()
        _, b = self.build()
        self.assertEqual(a["sha256"], b["sha256"])

    def test_versions_rise_above_the_packs(self):
        langs = language_rows()
        b = bb.Builder(self.repo, "de", None, self.till, now=dt.datetime(2026, 10, 16, 0, 0, 0, tzinfo=dt.timezone.utc),
                       languages=langs)
        later = bb.Builder(self.repo, "de", None, self.till, now=dt.datetime(2026, 10, 16, 0, 0, 10, tzinfo=dt.timezone.utc),
                           languages=langs)

        def key(v):
            return [int(x) for x in v.split(".")]
        self.assertGreater(key(b.version()), key("1.1.248"))
        self.assertGreater(key(later.version()), key(b.version()))

    # --- publish gate ------------------------------------------------------

    def test_thin_de_bundle_is_not_publishable(self):
        self.reviewed("pos.total", "Summe")
        _, rep = self.build()
        self.assertFalse(rep["publishable"])
        self.assertIn("min_coverage", rep["problems"][0])
        env = {"WEBLATE_URL": self.fake.base(), "WEBLATE_TOKEN": TOKEN}
        with patched_env(env), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = bb.main(["--locale", "de", "--repo", self.repo, "--till-source", self.till, "--out", self.out,
                            "--require-publishable"])
        self.assertEqual(code, 3)

    def test_empty_bundle_is_not_publishable(self):
        _, rep = self.build("tr")
        self.assertEqual(rep["bundle_json"]["approved_count"], 0)
        self.assertFalse(rep["publishable"])
        self.assertIn("empty", rep["problems"][0])

    def test_partial_bundle_for_a_locale_without_a_pack_is_publishable(self):
        self.reviewed("pos.total", "Toplam", lang="tr")
        _, rep = self.build("tr")
        self.assertTrue(rep["publishable"], rep["problems"])
        self.assertTrue(rep["human_spot_check"])

    def test_github_outputs(self):
        self.reviewed("pos.total", "Toplam", lang="tr")
        gh = os.path.join(self.tmp.name, "gh_output")
        env = {"WEBLATE_URL": self.fake.base(), "WEBLATE_TOKEN": TOKEN, "GITHUB_OUTPUT": gh}
        with patched_env(env), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bb.main(["--locale", "tr", "--repo", self.repo, "--till-source", self.till,
                                      "--out", self.out]), 0)
        with open(gh) as f:
            out = dict(line.strip().split("=", 1) for line in f)
        self.assertNotIn("publishable", out)  # nothing reads it; report.json is the record
        self.assertTrue(out["bundle"].endswith("_universal.tar.gz"))
        with open(os.path.join(self.out, "report.json"), "rb") as f:
            self.assertEqual(out["report_sha256"], hashlib.sha256(f.read()).hexdigest())

    # --- sources -----------------------------------------------------------

    def test_git_text_source_ships_only_what_matches_weblate(self):
        self.reviewed("pos.total", "Summe")
        self.reviewed("pos.short", "Zahlen")
        bb_path = os.path.join(self.repo, "sale", "de.json")
        with open(bb_path, "w", encoding="utf-8") as f:
            json.dump({"pos.total": "Summe", "pos.short": "Zahl!"}, f)
        b, rep = self.build(text_source="git")
        self.assertEqual(self.shipped(b), {"pos.total": "Summe"})
        self.assertTrue(rep["omitted"]["pos.short"].startswith("git_mismatch"))

    def english(self):
        """The repo's English synced from a till that holds it (main and
        line 0.34 at the same commit)."""
        add_english(self.till)
        sync_repo(self.repo, self.till)

    def test_english_bundle_is_the_source_and_needs_no_weblate(self):
        self.english()
        b = bb.Builder(self.repo, "en", None, self.till, now=NOW)
        rep = b.build(self.out)
        self.assertEqual(rep["bundle_json"]["approved_count"], len(TILL_EN))
        self.assertEqual(b.strings["pos.total"], "Total")
        self.assertEqual(self.fake.requests, [])

    def test_english_that_differs_from_the_till_fails_the_en_build(self):
        # Review finding 5: <area>/en.json is the bundle's text for en; a
        # change to it that universal-till's web/locales/en.json does not
        # hold fails the build.
        self.english()
        p = os.path.join(self.repo, "sale", "en.json")
        en = bb.load_json(p)
        en["pos.total"] = "Total (GoBD-free)"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(en, f)
        with self.assertRaisesRegex(bb.BundleError, "sale/en.json"):
            bb.Builder(self.repo, "en", None, self.till, now=NOW).build(self.out)
        self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))

    def test_dropped_legal_label_fails_the_en_build(self):
        self.english()
        p = os.path.join(self.repo, "meta", "receipts-and-legal.json")
        meta = bb.load_json(p)
        self.assertEqual(meta["receipt.vat"]["labels"], ["legal"])
        meta["receipt.vat"]["labels"] = []
        with open(p, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        with self.assertRaisesRegex(bb.BundleError, "meta/receipts-and-legal.json"):
            bb.Builder(self.repo, "en", None, self.till, now=NOW).build(self.out)

    def test_en_build_refuses_a_line_commit_that_is_not_its_tag(self):
        self.english()
        p = os.path.join(self.repo, "meta", "source.json")
        src = bb.load_json(p)
        src["lines"]["0.34"]["commit"] = self.sha  # an older commit, not v0.34.7
        with open(p, "w", encoding="utf-8") as f:
            json.dump(src, f)
        with self.assertRaisesRegex(bb.BundleError, "v0.34.7"):
            bb.Builder(self.repo, "en", None, self.till, now=NOW).build(self.out)

    @unittest.skipUnless(till_source(), "needs a universal-till checkout")
    def test_this_repos_english_matches_universal_till(self):
        # The real files: this repo's English equals universal-till's at
        # meta/source.json's commits, so an en bundle can be built.
        b = bb.Builder(bb.os.path.join(os.path.dirname(__file__), ".."), "en", None, till_source(), now=NOW)
        rep = b.build(self.out)
        self.assertGreater(rep["bundle_json"]["approved_count"], 3000)

    # --- source commit -----------------------------------------------------

    def test_source_commit_off_main_is_refused_before_any_till_script_runs(self):
        # Review finding 2(a): a fork's commit is fetchable by sha, so
        # meta/source.json naming one would run the fork's guards.
        self.tmp.cleanup()
        self.make(AI, till_edit=lambda b: b.replace(b"set -euo pipefail", b"echo FORK-RAN; exit 1", 1))
        fork = side_commit(self.till)
        src = bb.load_json(f"{self.repo}/meta/source.json")
        src["main"]["commit"] = fork
        with open(f"{self.repo}/meta/source.json", "w") as f:
            json.dump(src, f)
        self.reviewed("pos.total", "Summe")
        with self.assertRaisesRegex(bb.BundleError, "not on universal-till main") as cm:
            self.build()
        self.assertNotIn("FORK-RAN", str(cm.exception))
        # Control: the same fork commit, once main holds it, runs (and fails
        # on its own guard), so the refusal above came from the main check.
        bb.subprocess.run(["git", "-C", self.till, "update-ref", "refs/remotes/origin/main", fork], check=True)
        with self.assertRaisesRegex(bb.BundleError, "FORK-RAN"):
            self.build()

    def test_no_scans_builds_without_running_the_till_guards(self):
        # The build job (Weblate token) runs no universal-till code: the
        # guards run later, in the verify job, through --scan-bundle.
        self.reviewed("pos.total", "Kasse GoBD-konform")
        client = WeblateClient(self.fake.base(), TOKEN)
        rep = bb.Builder(self.repo, "de", client, None, scans=False, now=NOW).build(self.out)
        bundle = os.path.join(self.out, rep["bundle"])
        self.assertTrue(os.path.isfile(bundle))
        with self.assertRaisesRegex(bb.BundleError, "guard-compliance-claims"):
            bb.scan_bundle(self.repo, self.till, bundle, "de")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = bb.main(["--locale", "de", "--repo", self.repo, "--till-source", self.till,
                            "--scan-bundle", bundle])
        self.assertEqual(code, 1)
        self.assertIn("gobd-konform", err.getvalue().lower())

    def test_scan_bundle_passes_a_clean_bundle_and_checks_its_identity(self):
        self.reviewed("pos.total", "Summe")
        client = WeblateClient(self.fake.base(), TOKEN)
        rep = bb.Builder(self.repo, "de", client, None, scans=False, now=NOW).build(self.out)
        bundle = os.path.join(self.out, rep["bundle"])
        bb.scan_bundle(self.repo, self.till, bundle, "de")
        with self.assertRaisesRegex(bb.BundleError, "language-tr"):
            bb.scan_bundle(self.repo, self.till, bundle, "tr")
        src = bb.load_json(f"{self.repo}/meta/source.json")
        src["main"]["commit"] = "f" * 40
        with open(f"{self.repo}/meta/source.json", "w") as f:
            json.dump(src, f)
        with self.assertRaisesRegex(bb.BundleError, "source_commit"):
            bb.scan_bundle(self.repo, self.till, bundle, "de")

    def test_translated_locale_needs_weblate(self):
        with self.assertRaisesRegex(bb.BundleError, "WEBLATE_URL"):
            bb.Builder(self.repo, "de", None, self.till, now=NOW).build(self.out)
        with self.assertRaisesRegex(bb.BundleError, "WEBLATE_URL"):
            bb.Builder(self.repo, "de", None, self.till, now=NOW, languages=language_rows()).build(self.out)

    def test_unknown_or_malformed_locale_is_refused(self):
        for bad in ("xx", "../de", "DE", "", "pt-BR", "de;ls"):
            with self.assertRaises(bb.BundleError):
                bb.Builder(self.repo, bad, None, self.till, now=NOW, languages=language_rows())


@unittest.skipUnless(till_source(), "needs a universal-till checkout (UTT_TILL_SOURCE) for the real guards")
class DerivedLanguages(BundleCase, unittest.TestCase):
    """A language added in Weblate builds with no change in this repo
    (ut-docs#4640); the review gate (ADR-0160 §4) is the same for it."""

    def setUp(self):
        self.make(AI)
        self.en = {**bb.load_json(f"{self.repo}/sale/en.json"), **bb.load_json(f"{self.repo}/receipts-and-legal/en.json")}
        self.config_before = self.config_bytes()

    def config_bytes(self):
        with open(f"{self.repo}/config/bundles.json", "rb") as f:
            return f.read()

    def add_language(self, code, name):
        self.fake.languages.append({"code": code, "name": name})

    def reviewed(self, lang, key, target, area="sale", history=None):
        h = history or [(NEW, "alice", target), (APPROVE, "bob", target)]
        return self.fake.add_unit(area, lang, key, self.en[key], target, history=h)

    def build(self, code, scans=False):
        client = WeblateClient(self.fake.base(), TOKEN)
        b = bb.Builder(self.repo, code, client, self.till if scans else None, scans=scans, now=NOW)
        return b, b.build(self.out)

    def members(self, rep):
        with tarfile.open(os.path.join(self.out, rep["bundle"])) as tf:
            names = sorted(tf.getnames())
            return names, json.load(tf.extractfile("manifest.json")), json.load(tf.extractfile("bundle.json"))

    def test_new_language_builds_with_no_repo_change(self):
        self.add_language("fr", "French")
        self.reviewed("fr", "pos.total", "Total")
        self.reviewed("fr", "pos.items", "%d articles")
        b, rep = self.build("fr", scans=True)
        names, manifest, info = self.members(rep)
        self.assertEqual(names, ["bundle.json", "locales/fr.json", "manifest.json"])
        self.assertEqual((manifest["id"], manifest["locales"], info["locale"]), ("com.universaltill.language-fr", ["fr"], "fr"))
        self.assertEqual(manifest["name"], "French")
        self.assertEqual(b.strings, {"pos.total": "Total", "pos.items": "%d articles"})
        self.assertFalse(rep["human_spot_check"])
        self.assertTrue(rep["publishable"], rep["problems"])
        self.assertEqual(self.config_bytes(), self.config_before)

    def test_zh_hans_gets_the_tag_for_id_locale_and_file(self):
        self.add_language("zh_Hans", "Chinese (Simplified Han script)")
        self.reviewed("zh_Hans", "pos.total", "总计")
        b, rep = self.build("zh_Hans")
        names, manifest, info = self.members(rep)
        self.assertEqual(manifest["id"], "com.universaltill.language-zh-hans")
        self.assertEqual((manifest["locales"], info["locale"], rep["locale"]), (["zh-hans"], "zh-hans", "zh-hans"))
        self.assertIn("locales/zh-hans.json", names)
        self.assertEqual(b.strings, {"pos.total": "总计"})
        # Weblate was asked with the Weblate code
        paths = [p for _, p in self.fake.requests]
        self.assertTrue(any("/sale/zh_Hans/units/" in p for p in paths))

    def test_wrong_script_for_a_derived_language_is_omitted(self):
        self.add_language("zh_Hans", "Chinese")
        self.reviewed("zh_Hans", "pos.total", "Gesamt")
        _, rep = self.build("zh_Hans")
        self.assertEqual(rep["omitted"]["pos.total"].split(":")[0], "script")

    def test_the_seven_keep_their_exact_specs(self):
        frozen = bb.load_json(os.path.join(os.path.dirname(__file__), "data", "frozen_bundle_specs.json"))
        self.assertEqual(sorted(frozen), ["ar", "de", "en", "es", "fa", "pt", "tr"])
        live = {r["code"]: r["name"] for r in self.fake.languages}
        for code, old in frozen.items():
            spec = bb.locales.resolve_spec(live, self.repo, code)
            self.assertEqual(spec["weblate"], old["weblate"], code)
            self.assertEqual(spec["name"], old["name"], code)
            self.assertEqual(spec["script"], old["script"], code)
            self.assertEqual(spec["human_spot_check"], bool(old.get("human_spot_check")), code)
            self.assertEqual(spec.get("min_coverage"), old.get("min_coverage"), code)
            self.assertEqual(bool(spec.get("source")), bool(old.get("source")), code)
            self.assertEqual(spec["tag"], code)

    def test_de_manifest_is_unchanged(self):
        self.reviewed("de", "pos.total", "Summe")
        _, rep = self.build("de")
        _, manifest, _ = self.members(rep)
        self.assertEqual(manifest["name"], "German (Deutsch)")
        self.assertEqual(manifest["description"], "German (Deutsch) language bundle for Universal Till, built from "
                         "approved translations; strings not approved yet show in English.")

    def test_review_gate_is_the_same_for_a_derived_language(self):
        self.add_language("fr", "French")
        for key, tgt, hist, area in (
            ("pos.total", "Somme", [(NEW, "alice", "Somme"), (APPROVE, "alice", "Somme")], "sale"),          # self
            ("pos.short", "Payer", [(NEW, "ai-tr-opus", "Payer"), (APPROVE, "ai-rv-opus", "Payer")], "sale"),  # same model
            ("receipt.vat", "TVA incl.", [(NEW, "ai-tr-opus", "TVA incl."), (APPROVE, "ai-rv-qwen", "TVA incl.")],
             "receipts-and-legal"),                                                                          # legal, no human
            ("receipt.thanks", "Merci", [(NEW, "alice", "Merci"), (APPROVE, "carol", "Merci")], "receipts-and-legal"),
            ("pos.name", "Bonjour {name}", [(NEW, "alice", "Bonjour {name}"), (APPROVE, "carol", "Bonjour {name}")], "sale"),
        ):
            self.fake.add_unit(area, "fr", key, self.en[key], tgt, history=hist)
        b, rep = self.build("fr")
        self.assertEqual({k: v.split(":")[0] for k, v in rep["omitted"].items()},
                         {"pos.total": "self_approved", "pos.short": "same_model", "receipt.vat": "legal_needs_human",
                          "receipt.thanks": "legal_needs_human", "pos.name": "unknown_approver"})
        self.assertEqual(b.strings, {})

    def test_legal_string_with_a_human_approval_ships_for_a_derived_language(self):
        self.add_language("fr", "French")
        self.fake.add_unit("receipts-and-legal", "fr", "receipt.vat", self.en["receipt.vat"], "TVA incluse",
                           history=[(NEW, "alice", "TVA incluse"), (APPROVE, "bob", "TVA incluse")])
        b, _ = self.build("fr")
        self.assertEqual(b.strings, {"receipt.vat": "TVA incluse"})

    def test_min_coverage_only_where_overridden(self):
        self.add_language("fr", "French")
        self.reviewed("fr", "pos.total", "Total")
        _, rep = self.build("fr")
        self.assertTrue(rep["publishable"], rep["problems"])  # 1 of many keys, no floor
        self.reviewed("pt", "pos.total", "Total")
        _, rep = self.build("pt")
        self.assertFalse(rep["publishable"])
        self.assertIn("min_coverage", rep["problems"][0])

    def test_empty_derived_bundle_is_not_publishable(self):
        self.add_language("fr", "French")
        self.fake.units[("sale", "fr")] = []
        _, rep = self.build("fr")
        self.assertEqual(rep["bundle_json"]["approved_count"], 0)
        self.assertFalse(rep["publishable"])
        self.assertIn("empty", rep["problems"][0])

    def test_scan_bundle_needs_no_network(self):
        from unittest import mock
        self.add_language("pt_BR", "Portuguese (Brazil)")
        self.reviewed("pt_BR", "pos.total", "Total")
        _, rep = self.build("pt_BR")
        bundle = os.path.join(self.out, rep["bundle"])
        with mock.patch("socket.socket.connect", side_effect=AssertionError("network used")):
            bb.scan_bundle(self.repo, self.till, bundle, "pt_BR")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bb.main(["--locale", "pt_BR", "--repo", self.repo, "--till-source", self.till,
                                          "--scan-bundle", bundle]), 0)
        with self.assertRaisesRegex(bb.BundleError, "language-pt-br"):
            bb.scan_bundle(self.repo, self.till, bundle, "pt")

    def test_language_list_failures_build_nothing(self):
        for label, setup in (("404", lambda: setattr(self.fake, "languages", None)),
                             ("500", lambda: setattr(self.fake, "languages_status", 500)),
                             ("empty", lambda: setattr(self.fake, "languages", [])),
                             ("malformed", lambda: setattr(self.fake, "languages", [{"name": "x"}]))):
            self.fake.languages, self.fake.languages_status = language_rows(), 200
            setup()
            with self.assertRaises(bb.BundleError, msg=label):
                self.build("de")
            self.assertFalse(os.path.exists(self.out), label)

    def test_code_missing_from_the_list_is_refused_even_with_an_override(self):
        self.fake.languages = [r for r in language_rows() if r["code"] != "de"]
        with self.assertRaisesRegex(bb.BundleError, "not a language"):
            self.build("de")
        with self.assertRaises(bb.BundleError):
            self.build("ja")
        self.assertFalse(os.path.exists(self.out))

    def test_two_live_codes_with_one_tag_are_refused(self):
        self.add_language("pt_BR", "a")
        self.add_language("pt_br", "b")
        with self.assertRaisesRegex(bb.BundleError, "both map"):
            self.build("pt_BR")

    def test_weblate_cannot_widen_the_gate(self):
        self.fake.languages.append({"code": "fr", "name": "French", "human_spot_check": False, "min_coverage": 0,
                                    "source": True})
        b, _ = self.build("fr")
        self.assertFalse(b.spec.get("source"))
        self.assertNotIn("min_coverage", b.spec)
        self.assertEqual(b.strings, {})  # not the English


class Checks(unittest.TestCase):
    def test_till_verb_rule_is_mirrored(self):
        # internal/config/i18n.go samePlaceholders: multiset, %% stripped,
        # explicit indexes are part of the verb.
        self.assertIsNone(bc.check_string("%s of %d", "%d von %s", {}, "Latn"))
        self.assertIsNotNone(bc.check_string("%[1]s %[2]s", "%[2]s %[2]s", {}, "Latn"))
        self.assertIsNone(bc.check_string("19%% off", "19%% Rabatt", {}, "Latn"))
        self.assertIsNotNone(bc.check_string("Total", "100%ig", {}, "Latn"))

    def test_icu_argument_names_must_match(self):
        meta = {"flags": "icu-message-format"}
        en = "{count, plural, one {# item} other {# items}}"
        self.assertIsNone(bc.check_string(en, "{count, plural, one {# Artikel} other {# Artikel}}", meta, "Latn"))
        self.assertIsNotNone(bc.check_string(en, "{n, plural, one {# Artikel} other {# Artikel}}", meta, "Latn"))

    def test_script(self):
        self.assertIsNone(bc.check_script("Universal Till پرداخت", "Arab"))
        self.assertIsNotNone(bc.check_script("Оплата", "Latn"))
        self.assertIsNone(bc.check_script("123 %s", "Arab"))
        # Real key settings.printer.charset_win1253: the English names Greek letters.
        self.assertIsNone(bc.check_string("Greek (Windows-1253 — €/α/β)", "Griechisch (Windows-1253 — €/α/β)", {}, "Latn"))
        self.assertIsNotNone(bc.check_string("Greek", "Griechisch α", {}, "Latn"))

    def test_real_translations_that_must_pass(self):
        # Found by running the checks over every string of the de/es/pt packs
        # and the till's built-in tr/ar/fa (0 false positives after these).
        self.assertIsNone(bc.check_string("VAT no.", "Nº IVA", {}, "Latn"))           # º is no script
        self.assertIsNone(bc.check_string("EAN-13", "EAN-13", {}, "Arab"))            # kept as English
        self.assertIsNotNone(bc.check_string("Total", "Toplam", {}, "Arab"))          # Latin, not English
        self.assertIsNone(bc.check_string("Must start with http:// or https://, then a host",
                                          "Muss mit http:// oder https:// beginnen, dann ein Host", {}, "Latn"))
        self.assertIsNotNone(bc.check_string("See https://universaltill.com/help.", "Siehe Hilfe.", {}, "Latn"))
        self.assertIsNone(bc.check_string("See https://universaltill.com/help.",
                                          "Siehe https://universaltill.com/help!", {}, "Latn"))

    def test_scan_line_folds_what_hides_a_term(self):
        self.assertEqual(bc.scan_line("GoBD‑<b>kon</b>form"), "GoBD-konform")
        self.assertEqual(bc.scan_line("fully \n compliant"), "fully compliant")


@contextlib.contextmanager
def patched_env(env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
