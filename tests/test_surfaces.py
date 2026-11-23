"""Surfaces (ADR-0177 §1; ut-docs#5405): config/surfaces.json declares the
non-till English sources (website, ut-cloud). `sync_english.py --surface`
copies one surface's English + metadata into <component>/en.json and
meta/<component>.json; `provision_weblate.py` creates its component. The till
path is covered by test_sync.py / test_provision.py and must not change."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sync_english as s  # noqa: E402
import surfaces as sf  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def init_repo(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "commit.gpgsign", "false")


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def spec(**kw):
    base = {"repo": "https://example.invalid/x.git", "english": "i18n/en.json",
            "metadata": "i18n/en.meta.json", "component": "website", "name": "Website"}
    base.update(kw)
    return base


def cfg(surfaces):
    return {"schema": 1, "surfaces": surfaces}


class ShippedConfig(unittest.TestCase):
    """The real config/surfaces.json."""

    def test_declares_the_five_adr_0177_surfaces(self):
        got = sf.load(REPO)
        self.assertEqual(sorted(got), ["cloud-admin", "cloud-manage", "cloud-partner", "cloud-public", "website"])
        for name, sp in got.items():
            self.assertEqual(sp["component"], name)
            self.assertTrue(sp["repo"].startswith("https://github.com/universaltill/"))
        self.assertEqual(got["website"]["repo"], "https://github.com/universaltill/ut-website.git")
        self.assertEqual(got["website"]["english"], "i18n/en.json")
        self.assertEqual(got["website"]["metadata"], "i18n/en.meta.json")
        self.assertEqual({got[n]["repo"] for n in got if n.startswith("cloud-")},
                         {"https://github.com/universaltill/ut-cloud.git"})

    def test_cloud_surfaces_do_not_share_a_namespace(self):
        seen = {}
        for name, sp in sf.load(REPO).items():
            for ns in sp.get("namespaces", []):
                self.assertNotIn(ns, seen, f"{ns} is in {seen.get(ns)} and {name}")
                seen[ns] = name

    def test_no_component_is_a_till_area(self):
        with open(os.path.join(REPO, "meta/areas.json"), encoding="utf-8") as f:
            areas = json.load(f)
        self.assertFalse(set(sf.load(REPO)) & set(areas))


class Validation(unittest.TestCase):
    def check(self, surfaces, areas=()):
        return sf.validate(cfg(surfaces), till_areas=areas)

    def test_valid(self):
        self.assertEqual(self.check({"website": spec()})["website"]["component"], "website")

    def test_bad_schema(self):
        with self.assertRaises(sf.SurfaceError):
            sf.validate({"schema": 2, "surfaces": {}}, till_areas=())

    def test_missing_field(self):
        for field in ("repo", "english", "metadata", "component", "name"):
            bad = spec()
            del bad[field]
            with self.assertRaises(sf.SurfaceError, msg=field):
                self.check({"website": bad})

    def test_component_must_be_a_safe_slug(self):
        for bad in ("../x", "Web", "a b", "meta", "tools", "config"):
            with self.assertRaises(sf.SurfaceError, msg=bad):
                self.check({"website": spec(component=bad)})

    def test_paths_must_stay_inside_the_repo(self):
        for bad in ("/etc/passwd", "../x.json", "a/../../x.json", ""):
            with self.assertRaises(sf.SurfaceError, msg=bad):
                self.check({"website": spec(english=bad)})
            with self.assertRaises(sf.SurfaceError, msg=bad):
                self.check({"website": spec(metadata=bad)})

    def test_repo_must_be_https_or_local_path_never_option_like(self):
        with self.assertRaises(sf.SurfaceError):
            self.check({"website": spec(repo="--upload-pack=x")})

    def test_component_clashing_with_a_till_area_is_refused(self):
        with self.assertRaises(sf.SurfaceError):
            self.check({"website": spec(component="sale")}, areas=["sale"])

    def test_duplicate_component_is_refused(self):
        with self.assertRaises(sf.SurfaceError):
            self.check({"a": spec(component="same"), "b": spec(component="same")})

    def test_namespaces_must_be_strings(self):
        with self.assertRaises(sf.SurfaceError):
            self.check({"website": spec(namespaces=[1])})


class Hardening(unittest.TestCase):
    def test_surface_name_is_validated(self):
        with self.assertRaises(sf.SurfaceError):
            sf.validate(cfg({"../../tmp/x": spec()}), till_areas=())

    def test_branch_is_validated(self):
        for bad in ("main:refs/heads/evil", "-x", "a b"):
            with self.assertRaises(sf.SurfaceError, msg=bad):
                sf.validate(cfg({"website": spec(branch=bad)}), till_areas=())

    def test_published_is_reserved(self):
        with self.assertRaises(sf.SurfaceError):
            sf.validate(cfg({"website": spec(component="published")}), till_areas=())

    def test_catch_all_excludes_namespaces(self):
        with self.assertRaises(sf.SurfaceError):
            sf.validate(cfg({"website": spec(catch_all=True, namespaces=["a"])}), till_areas=())

    def test_shipped_cloud_namespaces_are_the_real_ut_cloud_ones(self):
        got = sf.load(REPO)
        self.assertEqual(got["cloud-manage"]["namespaces"], ["manage_app"])
        self.assertEqual(got["cloud-admin"]["namespaces"], ["admin_app"])
        self.assertEqual(got["cloud-partner"]["namespaces"], ["partner_app"])
        self.assertTrue(got["cloud-public"]["catch_all"])


class SurfaceSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "ut-cloud")
        self.tr = os.path.join(self.tmp.name, "ut-translations")
        init_repo(self.src)
        write_json(os.path.join(self.src, "i18n/en.json"),
                   {"manage.title": "My shop", "manage.greet": "Hi %s", "admin.title": "Admin", "public.hero": "Welcome"})
        write_json(os.path.join(self.src, "i18n/en.meta.json"), {
            "manage.title": {"description": "Page title.", "review": "standard", "core": True},
            "manage.greet": {"description": "Greeting.", "placeholders": ["the shop name"], "max_length": 30},
            "admin.title": {"description": "Admin title."},
        })
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "en")
        init_repo(self.tr)
        write_json(os.path.join(self.tr, "config/surfaces.json"), cfg({
            "cloud-manage": spec(repo=self.src, component="cloud-manage", name="cloud-manage", namespaces=["manage"]),
            "cloud-admin": spec(repo=self.src, component="cloud-admin", name="cloud-admin", namespaces=["admin"]),
        }))
        write_json(os.path.join(self.tr, "meta/areas.json"), {"sale": {"name": "Sale", "description": "", "keys": 0}})
        git(self.tr, "add", "-A")
        git(self.tr, "commit", "-q", "-m", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, name="cloud-manage", commit=True):
        return s.sync_surface(self.tr, name, commit=commit, log=lambda *_: None)

    def read(self, rel):
        with open(os.path.join(self.tr, rel), encoding="utf-8") as f:
            return json.load(f)

    def test_writes_only_that_surfaces_namespaces(self):
        self.assertTrue(self.run_sync())
        self.assertEqual(self.read("cloud-manage/en.json"), {"manage.greet": "Hi %s", "manage.title": "My shop"})
        self.assertFalse(os.path.exists(os.path.join(self.tr, "cloud-admin")))

    def test_metadata_mapping_and_core_flag(self):
        self.run_sync()
        meta = self.read("meta/cloud-manage.json")
        self.assertEqual(meta["manage.greet"]["flags"], 'max-length:30, placeholders:"%s"')
        self.assertIn("- %s: the shop name", meta["manage.greet"]["explanation"])
        self.assertEqual(meta["manage.greet"]["labels"], [])
        self.assertTrue(meta["manage.title"]["core"])
        self.assertNotIn("core", meta["manage.greet"])
        self.assertEqual(meta["manage.title"]["lines"], ["main"])

    def test_review_legal_gets_the_legal_label(self):
        write_json(os.path.join(self.src, "i18n/en.meta.json"),
                   {"manage.title": {"description": "T.", "review": "legal"}})
        git(self.src, "commit", "-qam", "legal")
        self.run_sync()
        self.assertEqual(self.read("meta/cloud-manage.json")["manage.title"]["labels"], ["legal"])

    def test_commits_and_records_the_source_commit(self):
        before = git(self.tr, "rev-parse", "HEAD")
        self.run_sync()
        self.assertNotEqual(git(self.tr, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.tr, "status", "--porcelain"), "")
        src = self.read("meta/source-cloud-manage.json")
        self.assertEqual(src["commit"], git(self.src, "rev-parse", "HEAD"))
        self.assertIn("cloud-manage", git(self.tr, "log", "-1", "--format=%s"))

    def test_second_run_is_a_no_op(self):
        self.run_sync()
        head = git(self.tr, "rev-parse", "HEAD")
        self.assertFalse(self.run_sync())
        self.assertEqual(git(self.tr, "rev-parse", "HEAD"), head)

    def test_source_commit_without_english_change_is_a_no_op(self):
        self.run_sync()
        head = git(self.tr, "rev-parse", "HEAD")
        git(self.src, "commit", "-q", "--allow-empty", "-m", "unrelated")
        self.assertFalse(self.run_sync())
        self.assertEqual(git(self.tr, "rev-parse", "HEAD"), head)

    def test_translations_are_left_alone(self):
        self.run_sync()
        write_json(os.path.join(self.tr, "cloud-manage/de.json"), {"manage.title": "Mein Shop"})
        git(self.tr, "add", "-A")
        git(self.tr, "commit", "-q", "-m", "Translated using Weblate (German)")
        write_json(os.path.join(self.src, "i18n/en.json"), {"manage.title": "My shop!", "admin.title": "A"})
        git(self.src, "commit", "-qam", "edit")
        self.assertTrue(self.run_sync())
        self.assertEqual(self.read("cloud-manage/de.json"), {"manage.title": "Mein Shop"})
        self.assertEqual(self.read("cloud-manage/en.json"), {"manage.title": "My shop!"})

    def test_missing_english_file_is_an_error(self):
        git(self.src, "rm", "-q", "i18n/en.json")
        git(self.src, "commit", "-qm", "rm")
        with self.assertRaises(s.SyncError) as cm:
            self.run_sync()
        self.assertIn("i18n/en.json", str(cm.exception))
        self.assertEqual(git(self.tr, "status", "--porcelain"), "")

    def test_missing_metadata_is_allowed_but_keys_get_no_description(self):
        git(self.src, "rm", "-q", "i18n/en.meta.json")
        git(self.src, "commit", "-qm", "rm")
        self.run_sync()
        self.assertEqual(self.read("meta/cloud-manage.json")["manage.title"]["explanation"], "")

    def test_catch_all_takes_what_siblings_do_not_claim(self):
        d = json.load(open(os.path.join(self.tr, "config/surfaces.json")))
        d["surfaces"]["cloud-public"] = spec(repo=self.src, component="cloud-public", name="cloud-public", catch_all=True)
        write_json(os.path.join(self.tr, "config/surfaces.json"), d)
        self.assertTrue(self.run_sync("cloud-public"))
        self.assertEqual(self.read("cloud-public/en.json"), {"public.hero": "Welcome"})

    def test_filter_matching_nothing_is_an_error(self):
        d = json.load(open(os.path.join(self.tr, "config/surfaces.json")))
        d["surfaces"]["cloud-manage"]["namespaces"] = ["nope"]
        write_json(os.path.join(self.tr, "config/surfaces.json"), d)
        with self.assertRaises(s.SyncError):
            self.run_sync()
        self.assertFalse(os.path.exists(os.path.join(self.tr, "cloud-manage")))

    def test_unknown_surface_is_an_error(self):
        with self.assertRaises(s.SyncError):
            self.run_sync("nope")

    def test_component_clashing_with_an_area_is_an_error(self):
        write_json(os.path.join(self.tr, "meta/areas.json"), {"cloud-manage": {"name": "x", "description": "", "keys": 0}})
        with self.assertRaises(s.SyncError):
            self.run_sync()

    def test_never_writes_to_the_source_repo(self):
        refs = git(self.src, "for-each-ref")
        self.run_sync()
        self.assertEqual(git(self.src, "for-each-ref"), refs)
        self.assertEqual(git(self.src, "status", "--porcelain"), "")

    def test_no_commit_mode(self):
        head = git(self.tr, "rev-parse", "HEAD")
        self.assertTrue(self.run_sync(commit=False))
        self.assertEqual(git(self.tr, "rev-parse", "HEAD"), head)
        self.assertNotEqual(git(self.tr, "status", "--porcelain"), "")


class TillSyncUnchanged(unittest.TestCase):
    def test_surface_folders_are_not_reported_as_removed_areas(self):
        with tempfile.TemporaryDirectory() as tmp:
            till = os.path.join(tmp, "till")
            tr = os.path.join(tmp, "tr")
            init_repo(till)
            write_json(os.path.join(till, "web/locales/en.json"), {"pos.pay": "Pay"})
            write_json(os.path.join(till, "web/i18n-meta/en.meta.json"), {})
            write_json(os.path.join(till, "web/i18n-meta/areas.json"),
                       {"default_area": "common", "areas": {"sale": {"namespaces": ["pos"]}, "common": {"namespaces": []}}})
            git(till, "add", "-A")
            git(till, "commit", "-q", "-m", "x")
            git(till, "tag", "v0.1.0")
            init_repo(tr)
            write_json(os.path.join(tr, "config/lines.json"), {"source": till, "supported_lines": ["0.1"]})
            write_json(os.path.join(tr, "config/surfaces.json"), cfg({"website": spec()}))
            write_json(os.path.join(tr, "website/en.json"), {"a": "b"})
            git(tr, "add", "-A")
            git(tr, "commit", "-q", "-m", "init")
            logs = []
            s.sync(tr, s.load_config(tr), commit=False, log=logs.append)
            self.assertFalse([m for m in logs if "no longer an area" in m], logs)

    def test_main_surface_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "web")
            tr = os.path.join(tmp, "tr")
            init_repo(src)
            write_json(os.path.join(src, "i18n/en.json"), {"nav.home": "Home"})
            git(src, "add", "-A")
            git(src, "commit", "-q", "-m", "x")
            init_repo(tr)
            write_json(os.path.join(tr, "config/surfaces.json"), cfg({"website": spec(repo=src)}))
            git(tr, "add", "-A")
            git(tr, "commit", "-q", "-m", "init")
            self.assertEqual(s.main(["--repo", tr, "--surface", "website", "--no-commit"]), 0)
            with open(os.path.join(tr, "website/en.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"nav.home": "Home"})
            self.assertEqual(s.main(["--repo", tr, "--surface", "nope", "--no-commit"]), 1)


class ProvisionSurfaces(unittest.TestCase):
    """provision_weblate.py creates one component per surface that has been
    synced; a fake Weblate on 127.0.0.1 - nothing talks to the live instance."""

    def setUp(self):
        import contextlib  # noqa: F401
        from test_provision import FakeWeblate, serve, write
        self.write = write
        self.tmp = tempfile.TemporaryDirectory()
        r = self.repo = self.tmp.name
        write(f"{r}/meta/areas.json", {"common": {"name": "Common", "description": "Shared.", "keys": 1}})
        write(f"{r}/common/en.json", {"common.ok": "OK"})
        write(f"{r}/meta/common.json", {"common.ok": {"explanation": "", "flags": "", "labels": [], "lines": ["main"]}})
        write(f"{r}/config/surfaces.json", cfg({
            "website": spec(),
            "cloud-manage": spec(component="cloud-manage", name="Cloud manage", namespaces=["manage"]),
        }))
        write(f"{r}/website/en.json", {"nav.home": "Home", "nav.hi": "Hi %s"})
        write(f"{r}/meta/website.json", {
            "nav.home": {"explanation": "Home link.", "flags": "", "labels": [], "lines": ["main"], "core": True},
            "nav.hi": {"explanation": "", "flags": 'placeholders:"%s"', "labels": ["legal"], "lines": ["main"]},
        })
        self.fake = FakeWeblate(r)
        self.srv = serve(self.fake)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def run_it(self, dry_run=False):
        import contextlib
        import io
        import provision_weblate as p
        client = p.WeblateClient(self.fake.base(), "wlu_test_token")
        with contextlib.redirect_stdout(io.StringIO()):
            return p.provision(client, self.repo, project="universal-till",
                               repo_url="https://github.com/universaltill/ut-translations.git",
                               push_url="git@github.com:universaltill/ut-translations.git", dry_run=dry_run)

    def comp(self, slug):
        return self.fake.components[("universal-till", slug)]

    def test_synced_surface_gets_a_component_like_an_area(self):
        self.run_it()
        c = self.comp("website")
        self.assertEqual(c["filemask"], "website/*.json")
        self.assertEqual(c["template"], "website/en.json")
        self.assertEqual(c["file_format"], "json")
        self.assertEqual(c["name"], "Website")
        self.assertEqual(c["source_language"]["code"], "en")
        self.assertEqual(sorted(c["enforced_checks"]), ["icu_message_format", "max-length", "placeholders"])
        # a linked component: the clone is owned by `common`
        self.assertTrue(c["repo"].startswith("weblate://universal-till/common")
                        or (c.get("linked_component") or "").rstrip("/").endswith("/api/components/universal-till/common"),
                        c)
        units = {u["context"]: u for u in self.fake.units[("universal-till", "website")]}
        self.assertEqual(units["nav.home"]["explanation"], "Home link.")
        self.assertEqual(units["nav.hi"]["extra_flags"], 'placeholders:"%s"')

    def test_surface_not_synced_yet_is_skipped_not_created(self):
        report = self.run_it()
        self.assertNotIn(("universal-till", "cloud-manage"), self.fake.components)
        self.assertTrue(any("skip cloud-manage" in a for a in report.actions), report.actions)

    def test_second_run_writes_nothing(self):
        self.run_it()
        self.fake.writes.clear()
        self.run_it()
        self.assertEqual(self.fake.writes, [])

    def test_dry_run_plans_the_surface_and_writes_nothing(self):
        report = self.run_it(dry_run=True)
        self.assertEqual(self.fake.writes, [])
        self.assertTrue(any("create component website" in a for a in report.actions))

    def test_till_provisioning_is_unchanged_without_surfaces_file(self):
        os.remove(os.path.join(self.repo, "config/surfaces.json"))
        report = self.run_it()
        self.assertEqual(list(self.fake.components), [("universal-till", "common")])
        self.assertFalse(any("surface" in a for a in report.actions))


if __name__ == "__main__":
    unittest.main()
