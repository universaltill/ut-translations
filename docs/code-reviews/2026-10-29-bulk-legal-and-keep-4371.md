# Bulk tool: owner-approved legal strings and "keep as is" (ut-docs#4371)

Author model: Sonnet 5.5. Reviewer: Opus 5.5 (different model, see findings).

## What changed
- `tools/bulk_locale.py approve-legal`: strings the bundle build treats as
  `legal` (keys marked in `meta/<area>.json`, the build's own source; Weblate
  units carry no legal label) are approved by a **human account** (the
  owner's, a `human-reviewers` member). A string a bot already approved is
  first put back to "translated" by the translator bot with the text
  unchanged, so the translator stays the recorded author and the owner is a
  different approver (ADR-0160 §4).
- Earlier bug found on the way: the first tool version looked for a Weblate
  `legal` label that does not exist, so it never skipped legal keys and the
  bootstrap bot approved them (the build then omitted them as
  `legal_needs_human`). Now `is_legal()` reads the meta files.
- `keep_as_is()`: codes, acronyms and product names (SKU, EAN-13, Code 128,
  Local Plus) are copied from English, not sent to the model.
- `scripts/bulk-locales.sh`: `--legal` and `--name` (a second container beside
  a running queue); reports are now `<lang>-<command>.json`.

## What it exposes
The owner's Weblate token reaches a Docker Desktop container as an inherited
environment variable, only for `--legal` runs. It is the owner's own machine
and the code is mounted read-only. The approval is owner-directed bulk
approval of statutory receipt text, not a legal review: re-review is pending
and the build still needs the `bundle-publish-human` approval before German
publishes.

## Verified
Unit tests (23 in `test_bulk_locale.py`, full suite OK); a dry run against the
live Weblate finds 197 German legal strings.

## Findings
See the addendum below.

## Addendum: Opus 5.5 review (3 real findings, all fixed)
- HIGH `keep_as_is` matched every all-caps string, so "TAX INVOICE", "TOTAL",
  "VAT", Z-report headings would have been copied in English and then
  approved. Fixed: exact allowlist only; tests added for those strings.
- HIGH the translator's state-only reset does not change the recorded author
  (whoever last changed the text), so the owner's approval could still be
  `self_approved`. Fixed: `approve_legal_unit` always re-authors (edit and
  restore by the translator) before the human approves; test updated.
- MEDIUM `--legal` without `--name` would have removed the running queue
  container. Fixed: default name `ut-bulk-legal`, and a running container of
  the same name is never replaced.
- LOW fixed: `--rm` (tokens do not linger in a stopped container; the log goes
  to `~/ut-bulk-reports/<name>.log`), the human token is passed only for
  `--legal`, `--name` argument check, header comment.
- Found while starting the runs: the script passed `--include-approved` to
  every command, so a translate run (`all`) would have re-authored the whole
  language. Now only `approve` and `approve-legal` get it.
