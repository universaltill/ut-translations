"""The publish workflow's security shape (ut-docs#4367), checked on the YAML
text (no YAML parser in the standard library): least privilege, pinned
actions, GitHub-hosted runners, secrets only where needed, no expression
interpolated into a shell script, the locale list and the human
environment's locales equal to config/bundles.json, CODEOWNERS on it."""

import json
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "publish-bundle.yml")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def jobs(text):
    """{job name: its text}."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z][a-z0-9_-]*):\n", body, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def run_blocks(text):
    """Every `run:` script body."""
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:- )?run: ?(.*)$", line)
        if not m:
            continue
        indent = len(m.group(1))
        if m.group(2) not in ("|", ">-", ">", "|-"):
            out.append(m.group(2))
            continue
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.append(nxt)
        out.append("\n".join(block))
    return out


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = read(WF)
        self.cfg = json.loads(read(os.path.join(ROOT, "config", "bundles.json")))
        self.jobs = jobs(self.text)

    def test_dispatch_only_one_locale_from_the_config(self):
        on = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", on, re.M), ["workflow_dispatch"])
        m = re.search(r"locale:\n(?:\s+.*\n)*?\s+options: \[([^\]]*)\]", on)
        self.assertEqual(sorted(o.strip() for o in m.group(1).split(",")), sorted(self.cfg["locales"]))

    def test_human_environment_holds_exactly_the_spot_check_locales(self):
        m = re.search(r"contains\(fromJSON\('(\[[^']*\])'\), inputs\.locale\) && 'bundle-publish-human' \|\| 'bundle-publish'",
                      self.jobs["publish"])
        self.assertIsNotNone(m, "publish job must pick bundle-publish-human / bundle-publish by locale")
        want = sorted(c for c, s in self.cfg["locales"].items() if s.get("human_spot_check"))
        self.assertEqual(sorted(json.loads(m.group(1))), want)
        self.assertEqual(want, ["de", "en", "tr"])

    def test_least_privilege(self):
        self.assertRegex(self.text, r"\npermissions: \{\}\n")
        for name, job in self.jobs.items():
            perms = re.search(r"\n    permissions:\n((?:      .*\n)+)", job)
            self.assertIsNotNone(perms, f"{name} has no job-level permissions")
            self.assertEqual(perms.group(1).strip(), "contents: read", name)

    def test_main_only_and_github_hosted(self):
        for name, job in self.jobs.items():
            self.assertIn("runs-on: ubuntu-latest", job, name)
            self.assertIn("github.ref == 'refs/heads/main'", job, name)
            self.assertIn("timeout-minutes:", job, name)
        self.assertNotIn("self-hosted", self.text)
        self.assertNotIn("homelab", self.text)

    def test_actions_pinned_to_a_commit(self):
        uses = re.findall(r"uses: (\S+)", self.text)
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")

    def test_checkouts_keep_no_credential(self):
        n = self.text.count("actions/checkout@")
        self.assertEqual(self.text.count("persist-credentials: false"), n)

    def steps(self, job):
        return re.split(r"\n      - ", self.jobs[job])[1:]

    def test_secrets_only_where_needed(self):
        build, verify, publish = self.jobs["build"], self.jobs["verify"], self.jobs["publish"]
        self.assertEqual(re.findall(r"secrets\.(\w+)", build), ["WEBLATE_BUNDLE_TOKEN"])
        self.assertEqual(re.findall(r"secrets\.(\w+)", verify), [])
        self.assertEqual(re.findall(r"secrets\.(\w+)", publish), ["MARKETPLACE_LANGUAGE_UPLOAD_TOKEN"])
        self.assertIn("environment: bundle-build", build)
        self.assertNotIn("environment:", verify)
        holding = [s for s in self.steps("build") if "WEBLATE_BUNDLE_TOKEN" in s]
        self.assertEqual(len(holding), 1)
        self.assertIn("tools/build_bundle.py", holding[0])

    def test_no_till_code_runs_in_the_job_holding_the_weblate_token(self):
        # Review finding 2(c): the till verifier and universal-till's guards
        # (both its code) run in the verify job, which has no secret.
        build = self.jobs["build"]
        for banned in ("till_verify", "setup-go", "go test", "--scan-bundle"):
            self.assertNotIn(banned, build)
        step = [s for s in self.steps("build") if "tools/build_bundle.py" in s][0]
        self.assertIn("--no-scans", step)
        verify = self.jobs["verify"]
        self.assertIn("needs: build", verify)
        self.assertIn("tools/till_verify.py", verify)
        self.assertIn("--scan-bundle", verify)
        self.assertIn('--commit "$COMMIT"', verify)

    def test_artifact_is_uploaded_right_after_the_build(self):
        steps = self.steps("build")
        i = next(n for n, s in enumerate(steps) if "tools/build_bundle.py" in s)
        self.assertIn("actions/upload-artifact@", steps[i + 1])
        self.assertEqual([s for s in steps[i + 1:] if "run:" in s and "review.md" not in s], [])

    def test_hand_off_is_pinned_by_sha256(self):
        outs = re.search(r"\n    outputs:\n((?:      .*\n)+)", self.jobs["build"]).group(1)
        self.assertEqual(sorted(re.findall(r"^      (\w+):", outs, re.M)), ["bundle", "report_sha256", "sha256", "version"])
        for job in ("verify", "publish"):
            self.assertIn("needs.build.outputs.sha256", self.jobs[job], job)
            self.assertIn("needs.build.outputs.report_sha256", self.jobs[job], job)
        self.assertIn('--report-sha256 "$REPORT_SHA256"', self.jobs["publish"])
        self.assertIn("sha256sum -c", self.jobs["verify"])

    def test_till_checkouts_must_be_on_universal_till_main(self):
        # Review finding 2(a): a fork's commit is fetchable by sha.
        texts = {"publish-bundle/build": self.jobs["build"], "publish-bundle/verify": self.jobs["verify"],
                 "test": read(os.path.join(ROOT, ".github", "workflows", "test.yml"))}
        for name, text in texts.items():
            self.assertIn("repository: universaltill/universal-till", text, name)
            self.assertIn("fetch-depth: 0", text, name)
            self.assertIn("git -C till fetch", text, name)
            self.assertIn('merge-base --is-ancestor "$commit" refs/remotes/origin/main', text, name)
            self.assertIn("git -C till tag --points-at", text, name)

    def test_no_expression_inside_a_shell_script(self):
        for block in run_blocks(self.text):
            self.assertNotIn("${{", block, block)

    def test_publish_needs_the_build_the_verifier_and_the_tick(self):
        self.assertIn("needs: [build, verify]", self.jobs["publish"])
        self.assertIn("if: inputs.publish &&", self.jobs["publish"])
        self.assertIn("--require-publishable", self.jobs["build"])

    def test_codeowners_cover_the_publish_path(self):
        owners = read(os.path.join(ROOT, ".github", "CODEOWNERS"))
        for path in ("/.github/workflows/publish-bundle.yml", "/tools/build_bundle.py", "/tools/bundle_checks.py",
                     "/tools/publish_bundle.py", "/tools/till_verify.py", "/tools/till_harness/",
                     "/config/ai-accounts.json", "/config/bundles.json", "/.github/CODEOWNERS",
                     # Review finding 2(b): what decides the English, its
                     # labels and the source commit, and the job that writes them.
                     "/scripts/", "/config/", "/tools/sync_english.py",
                     "/.github/workflows/sync-english.yml"):
            self.assertRegex(owners, rf"(?m)^{re.escape(path)} @\w+")


if __name__ == "__main__":
    unittest.main()
