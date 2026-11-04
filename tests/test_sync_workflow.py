"""sync-english.yml also syncs the website surface (ADR-0177; ut-docs#5405)."""
import os
import unittest

WF = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "sync-english.yml")


class SyncWorkflowSurfaces(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as f:
            self.text = f.read()

    def test_website_surface_is_synced(self):
        self.assertIn("tools/sync_english.py --surface website", self.text)

    def test_push_runs_when_either_sync_changed(self):
        self.assertIn("steps.sync_website.outputs.changed == 'true'", self.text)
        self.assertIn("steps.sync.outputs.changed == 'true'", self.text)

    def test_still_secret_free(self):
        code = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("secrets.", code)


if __name__ == "__main__":
    unittest.main()
