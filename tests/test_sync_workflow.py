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

    def step(self, name):
        """The text of the step called `name` (up to the next step)."""
        m = re.search(rf"- name: {re.escape(name)}[^\n]*\n(.*?)(?=\n      - |\Z)", self.job, re.S)
        self.assertTrue(m, name)
        return m.group(1)

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

    def test_exactly_one_push_and_it_targets_the_bot_branch(self):
        pushes = re.findall(r"(?m)^\s*git push\b[^\n]*", self.runs)
        self.assertEqual(len(pushes), 1, pushes)
        self.assertRegex(pushes[0], r"HEAD:refs/heads/sync/english\s*$")
        self.assertEqual(len(re.findall(r"\bgit push\b", self.runs)), 1)

    def test_gate_runs_before_the_push_and_the_merge(self):
        i_gate = self.runs.index("--check-paths")
        self.assertLess(i_gate, self.runs.index("git push"))
        self.assertLess(i_gate, self.runs.index("gh pr merge"))
        self.assertIn("--areas-file", self.runs)
        # known areas come from main, never from the changed tree
        self.assertIn("git show origin/main:meta/areas.json", self.runs)

    def test_gate_reads_modes_nul_framed_without_rename_detection(self):
        # mode-aware (typechange/symlink/exec bit), NUL framing, and a rename
        # can't hide a path behind its new name.
        gate = self.step("Path gate")
        self.assertIn("git diff --raw -z --no-renames", gate)
        self.assertIn("--check-paths --raw", gate)

    def test_gate_diff_base_is_the_checked_out_tip_not_a_refetch(self):
        gate = self.step("Path gate")
        self.assertIn("HEAD^", gate)
        self.assertNotIn("git fetch", gate)
        # the sync is exactly one commit on top of the checked-out main
        self.assertIn('[ "$(git rev-parse HEAD^)" = "$(git rev-parse origin/main)" ]', gate)
        self.assertIn('git diff --raw -z --no-renames HEAD^ HEAD', gate)

    def test_code_dirs_must_be_identical_to_main_before_the_gate(self):
        i = self.runs.index("git diff --quiet origin/main HEAD -- tools .github scripts config")
        self.assertRegex(self.runs[i:i + 120], r"\|\| ")
        self.assertLess(i, self.runs.index("--check-paths"))

    def test_the_gate_cannot_be_bypassed(self):
        gate = self.step("Path gate")
        self.assertNotIn("continue-on-error", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s*if: always\(\)")
        self.assertNotRegex(self.text, r"(?m)^\s*if:[^\n]*(always|failure|cancelled)\(\)")
        # the refusal path exits non-zero and never 0
        self.assertNotRegex(self.runs, r"\bexit 0\b")
        self.assertIn("exit 1", gate)
        self.assertIn("set -euo pipefail", gate)
        # the PR step runs only when the sync changed something, behind the gate
        pr = self.step("Open the PR")
        self.assertEqual(self.text.count("if: steps.sync.outputs.changed == 'true'"), 2)
        self.assertNotIn("always()", pr)

    def test_auto_merge_and_ci_dispatch(self):
        self.assertIn("gh pr merge", self.runs)
        self.assertIn("--auto", self.runs)
        self.assertIn("gh workflow run test.yml --ref sync/english", self.runs)
        self.assertIn("gh workflow run commit-attribution.yml --ref sync/english", self.runs)

    def test_merge_commit_not_squash_and_pinned_to_the_gated_head(self):
        # repo rule #250: merge commits, never squash.
        merge = re.search(r"gh pr merge[^\n]*", self.runs).group(0)
        self.assertIn("--merge", merge)
        self.assertNotIn("--squash", self.text)
        self.assertNotIn("--rebase", self.text)
        self.assertIn('--match-head-commit "$(git rev-parse HEAD)"', merge)

    def test_refuses_to_enable_auto_merge_without_the_required_checks_ruleset(self):
        # `gh pr merge --auto` merges at once when main requires no check.
        i_rules = self.runs.index("rules/branches/main")
        self.assertIn('repos/$GITHUB_REPOSITORY/rules/branches/main', self.runs)
        self.assertIn("required_status_checks", self.runs)
        for ctx in ("unit", "authors", "paths"):
            self.assertRegex(self.runs, rf"['\"\[ ,]{ctx}['\"\], ]", ctx)
        self.assertLess(i_rules, self.runs.index("gh pr merge"))
        block = self.runs[i_rules:self.runs.index("gh pr merge")]
        self.assertIn("exit 1", block)

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
                  "/.github/CODEOWNERS", "/scripts/", "/.github/"):
            self.assertIn(p, rules)


class PathsCheckAndBotAllowance(unittest.TestCase):
    def test_test_workflow_has_a_required_paths_job_for_the_sync_branch(self):
        text = read(os.path.join(WFDIR, "test.yml"))
        job = jobs(text)["paths"]
        self.assertRegex(job, r"permissions:\n      contents: read")
        self.assertIn("runs-on: ubuntu-latest", job)
        self.assertIn("sync/english", job)
        self.assertIn("git merge-base origin/main HEAD", job)
        self.assertIn("git diff --raw -z --no-renames", job)
        self.assertIn("--check-paths --raw", job)
        self.assertIn("git show origin/main:meta/areas.json", job)
        # head ref comes through env, never an expression inside the script
        for block in run_blocks(job):
            self.assertNotIn("${{", block, block)
        self.assertRegex(job, r"HEAD_REF: \$\{\{ github\.head_ref \|\| github\.ref_name \}\}")
        self.assertNotIn("continue-on-error", job)
        # runs on every PR (a required check that is skipped never reports)
        self.assertNotRegex(job, r"(?m)^    if:")

    def test_attribution_workflow_allows_the_bot_only_on_dispatch(self):
        text = read(os.path.join(WFDIR, "commit-attribution.yml"))
        self.assertRegex(text, r"ALLOW_ACTIONS_BOT: \$\{\{ github\.event_name == 'workflow_dispatch' && 'true' \|\| 'false' \}\}")
        guard = read(os.path.join(ROOT, "scripts", "ci", "guard-commit-attribution.sh"))
        self.assertIn('"${GITHUB_EVENT_NAME:-}" = "workflow_dispatch"', guard)
        self.assertIn('"${ALLOW_ACTIONS_BOT:-}" = "true"', guard)


if __name__ == "__main__":
    unittest.main()
