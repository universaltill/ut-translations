# Import existing translations into the Weblate files (ut-docs#4371, #4372)

Review: done (Fable, independent), see below

## Change

- `tools/import_existing.py`: one-off, re-runnable import of the
  translations that already exist — `ut-plugin-language-{de,es,pt}`
  `locales/<code>.json` and universal-till `web/locales/{tr,ar,fa}.json`,
  read at `origin/main` with `git show` — into `<area>/<code>.json`, split
  by this repo's `<area>/en.json`, written with `i18n_map.dumps` (the
  en.json format). Text copied byte for byte.
- 78 files: 13 areas x de, tr, es, pt, ar, fa.
- `tests/test_import_existing.py` (13 tests), README section.

## Decisions to check

- Identical-to-English values are skipped unless the pack's reviewed
  `same-as-en.txt` lists the key: Weblate counts any non-empty target as
  translated, and core's tr/ar/fa hold real English copies
  (`filter.*`, `settings.shop_type.service_layout_note`). Cost: 50 tr /
  19 ar / 16 fa legitimately-same values (EAN-13, PIN) show untranslated.
- Existing file that differs is refused for the whole run unless `--force`.
- Placeholder mismatches are imported and reported, not fixed (0 found).

## Coverage (imported / en keys, 3552)

de 3552, es 3552, pt 3552, tr 3502, ar 3533, fa 3536. Stale keys: 0.

## Tests

`python3 -W error::ResourceWarning -m unittest discover -s tests`: 165 run,
1 failure — `test_till_verifier` signed-bundle case, which fails the same
way on a clean `origin/main` worktree against the local universal-till
checkout (33 commits behind `meta/source.json`'s commit;
"content_digest/content_signature" required). Pre-existing, local only; CI
checks universal-till out at the source commit.


## Independent review (Fable, 2026-10-18)
- **Approve the import.**
  - Dry run against the committed files: 0 written, 78 unchanged.
  - ar/fa are byte-identical to the source, bidi marks kept.
  - The format matches `en.json`, and 154 non-verifier tests pass.
- Bundles are built from Weblate's *approved* units and the publish workflow is dispatch-only, so merging publishes nothing. The tr/ar/fa skips (50/19/16 values equal to English, e.g. `barcode.symbology.*`, `PIN`) fall back to English harmlessly.
- **CI red is pre-existing, not this change.** `test_till_verifier` fails on main too: ADR-0165 (universal-till 8529058bf) requires a content digest that `build_bundle.py` doesn't emit yet. Filed as ut-docs#4634. An earlier note here blamed a stale local checkout; that was wrong.
- **Fixed in this PR:** Weblate would have rewritten all 78 files on its first save (indent 4, unsorted). `provision_weblate.py` now also installs `weblate.json.customize` (sort_keys, indent 2, spaces), with a test. It is applied live by re-running provisioning after merge.
- Low, accepted: `git_show` matches English git stderr (locale-fragile).
