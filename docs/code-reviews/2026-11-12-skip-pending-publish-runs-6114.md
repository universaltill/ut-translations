# Review: refresh-bundles skips a language whose publish run is still pending (ut-docs#6114)

Built by Opus 5.5 inline (two-line workflow change plus tests, easy card); independent review by Sonnet 5.5 (fresh context).

## What shipped
- `publish-bundle.yml` gains `run-name: publish-bundle ${{ inputs.locale }} ${{ inputs.channel }}`. The runs API does not
  return a run's inputs, so the title is how another workflow can tell which language a run is for. `run-name` is display
  only and is never executed.
- `refresh-bundles.yml` `dispatch`: before `gh workflow run`, it lists publish-bundle runs once per active status
  (`requested`, `waiting`, `pending`, `queued`, `in_progress`, 100 each). It skips the language when a run titled
  `publish-bundle <locale>`, or starting `publish-bundle <locale> `, is active, and writes a `::notice::` with that
  run's URL (an approval is due). Other languages still dispatch. A skipped language does not wait `DISPATCH_GAP`.
  Permissions are unchanged: `actions: write` covers the read, and `contents` stays at none.
- README "Keeping bundles current" describes the skip.

## Findings
| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Low-med | One unfiltered `gh run list --limit 200` is newest first, so a run waiting for weeks could scroll out of the list and be dispatched again | Fixed: one list per active status (`--status`), merged with `jq -s add`; test `test_match_is_found_anywhere_in_a_mixed_list` |
| 2 | Low | The fake `gh` ignored `run list` arguments, so dropping `--workflow` or `displayTitle` would still pass | Fixed: the fake exits 64 unless `--workflow publish-bundle.yml`, `--status` and the JSON fields are present, and filters by the given status |
| 3 | Info | A manual dispatch with a bad locale (`de x`) gets a title that looks like an active `de` run until `validate` fails it seconds later | Accepted: negligible, and not an injection (no `run:` body uses it) |
| 4 | Info | Runs started before the `run-name` existed are titled `publish-bundle` and are not matched | Accepted and documented. The owner approves or rejects the two runs waiting since 2026-11-05 by hand (card's owner action) |
| 5 | — | Found by the full suite: `test_workflow` pins the count of `inputs.locale` in publish-bundle (5) | Updated to 6, and the new use is pinned to the exact `run-name` line |

## Verified
- New `DispatchSkipsPendingRuns` tests extract the real `dispatch` step script from the YAML and run it under
  `bash -eo pipefail` with a fake `gh` on `PATH`. A `de` run waiting for approval means no dispatch for `de`, plus a
  notice naming the run. Every active status skips. `fr` still dispatches, and so does `pt` next to a waiting `pt_BR`.
  A completed run, or no runs at all, dispatches as before.
- TDD: on the `origin/main` workflows with the new tests, 8 of 26 fail. With the fix, all 26 pass
  (`-W error::ResourceWarning`). The orchestrator and the reviewer each re-ran this in a throwaway worktree.
- Full suite: 554 tests. The only remaining error is `test_bundle.Export.test_this_repos_english_matches_universal_till`,
  which comes from this container's shallow universal-till clone; the #5559 record notes the same. CI has the full checkout.

Pre-flight: review record added; README updated; reference/ docs n/a (CI tooling only, no contract or ADR change);
help/locales n/a; follow-up: none new (the owner action on the two old waiting runs is already on the card).

Verdict: safe to merge. The live check is the next scheduled `refresh-bundles` run. A language with a publish run
still waiting shows a notice and gets no new run; the others dispatch 120 s apart as before.
