# Rebase of PR #15 (publish-locales) onto main — ut-docs#5405

Retargeted from `feat/5405-surfaces` to `main` (#14 and #16 are merged).
Conflicts in `.github/CODEOWNERS` (kept refresh-bundles lines and added the
publish-locales lines) and `README.md` (kept the glossary row and main's tool
list, added `publish_locales.py`). `refresh-bundles.yml`, `bundle_refresh.py`,
`locales.py`, `bundles.json` are untouched, so the owner `BUNDLE_AUTO_PUBLISH`
flow is unchanged.

Independent review (Opus, different model from the author): no blockers.
Verified: nothing dropped from main; workflow has no secrets, job-level
`contents: write` only, pinned checkout; only Weblate-approved units ship via
`build_bundle.review_verdict`; 466 tests OK.

Accepted / deferred (non-blocking):
- `config/publish-policy.json` `human_reviewers` is a static list, while the
  bundle path uses the Weblate team in `config/bundles.json`: two places to
  update when a reviewer is added.
- Language discovery and `CODE_RE` duplicate `locales.py`; to be swapped for
  `locale_catalog.py` (#4640).
- Unclosed file handles in `publish_locales.py:490` and some tests.

Verdict: safe to merge once CI is green on the rebased head.
