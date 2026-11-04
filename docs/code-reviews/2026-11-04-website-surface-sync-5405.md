# Review: sync the website surface's English (ut-docs#5405)

Shipped: `sync-english.yml` also runs `sync_english.py --surface website`; first
output committed (`website/en.json`, `meta/website.json`, `meta/source-website.json`,
483 keys, all with an explanation); `tests/test_sync_workflow.py`.

Reviewer: independent Opus subagent (code by Sonnet). Findings:
- Low: a website step failure would block the till push -> fixed (`continue-on-error`).
- Nit: header comment omitted the second public read -> fixed.
- Nit: `upload_translations.load_meta` loads `meta/source-*.json` as a pseudo-component
  (harmless, nothing looks it up) -> deferred.

Verified: 74 tests of sync/surfaces/provision pass; a re-run of the surface sync is a no-op.
Safe to merge. Provisioning the Weblate component follows once this is on main
(Weblate reads website/en.json from main).
