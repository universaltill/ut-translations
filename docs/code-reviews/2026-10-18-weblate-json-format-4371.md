# Weblate writes JSON like our files (ut-docs#4371 follow-up)

## What shipped
- `provision_weblate.py`: each component gets `file_format_params` = `{json_sort_keys: case_sensitive, json_indent: 2, json_indent_style: spaces}`. It replaces the `weblate.json.customize` add-on from ut-translations#3, which doesn't exist in Weblate 2026.10 (live API: "Add-on not found"). Test updated.
- Applied live on 2026-10-18 with `WEBLATE_PUSH_URL=""`, which keeps the current empty push URL: deploy keys are still pending in #4365. A second dry run shows 0 changes, and the API returns exactly these params.

## Review (Fable, independent)
- Approve.
- Key order: all 3,552 keys are lowercase, `_`, digits and `.`, so a case-sensitive sort equals Python `sorted()`. Our writer (`ensure_ascii=False`, indent 2, sorted, trailing newline) matches translate-toolkit's serialisation, so no first-save reformat is expected.
- Raised risk: if the API returned defaults, the comparison would PATCH on every run. Checked: idempotent.
- `tests.test_provision`: 20 tests OK.
