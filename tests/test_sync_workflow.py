"""The sync workflow's security shape (ut-docs#4367, Option A): it opens a
PR from a bot branch and enables auto-merge only after the path gate; it
never pushes to main; its token is the narrowest that can do that."""

import os
import re
import unittest

from test_workflow import ROOT, jobs, read, run_blocks

WFDIR = os.path.join(ROOT, ".github", "workflows")


class SyncWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = read(os.path.join(WFDIR, "sync-english.yml"))
        self.job = jobs(self.text)["sync"]
        self.runs = "\n".join(run_blocks(self.job))

    def test_permissions_are_job_level_and_minimal(self):
        self.assertRegex(self.text, r"\npermissions: \{\}\n")
        perms = re.search(r"\n    permissions:\n((?:      .*\n)+)", self.job).group(1)
        got = dict(l.strip().split(": ") for l in perms.strip().splitlines())
        # actions: write only to dispatch the CI on the bot branch (a
        # GITHUB_TOKEN push/PR starts no workflow; workflow_dispatch is the
        # one event it may start).
        self.assertEqual(got, {"contents": "write", "pull-requests": "write", "actions": "write"})

    def test_never_pushes_to_main(self):
        self.assertNotRegex(self.runs, r"HEAD:(refs/heads/)?main\b")
        self.assertNotRegex(self.runs, r"(?m)^\s*git push[^\n]*\bmain\b")
        self.assertIn("refs/heads/sync/english", self.runs)

    def test_gate_runs_before_the_push_and_the_merge(self):
        i_gate = self.runs.index("--check-paths")
        self.assertLess(i_gate, self.runs.index("git push"))
        self.assertLess(i_gate, self.runs.index("gh pr merge"))
        self.assertIn("--areas-file", self.runs)
        # known areas come from main, never from the changed tree
        self.assertIn("git show origin/main:meta/areas.json", self.runs)

    def test_auto_merge_and_ci_dispatch(self):
        self.assertIn("gh pr merge", self.runs)
        self.assertIn("--auto", self.runs)
        self.assertIn("gh workflow run test.yml --ref sync/english", self.runs)
        self.assertIn("gh workflow run commit-attribution.yml --ref sync/english", self.runs)

    def test_no_expression_in_scripts_no_secrets_hosted_runner(self):
        for block in run_blocks(self.job):
            self.assertNotIn("${{", block, block)
        self.assertNotIn("${{ secrets.", self.text)
        self.assertNotIn("pull_request_target", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.job)

    def test_checkout_does_not_persist_a_wider_credential(self):
        # the default GITHUB_TOKEN is the credential; no PAT, no deploy key
        self.assertNotRegex(self.text, r"(?i)\bpat\b|ssh-key|token: \$\{\{ (?!github\.token)")


class CiCanRunOnTheBotBranch(unittest.TestCase):
    def test_required_check_workflows_accept_dispatch(self):
        for name in ("test.yml", "commit-attribution.yml"):
            text = read(os.path.join(WFDIR, name))
            on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
            self.assertIn("workflow_dispatch:", on, name)
            self.assertIn("pull_request:", on, name)
            self.assertRegex(text, r"\npermissions:\n  contents: read\n", name)

    def test_authors_check_has_a_base_without_a_pr(self):
        text = read(os.path.join(WFDIR, "commit-attribution.yml"))
        self.assertIn("origin/main", text)


class CodeownersVsSyncPaths(unittest.TestCase):
    def test_sync_owned_data_paths_need_no_code_owner(self):
        owners = read(os.path.join(ROOT, ".github", "CODEOWNERS"))
        rules = [l.split()[0] for l in owners.splitlines() if l.strip() and not l.startswith("#")]
        # A code-owner review on these would stop the auto-merge ever
        # completing. The sync job and the path gate guard them instead.
        self.assertNotIn("/meta/", rules)
        self.assertNotIn("/*/en.json", rules)
        # ...while everything that can carry code stays owned.
        for p in ("/tools/sync_english.py", "/.github/workflows/sync-english.yml", "/config/",
                  "/.github/CODEOWNERS", "/scripts/"):
            self.assertIn(p, rules)


if __name__ == "__main__":
    unittest.main()
