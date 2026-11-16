# Retire the language-pack flow (ut-docs#4371)

**What:** ADR-0160 section 9.3, owner rule "no leftovers". German, Spanish and Portuguese now ship as Weblate bundles (`com.universaltill.language-*`); the hand-made `ut-plugin-language-*` repos are archived. This repo drops its references to them.

**Change:** Deleted the one-off import/fill tools (import_existing.py, fill_from_plugin.py, fill-from-plugins.sh) with their tests; removed the README import section and tools-row mention; reworded README min_coverage text, config/bundles.json note, build_bundle.py/publish_bundle.py pack references. Weblate write-back claim in README left for card N1. Merge only after the final fill (R2).

**Review:** self-review of the diff against the Architect design (WP-C); the pack-name grep is empty outside historical review records.

## Reviewer

Reviewed for ut-docs#4371 (combined batch), Opus 5.5. Diff read in full. The five deleted files (`import_existing.py`, `fill_from_plugin.py`, `fill-from-plugins.sh` and both tests) have no remaining importer or caller: `git grep` for `import_existing|fill_from_plugin|fill-from-plugins|ut-plugin-language` outside `docs/code-reviews` gives no hits. The README, `config/bundles.json`, `build_bundle.py` and `publish_bundle.py` rewording is accurate. The `min_coverage` refusal text changed, but its value and logic did not. Tests: 529 OK (Tester). The README diagram still says "Weblate commits + pushes", but README:285 already says Weblate can't push yet, and the fix is tracked on ut-docs#6442 (card N1). Accepted as a nit.

**Merge gate (not a code finding):** merge only after R2 (the final `fill-from-plugins.sh de es pt` salvage) has run, because this PR deletes that tool. Verdict: approve, merge held for R2.
