# Fill empty Weblate strings from the old language plugins (ut-docs#4371)

Author model: Sonnet 5.5. Reviewer: Opus 5.5 (different model).

## What shipped
`tools/fill_from_plugin.py` + `scripts/fill-from-plugins.sh`: for a language,
each empty single-form string whose key the old `ut-plugin-language-<code>`
translates is saved as "translated" by the translator bot, after the bundle
build's own `check_string` (placeholders, tags, script). Rejected texts are in
the report. `--approve` then runs `bulk_locale.py approve` (the approver bot, a
different account). `--dry-run` writes nothing. Reports:
`~/ut-bulk-reports/<lang>-fill.json`.

Why: Weblate's import predates keys the plugins gained since (German: 194 of
198 empty strings were already translated in the plugin).

## What it exposes
Writes to the production Weblate with the existing translator/approver bot
tokens (Keychain, never printed). No new credential.

## Findings (all fixed)
- `--approve` did not pass `--weblate-url`/`--component` on: now it does. It
  still approves every waiting non-legal string of the language, as documented.
- `--dry-run` was only honoured as the first argument of the wrapper (a later
  one would have written and approved): flags are now parsed in any position.
- Legal keys: the plugins have no English baseline, so legal wording may be
  stale. Legal keys are skipped (`skipped_legal`) and go through
  `approve-legal` after a deliberate translation.
- One failing language aborted the batch: the wrapper continues and exits 1.
- No stop after repeated failed writes: stops after 8 in a row.
- Not a finding: no overwrite of existing translations (only empty,
  not-yet-translated strings are touched).

## Verified
7 unit tests (selection, checks, legal skip, `zh_Hans` file name); reviewer
confirmed all 3,751 plugin keys per language (de/es/pt) pass the checks
offline against the current `en.json`.
