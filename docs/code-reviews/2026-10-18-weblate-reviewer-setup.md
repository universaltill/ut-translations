# Review: Weblate AI reviewer setup

## Scope

Registered the Weblate reviewer bot and the Hy-MT2 translator account in
`config/ai-accounts.json`, saved the repeatable review checklist under
`tools/review/`, and ignored the requested local review logs.

Updated the reviewer model to `openai/gpt-6-luna` so it matches the model
used for review. The translator remains `mt:ollama` with
`tencent/Hy-MT2-30B-A3B`.

## Findings

- The reviewer identity was verified with a harmless French comment on unit
  24884. Weblate recorded it as `bot-universal-till-ai-reviewer-codex` when
  the credential was read from the `weblate-codex` Keychain entry.
- The 91 French `sale` approvals recorded under the owner account were
  returned to translated state, rechecked against the review rules, and
  approved again under the reviewer bot. Weblate's `sale/fr` change history
  confirms 91 resets and 91 approvals by that bot. The French glossary
  endpoint returned no terms. No legal-labelled unit was changed.
- Seven suggestions from the earlier owner-account session remain to be
  replaced. The reviewer credential receives HTTP 403 when deleting the
  first suggestion, so the delete-and-repost operation is blocked by the
  bot's current Weblate permissions. None of the seven suggestions was
  approved.
- The Weblate REST schema exposes `GET` and `POST` for unit comments, but no
  comment deletion endpoint. The identity-check comments therefore remain
  visible.
- The action log is in the ignored `.review-logs/2026-10-18-fr.jsonl` file.

## Verification

- `/opt/homebrew/bin/python3 -m unittest discover tests` — 165 tests passed.
- `git diff --check` — passed.

## Verdict

The model correction is ready for review. Translation review can proceed
under the verified bot identity; replacing the seven owner-authored
suggestions requires the Weblate reviewer account to have delete-suggestion
permission. An independent diff review found no config issues.
