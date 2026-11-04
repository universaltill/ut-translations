# AI reviewer runner: second-model review of Weblate strings (ut-docs#5420)

Author model: Sonnet 5.5. Reviewer: Fable 5.1 (different model; security and
data-integrity scope per the reviewer skill). ADR-0160 §4 and §10.

## What shipped
- `tools/review_runner.py`: `export | scan | prompt | review | apply`. Scope =
  non-empty target in state translated, or approved by anyone who is not an
  AI reviewer of another family (bootstrap approvals are not a review).
  Dry run by default; `--write` only through a registered reviewer account.
- `tools/review_checks.py`: deterministic pre-checks (publish-gate rules,
  ICU/plural forms per CLDR, RTL, length ratio, brand terms, untranslated,
  glossary) and the real ADR-0040/0106 guards; `needs_human()`.
- `tools/review_prompt.py` (versioned prompt, unit text in an escaped data
  block marked untrusted), `tools/review_model.py` (strict verdict schema,
  stdlib Anthropic Messages adapter, https-only, no redirects).
- `.github/workflows/ai-review.yml`: `workflow_dispatch`, main only,
  `permissions: {}` + `contents: read`, ubuntu-latest, sha-pinned actions,
  each secret in the env of the one step that needs it, universal-till code
  runs only in the secret-free `scan` job.
- `config/review-glossary.json` (same-as-source lists, no hard terms yet),
  `docs/ai-review.md`, README pointer, `.review-logs/` gitignored.
- Tests: `tests/test_review_runner.py` (fake Weblate + fake Messages API),
  `tests/test_review_checks.py`, `tests/test_ai_review_workflow.py`.

## What it exposes
Nothing new at rest: both secrets live in the `ai-review` environment
(limited to main) and reach only the step that needs them; the runner never
prints or logs a token, and the audit artifact holds unit text, hashes,
verdicts and error strings only (Weblate error bodies are truncated and
carry no token). `vars.WEBLATE_URL` can only point the token at an https
host (the client refuses plain http off loopback); a wrong value needs
repo-variable write access, same as the existing publish workflow.

## Findings (Fable 5.1)
- IMPORTANT, fixed — the re-fetch before each write was a plain
  `client.get`: a 429 raised, counted as an error and skipped the unit. A
  full language is ~3,800 re-fetches plus two PATCHes each, well over
  Weblate's per-hour throttle, so a long run would have degraded into
  errors. `Writer.fetch()` now shares the 429/Retry-After loop with the
  writes without being throttled or counted. Test:
  `test_refetch_429_is_retried_and_counts_as_no_write`.
- IMPORTANT, fixed — a unit whose Weblate history records no author for the
  current text (no changes, or the last change's text differs) was
  approved. Weblate would then record the reviewer as author and approver of
  the text, which `build_bundle.review_verdict()` rejects as
  `self_approved`, and the string could never ship until re-authored.
  `export` now drops author/approver when the history text does not match
  the current single-form target; `apply` refuses an approve for a unit
  without an author (suggestions and comments still go through). Test:
  `test_unit_without_a_recorded_author_is_not_approved`.
- MEDIUM, fixed — `is_bot()` treated a missing `is_bot` field (a token
  without `user.view`) as "human", so an unlisted bot's approval would have
  passed as a human review and the unit would never be re-reviewed.
  `export` now fails closed with a clear message, mirroring
  `build_bundle.Accounts.user`. Test:
  `test_export_fails_closed_when_the_token_cannot_see_the_bot_flag`.
- MEDIUM, fixed — the `apply` job was skipped whenever `review` failed on a
  single batch (5 retries exhausted), so no verdict was applied and no
  audit was produced for the run. `apply` now runs after a failed (not
  cancelled) `review`; batches without a verdict file get "no verdict",
  which is never an approval. Workflow test updated.
- LOW, fixed — `correction_problem()` re-checked a model correction with
  the real repo's script table instead of `--repo`'s; `apply` now passes the
  same script as `export`. The `lang` workflow input is now `required`.
- LOW, accepted — a model correction is pre-checked but not scanned
  (ADR-0040/0106) before it is posted as a suggestion. It is only a
  suggestion: a different account or a person accepts it, and the bundle
  build scans the accepted text. Noted for a follow-up if suggestions are
  ever auto-accepted.
- LOW, accepted — `final_verdict` turns an approve on a batch whose scans
  are not "pass" into `needs-fix` (comment). Unreachable through the CLI
  (batches exist only after `scan_and_plan`, and an edited batch fails its
  `batch_sha`); kept as belt and braces.
- No finding: token handling (env, else Keychain by service name; never in
  argv, output or audit), the needs-human precedence over any model verdict
  (decided before the model is asked; a forged verdict for a decided unit
  is never read), family-level model comparison with the account's model,
  idempotency keyed on (unit, src sha, tgt sha, prompt version), stale
  re-fetch, pagination through `WeblateClient.list`, prompt-injection
  containment (`<`/`>` escaped inside the data block, one closing marker),
  and the PR #6 boundary (`config/ai-accounts.json`,
  `tools/review/CODEX-REVIEWER.md`, `tools/bulk_locale.py` untouched; the
  one shared `.gitignore` line is identical in both branches and merges
  clean).

## Verified beyond the automated tests
- Mutation check of two guards with the file restored from a scratchpad
  copy: disabling the needs-human precedence failed
  `test_needs_human_never_written_even_if_the_model_approves` and
  `test_needs_human_override`; disabling the family refusal failed
  `test_verdict_model_not_the_accounts_family` and
  `test_model_family_mismatch_refused`. The tests are not false-pass.
- The three pinned action shas resolve to `actions/checkout` v7.0.1,
  `actions/upload-artifact` v7.0.2 and `actions/download-artifact` v8.0.2 on
  GitHub and match the pins already used by `publish-bundle.yml`/`test.yml`.
- Full gate after the fixes: `python3 -W error::ResourceWarning -m unittest
  discover -s tests` with `UTT_TILL_SOURCE`/`UTT_REQUIRE_TILL=1` (real
  guards): green, 310 tests (307 before the review, 3 added).
- Approve flow cross-checked against the real gate:
  `test_bootstrap_unit_approved_passes_the_real_publish_gate` ends with
  `review_verdict(...) is None` for the registered reviewer account.

## Verdict
Safe to merge as a dry-run-only tool today: `--write` refuses until
ut-docs#4366 registers `bot-universal-till-ai-reviewer-claude` (role
`reviewer`, model `claude`) and the `ai-review` environment holds
`WEBLATE_REVIEWER_TOKEN`. The three-language run (zh_Hans, fr, ms) and the
Weblate suggestion/comment endpoints' live confirmation stay on the card.

## Deferred
- Scan (ADR-0040/0106) of model corrections before they are suggested.
- Human-approved legal units are listed as needs-human on every run (BA
  decision); with `--comment-needs-human` they would be commented on each
  run, so use that flag on first runs only, or add a "already commented"
  check in a follow-up.
