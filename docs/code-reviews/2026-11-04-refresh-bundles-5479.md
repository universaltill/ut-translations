# Review: refresh-bundles plan and opt-in auto publish (ut-docs#5479)

Author model Sonnet 5.5; independent reviewer Opus (read-only, ran the tests).

## What shipped
`tools/bundle_refresh.py`, `.github/workflows/refresh-bundles.yml` (daily plan,
dispatch of `publish-bundle` only for languages in the `BUNDLE_AUTO_PUBLISH`
variable), `tests/test_bundle_refresh.py`, README section, CODEOWNERS lines.
No Weblate or marketplace token is used; no string is approved.

## Findings
| Sev | Finding | Status |
|---|---|---|
| Medium | Day-granular stale compare costs a second build per change (and a second human approval for en/de/tr) | Fixed: compares Weblate's last_change with the build moment in the version (`2.YYYYMMDD.<secs UTC>`); boundary test |
| Medium | A second catalogue listing of a tag could mask the real one (last wins) | Fixed: newest version per tag; test |
| Low | A language whose publish never succeeds is re-dispatched daily | Accepted: visible in the run list; the owner controls the opt-in list |
| Low | Non-numeric `approved` crashed the plan; Weblate pagination not followed | Fixed (coerced to 0 = empty); pagination accepted: 11 languages, endpoint unpaginated at this size |
| Test gaps | main() stdout, redirects | main() stdout test added; redirect refusal reuses locales._NoRedirects, already tested there |

## Verified
Security: no injection (matrix value re-validated with the publish regex, no `${{ }}` in any script), `permissions: {}` with `actions: write` on the dispatch job only, actions pinned, https enforced, no redirects. GITHUB_TOKEN-dispatched workflow_dispatch is allowed. Full suite 434 tests OK.

Verdict: safe to merge. Merging changes nothing until the owner sets `BUNDLE_AUTO_PUBLISH`.
