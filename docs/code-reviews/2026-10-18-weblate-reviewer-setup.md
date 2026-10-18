# Review: Weblate AI reviewer setup

## Scope

Registered the Weblate reviewer bot and the Hy-MT2 translator account in
`config/ai-accounts.json`, saved the repeatable review checklist under
`tools/review/`, and ignored the requested local review logs.

## Findings

- No content or test issues found in the setup changes. However, this setup
  does not resolve the attribution of the French actions already made with
  the owner credential; Weblate recorded those actions as `farshid`, not as
  the reviewer bot. Those actions should not be treated as bot-attributed
  approvals.
- Live translation review is paused because `WEBLATE_AUTH` is attributed by
  Weblate to `farshid`, not to `bot-universal-till-ai-reviewer-codex`. The
  French `sale` component received 91 approvals, 17 alternative deletions,
  6 suggestion rejections, 5 translation comments, and 7 unapproved Codex
  suggestions (attributed by Weblate to `farshid`) before the identity
  mismatch was detected. The action log is in the ignored
  `.review-logs/2026-10-18-fr.jsonl` file.

## Verification

- `python3 -m unittest discover tests` — 165 tests passed.
- `git diff --check` — passed.

## Verdict

Safe to review as a setup-only PR. The API identity must be resolved before
continuing the French, Malay, or Simplified Chinese translation review.
