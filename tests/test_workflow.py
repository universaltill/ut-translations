"""The publish workflow's security shape (ut-docs#4367), checked on the YAML
text (no YAML parser in the standard library): least privilege, pinned
actions, GitHub-hosted runners, secrets only where needed, no expression
interpolated into a shell script, a secret-free validate job first (strict
locale regex equal to tools/locales.py's), the human environment chosen by
the config overrides (no list in the workflow), CODEOWNERS on it."""

import json
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import locales  # noqa: E402

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

    def test_dispatch_only_and_locale_is_a_free_string(self):
        on = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", on, re.M), ["workflow_dispatch"])
        block = re.search(r"\n      locale:\n((?:        .*\n)+)", on).group(1)
        self.assertIn("type: string", block)
        self.assertIn("required: true", block)
        self.assertNotIn("options", block)

    def test_no_locale_list_in_the_workflow(self):
        self.assertNotIn("fromJSON", self.text)
        self.assertNotRegex(self.text, r"\[\s*\"?en\"?\s*,\s*\"?de\"?")
        self.assertNotRegex(self.text, r"options: \[en")

    def test_validate_is_first_and_secret_free(self):
        self.assertEqual(list(self.jobs)[0], "validate")
        v = self.jobs["validate"]
        self.assertNotIn("secrets.", v)
        self.assertNotIn("environment:", v)
        self.assertNotIn("needs:", v)
        self.assertIn("github.ref == 'refs/heads/main'", v)
        self.assertEqual(re.search(r"\n    permissions:\n((?:      .*\n)+)", v).group(1).strip(), "contents: read")
        for name in ("build", "verify", "publish", "publish-human"):
            self.assertRegex(self.jobs[name], r"needs: (validate|\[validate,)", name)

    def test_regex_is_the_first_step_and_equals_the_python_one(self):
        steps = re.split(r"\n      - ", self.jobs["validate"])[1:]
        first = steps[0]
        self.assertNotIn("uses:", first)
        self.assertIn("LOCALE: ${{ inputs.locale }}", first)
        m = re.search(r"\[\[ \"\$LOCALE\" =~ (\S+) \]\]", first)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), locales.CODE_RE_TEXT)
        self.assertEqual(re.compile(m.group(1)).pattern, locales.CODE_RE.pattern)
        # nothing that holds a credential or reads the repo runs before it
        self.assertNotIn("secrets.", first)
        self.assertIn("actions/checkout@", steps[1])
        self.assertIn("tools/locales.py --check \"$LOCALE\" --live", steps[2])
        self.assertIn("vars.WEBLATE_URL", steps[2])

    def test_no_secret_step_before_validation(self):
        text = self.text
        self.assertLess(text.index("validate:"), text.index("secrets."))
        for name, job in self.jobs.items():
            if name != "validate":
                self.assertIn("validate", job.split("steps:")[0], name)

    def test_publish_routing_is_by_the_validated_flag_only(self):
        human, normal = self.jobs["publish-human"], self.jobs["publish"]
        self.assertIn("environment: bundle-publish-human\n", human)
        self.assertIn("environment: bundle-publish\n", normal)
        self.assertIn("needs.validate.outputs.human == 'true'", human)
        self.assertIn("needs.validate.outputs.human == 'false'", normal)
        self.assertNotIn("== 'false'", human)
        self.assertNotIn("== 'true'", normal)
        self.assertNotIn("contains(", self.text)
        self.assertNotIn("environment: ${{", self.text)

    def test_the_two_publish_jobs_differ_only_in_environment_and_condition(self):
        def norm(job):
            job = re.sub(r"^    if: .*$", "IF", job, flags=re.M)
            job = re.sub(r"^    environment: .*$", "ENV", job, flags=re.M)
            return job.strip()
        self.assertEqual(norm(self.jobs["publish-human"]), norm(self.jobs["publish"]))

    def test_human_locales_come_from_the_overrides(self):
        want = sorted(c for c, o in self.cfg["overrides"].items() if o.get("human_spot_check"))
        self.assertEqual(want, ["de", "en", "tr"])
        self.assertNotIn("locales", self.cfg)

    def test_artifact_names_use_the_validated_tag_only(self):
        names = re.findall(r"name: (bundle-.*)$", self.text, re.M)
        self.assertEqual(len(names), 4)  # upload + verify + two publish downloads
        for n in names:
            self.assertEqual(n, "bundle-${{ needs.validate.outputs.tag }}")
        self.assertEqual(self.text.count("inputs.locale"), 5)  # group + 4 env bindings
        self.assertNotIn("github.event.inputs", self.text)  # the same raw input under another name
        for block in run_blocks(self.text):
            self.assertNotIn("inputs.", block)

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
        build, verify = self.jobs["build"], self.jobs["verify"]
        self.assertEqual(re.findall(r"secrets\.(\w+)", build), ["WEBLATE_BUNDLE_TOKEN"])
        self.assertEqual(re.findall(r"secrets\.(\w+)", verify), [])
        for name in ("publish", "publish-human"):
            self.assertEqual(re.findall(r"secrets\.(\w+)", self.jobs[name]), ["MARKETPLACE_LANGUAGE_UPLOAD_TOKEN"])
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
        self.assertIn("needs: [validate, build]", verify)
        self.assertIn("tools/till_verify.py", verify)
        self.assertIn("--scan-bundle", verify)
        self.assertIn('--commit "$COMMIT"', verify)
        self.assertIn('--locale "$TAG"', verify)

    def test_artifact_is_uploaded_right_after_the_build(self):
        steps = self.steps("build")
        i = next(n for n, s in enumerate(steps) if "tools/build_bundle.py" in s)
        self.assertIn("actions/upload-artifact@", steps[i + 1])
        self.assertEqual([s for s in steps[i + 1:] if "run:" in s and "review.md" not in s], [])

    def test_hand_off_is_pinned_by_sha256(self):
        outs = re.search(r"\n    outputs:\n((?:      .*\n)+)", self.jobs["build"]).group(1)
        self.assertEqual(sorted(re.findall(r"^      (\w+):", outs, re.M)), ["bundle", "report_sha256", "sha256", "version"])
        for job in ("verify", "publish", "publish-human"):
            self.assertIn("needs.build.outputs.sha256", self.jobs[job], job)
            self.assertIn("needs.build.outputs.report_sha256", self.jobs[job], job)
        for job in ("publish", "publish-human"):
            self.assertIn('--report-sha256 "$REPORT_SHA256"', self.jobs[job])
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
        for job in ("publish", "publish-human"):
            self.assertIn("needs: [validate, build, verify]", self.jobs[job])
            self.assertIn("if: inputs.publish &&", self.jobs[job])
        self.assertIn("--require-publishable", self.jobs["build"])

    def test_codeowners_cover_the_publish_path(self):
        owners = read(os.path.join(ROOT, ".github", "CODEOWNERS"))
        for path in ("/.github/workflows/publish-bundle.yml", "/tools/build_bundle.py", "/tools/bundle_checks.py",
                     "/tools/publish_bundle.py", "/tools/till_verify.py", "/tools/locales.py", "/tests/test_locales.py",
                     "/tests/test_workflow.py", "/tests/test_bundle.py", "/tools/till_harness/",
                     "/config/ai-accounts.json", "/config/bundles.json", "/.github/CODEOWNERS",
                     # Review finding 2(b): what decides the English, its
                     # labels and the source commit, and the job that writes them.
                     "/meta/", "/*/en.json", "/config/", "/tools/sync_english.py",
                     "/.github/workflows/sync-english.yml"):
            self.assertRegex(owners, rf"(?m)^{re.escape(path)} @\w+")


if __name__ == "__main__":
    unittest.main()
