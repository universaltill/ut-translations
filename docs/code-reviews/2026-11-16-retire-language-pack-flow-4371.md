# Retire the language-pack flow (ut-docs#4371)

**What:** ADR-0160 section 9.3, owner rule "no leftovers". German, Spanish and Portuguese now ship as Weblate bundles (`com.universaltill.language-*`); the hand-made `ut-plugin-language-*` repos are archived. This repo drops its references to them.

**Change:** Deleted the one-off import/fill tools (import_existing.py, fill_from_plugin.py, fill-from-plugins.sh) with their tests; removed the README import section and tools-row mention; reworded README min_coverage text, config/bundles.json note, build_bundle.py/publish_bundle.py pack references. Weblate write-back claim in README left for card N1. Merge only after the final fill (R2).

**Review:** self-review of the diff against the Architect design (WP-C); the pack-name grep is empty outside historical review records.
