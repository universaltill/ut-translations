# Review: AI review runner for surface components (ut-docs#5570)

Built by Sonnet 5.5 (earlier run, finished here); independent review by Opus 5.5.

## What shipped
`tools/review_runner.py` reviews non-till surface components from
`config/surfaces.json` (website first). Till source scans are skipped for
surfaces only; every other rule is unchanged. Prompt gets a web-prose hint.

## Findings
| # | Sev | Finding | Outcome |
|---|---|---|---|
| 0 | Medium | (own) surface was read back from `units.json` | Fixed: keyed by component via `surface_components(repo)` in scan/plan/apply |
| 1 | Medium | website price amounts (`£70`) and cookie/consent wording were model-approvable | Fixed: `MONEY_SYMBOL` + wider `HUMAN_WORDS`; test added |
| 2 | Low-Med | component itself comes from the file (re-hashable batch) | Fixed: `apply` refuses a unit whose Weblate record is in another component |
| 3 | Low | `batch['surface']` is a prompt hint only | Accepted |
| 4 | Low | export with no `--component` now includes surfaces | Documented in docs/ai-review.md |
| 5 | Low | `legal` labels come from ut-website's meta | Accepted; wording scan backs it up |

## Verified
Review tests 145 OK, full suite 510 OK. Read-only dry run against live
Weblate: website/fr has 483 units, all untranslated (state 0), so 0 in scope;
website/de: 391 translated, plan = 38 decided without a model (30 needs-human),
353 in 9 batches. No writes.

Verdict: safe to merge.
