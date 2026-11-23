"""The real config/ai-accounts.json keeps the accounts the review gate needs
(ADR-0160 §4, ut-docs#4366, #4371). Dropping the bootstrap approver would turn
every string it approved (~3,530 per language) into an unknown-account
approval, which the bundle export omits."""

import json
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PATH = os.environ.get("UTT_AI_ACCOUNTS") or os.path.join(ROOT, "config", "ai-accounts.json")


def accounts():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)["accounts"]


class AIAccountsConfig(unittest.TestCase):
    def test_bootstrap_approver_stays_registered(self):
        a = accounts().get("bot-universal-till-bootstrap-approver")
        self.assertIsNotNone(a, "the bootstrap approver was dropped: its approvals become unknown-account")
        self.assertEqual(a.get("model"), "owner-bootstrap")
        self.assertEqual(a.get("role"), "reviewer")

    def test_codex_reviewer_registered(self):
        a = accounts().get("bot-universal-till-ai-reviewer-codex")
        self.assertIsNotNone(a)
        self.assertEqual(a.get("role"), "reviewer")

    def test_every_account_names_a_model_and_a_known_role(self):
        for name, a in accounts().items():
            self.assertTrue(a.get("model"), name)
            self.assertIn(a.get("role"), ("translator", "reviewer"), name)


if __name__ == "__main__":
    unittest.main()
