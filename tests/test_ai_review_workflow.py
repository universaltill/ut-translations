"""The AI review workflow's security shape (ut-docs#5420), checked on the
YAML text like tests/test_workflow.py: dispatch only, main only,
GitHub-hosted, least privilege, pinned actions, each secret in the env of
the one step that needs it, no universal-till code in a job holding a
secret, no expression inside a shell script, dry run unless `write`."""

import os
import re
import unittest

from test_workflow import ROOT, jobs, read, run_blocks

WF = os.path.join(ROOT, ".github", "workflows", "ai-review.yml")


class AiReviewWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = read(WF)
        self.jobs = jobs(self.text)

    def steps(self, job):
        return re.split(r"\n      - ", self.jobs[job])[1:]

    def test_jobs(self):
        self.assertEqual(list(self.jobs), ["export", "scan", "review", "apply"])
        self.assertIn("needs: export", self.jobs["scan"])
        self.assertIn("needs: scan", self.jobs["review"])
        self.assertIn("needs: review", self.jobs["apply"])

    def test_dispatch_only_with_the_review_languages(self):
        on = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", on, re.M), ["workflow_dispatch"])
        self.assertEqual(sorted(re.findall(r"^      ([a-z_]+):", on, re.M)),
                         ["component", "include_reviewed_by", "lang", "limit", "write"])
        opts = re.search(r"lang:\n(?:\s+.*\n)*?\s+options: \[([^\]]*)\]", on).group(1)
        self.assertTrue({"fr", "ms", "zh_Hans", "el"} <= {o.strip() for o in opts.split(",")})
        self.assertRegex(on, r"write:\n(?:\s+.*\n)*?\s+default: false")

    def test_least_privilege(self):
        self.assertRegex(self.text, r"\npermissions: \{\}\n")
        for name, job in self.jobs.items():
            perms = re.search(r"\n    permissions:\n((?:      .*\n)+)", job)
            self.assertIsNotNone(perms, name)
            self.assertEqual(perms.group(1).strip(), "contents: read", name)

    def test_main_only_and_github_hosted(self):
        for name, job in self.jobs.items():
            self.assertIn("runs-on: ubuntu-latest", job, name)
            self.assertIn("github.ref == 'refs/heads/main'", job, name)
            self.assertIn("timeout-minutes:", job, name)
        self.assertNotIn("self-hosted", self.text)
        self.assertNotIn("homelab", self.text)

    def test_actions_pinned_and_checkouts_keep_no_credential(self):
        uses = re.findall(r"uses: (\S+)", self.text)
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
        self.assertEqual(self.text.count("persist-credentials: false"), self.text.count("actions/checkout@"))

    def test_each_secret_only_in_the_one_step_that_needs_it(self):
        want = {"export": ["WEBLATE_REVIEWER_TOKEN"], "scan": [], "review": ["ANTHROPIC_API_KEY"],
                "apply": ["WEBLATE_REVIEWER_TOKEN"]}
        for job, secrets in want.items():
            self.assertEqual(re.findall(r"secrets\.(\w+)", self.jobs[job]), secrets, job)
            holding = [s for s in self.steps(job) if "secrets." in s]
            self.assertEqual(len(holding), len(secrets), job)
            for s in holding:
                self.assertRegex(s, r"\n        env:\n", job)
                self.assertIn("tools/review_runner.py", s, job)
        self.assertNotIn("environment:", self.jobs["scan"])
        for job in ("export", "review", "apply"):
            self.assertIn("environment: ai-review", self.jobs[job], job)
        self.assertEqual(re.findall(r"secrets\.(\w+)", self.text.split("\njobs:\n")[0]), [])

    def test_no_till_code_in_a_job_holding_a_secret(self):
        for job in ("export", "review", "apply"):
            self.assertNotIn("universaltill/universal-till", self.jobs[job], job)
            self.assertNotIn("--till-source", self.jobs[job], job)
        self.assertIn("--no-scans", self.jobs["export"])
        scan = self.jobs["scan"]
        self.assertIn("repository: universaltill/universal-till", scan)
        self.assertIn('merge-base --is-ancestor "$commit" refs/remotes/origin/main', scan)
        self.assertIn("review_runner.py scan", scan)

    def test_no_expression_inside_a_shell_script(self):
        for block in run_blocks(self.text):
            self.assertNotIn("${{", block, block)

    def test_dry_run_unless_write(self):
        apply = self.jobs["apply"]
        self.assertEqual(self.text.count("--write"), 1)
        self.assertIn('if [ "$WRITE" = "true" ]; then args+=(--write); fi', apply)
        self.assertIn("WRITE: ${{ inputs.write }}", apply)

    def test_audit_always_uploaded(self):
        apply = self.jobs["apply"]
        self.assertIn("!cancelled()", apply)
        self.assertIn("needs.review.result == 'failure'", apply)
        last = self.steps("apply")[-1]
        self.assertIn("actions/upload-artifact@", last)
        self.assertIn("if: always()", last)
        self.assertIn("retention-days: 30", last)


if __name__ == "__main__":
    unittest.main()
