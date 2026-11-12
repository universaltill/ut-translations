# Review: stagger bundle publishes and retry transient Weblate reads (ut-docs#5559)

Built by Sonnet 5.5; independent review by Opus 5.5 (fresh context). BA, Architect and Tester ran as separate agents.

## What shipped
- `refresh-bundles.yml` `dispatch`: `max-parallel: 1`, plus a fixed `DISPATCH_GAP` of 120 s after each
  `gh workflow run`. The publish-bundle runs used to start in the same second. On 2026-11-12, 4 of 6 failed in
  `validate` with `can't read Weblate's language list: <urlopen error timed out>` (on 2026-11-04 it was the build's
  Weblate read). `plan` timeout went from 5 to 10 min (worst case is two retried reads). Permissions, the locale regex
  and the per-locale concurrency group in `publish-bundle.yml` are unchanged.
- New `tools/http_retry.py`: tries at most 4 times, waiting 5/10/20 s, and honours `Retry-After` (digits only,
  capped at 30 s). It retries timeouts, connection errors, `IncompleteRead` and HTTP 429/502/503/504. Everything else
  fails at once, re-raising the last error unchanged, so today's messages and the fail-closed exits stay. A retried
  `HTTPError` is closed before the wait. Retry log lines name the method and path only: no host, query or token.
- Used by `locales.fetch_languages_public`, `bundle_refresh._get_json` and `WeblateClient` GETs (`read_tries`,
  default 4). Writes (POST/PATCH/…) get exactly one try. `review_runner.writer_client` uses `read_tries=1`, because
  `Writer.fetch` already has its own 429 loop.
- README "Keeping bundles current" names which reads retry.

## Findings
| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Minor | GET retry stacked under `review_runner.Writer.fetch`'s own 429 loop (≈4× requests when throttled) | Fixed: `read_tries=1` for the writer client; tests `WriterClientSingleAttempt`, `test_read_tries_one_means_single_attempt` |
| 2 | Minor | `Retry-After: ²` or a 5000-digit value passed `isdigit()`, then `int()` raised out of `call()` | Fixed: `fullmatch([0-9]{1,6})`, fallback to backoff; tests for ², huge, -1, HTTP-date, garbage, empty, 1.5 |
| 3 | Minor | Retry log included the next-page URL with its query string | Fixed: `urlsplit(path).path`; test asserts no host/`?`/query |
| 4 | Minor | `http.client.IncompleteRead` not retried | Fixed: transient (body read is inside the attempt); test added |
| 5 | Nit | Workflow comment implied the gap waits for `validate` | Fixed: "fixed ~2 min gap" |
| 6 | Nit | README said Weblate-only | Fixed |
| 7 | Note | The last dispatch also sleeps 120 s | Accepted: costs runner minutes only |

## Verified
- Full suite (`-W error::ResourceWarning`): 547 tests; the only error is `test_bundle.Export.test_this_repos_english_matches_universal_till`.
  It fails identically on `origin/main` in this container (shallow universal-till clone without the `v0.34.19` tag),
  so it comes from the environment. CI has the full checkout.
- Tester: 13 mutation checks (`transient()` → False, `max_tries=1`, retrying 4xx, retrying POST, uncapped
  Retry-After, no close, URL in the log, wrapper removed in each call site, workflow max-parallel/sleep/timeout).
  The right tests failed each time. The orchestrator re-ran the `transient()` mutation in a copy: 10 failures, and
  green again once restored.
- Driven run against a loopback stub. `locales.py --check pt --live` got a timeout, then a 503 with
  `Retry-After: 2`, then a 200, and succeeded in 37 s, with `attempt n/4` lines on stderr and no URL. A 404 fails at
  once with the unchanged text. `bundle_refresh.py` plan succeeded through the stub.

Pre-flight: review record updated; README updated; reference/ docs n/a (no contract or ADR change; reuses the
`review_model.py` retry pattern); help/locales n/a (CI tooling only); follow-up → ut-docs#6114 (don't re-dispatch a
locale whose publish run is waiting for approval).

Verdict: safe to merge. The live check is the next scheduled `refresh-bundles` run: its publish runs should be
about 2 min apart, with no Weblate timeout failures.
