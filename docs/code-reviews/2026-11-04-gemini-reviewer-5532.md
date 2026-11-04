# Review: Gemini reviewer backend (ut-docs#5532, part of #5420)

Author model: Sonnet 5.5. Independent reviewer: Opus 5.5 (read-only, ran the tests and `terraform validate`).

## What shipped
- `GeminiAdapter` in `tools/review_model.py` (v1beta generateContent; key in the `x-goog-api-key` header only; https only; no redirects; model name validated; errors never echo a body; replies that did not stop with STOP, were blocked or name no model are errors; thinking capped).
- `review --provider gemini` in `tools/review_runner.py`; key from `GEMINI_API_KEY` or Keychain `weblate-review-gemini`.
- Family `gemini` in `review_checks.family`; account `bot-universal-till-ai-reviewer-gemini` (reviewer) in `config/ai-accounts.json`, with its own Weblate token source.
- `docs/ai-review.md`; `tests/test_review_gemini.py` (26 tests). Full suite: 492 OK.

## Findings
| # | Sev | Finding | Result |
|---|---|---|---|
| 1 | Medium | Generic `WEBLATE_REVIEWER_TOKEN` (the Claude reviewer's) would be used for the Gemini account: writes as the wrong Weblate user, audit names Gemini | Fixed: an account with its own `keychain` entry reads only that item or its `token_env`; tests prove the generic token is refused (exit 2, zero writes) |
| 2 | Medium | Infra: key could exist without its daily cap | Fixed in ut-infra (`depends_on`) |
| 3 | Low | Thinking tokens could exhaust `maxOutputTokens` | Fixed: `thinkingBudget` 8192, `maxOutputTokens` 32000, test |
| 4 | Low | 529 retried, 504 not | Fixed: 504 retried, test. 429 body-hint backoff not parsed: accepted (a failed batch is retried by re-running `review`) |

Verified: rules re-proved under the Gemini account (legal/money/tax/receipt stay needs-human incl. forged verdicts; model only escalates; needs-fix is a suggestion, no PATCH of text; cross-family verdict files refused both ways; own-family author skipped; no key = exit 2).
Italian dry run (no writes): 3649 in scope, 342 decided without a model (298 needs-human, 44 needs-fix), 3307 units in 87 batches for the model.

Verdict: safe to merge. Deferred: `ai-review.yml` has no Gemini job (owner decides whether CI should run it).
