"""Bounded retry for idempotent reads of the public/self-hosted Weblate and
marketplace APIs (ut-docs#5559). Standard library only.

call(attempt, what=...) runs attempt() (open + read), retries only a
transient failure, and after the last try re-raises the last exception
unchanged, so each caller keeps its existing error text and fail-closed exit.

Transient: a timeout, a connection reset/refused/aborted/dropped, a body
cut short (IncompleteRead), and HTTP 429, 502,
503, 504. Everything else (other 4xx, 500, redirects, TLS/DNS errors, bad
JSON, a caller's own refusal) is raised at once.

Waits: 5, 10, 20 s (cap 30); a 429/503 Retry-After in whole seconds is
honoured up to the same cap. Worst case for one call with a 30 s request
timeout: 4 x 30 + 35 = 155 s (210 s with Retry-After at the cap). Retries are
logged to stderr, never with a host, query string, header or token.
"""

import http.client
import re
import sys
import time
import urllib.error

RETRY_STATUS = {429, 502, 503, 504}
MAX_TRIES = 4
BASE_WAIT = 5
MAX_WAIT = 30


def transient(exc):
    if isinstance(exc, urllib.error.HTTPError):  # before URLError: it is a subclass
        return exc.code in RETRY_STATUS
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, ConnectionError))
    # IncompleteRead: the body was cut short mid-read (the read is inside the attempt).
    return isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead))


def _wait(exc, n):
    backoff = min(BASE_WAIT * 2 ** (n - 1), MAX_WAIT)
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 503):
        v = (exc.headers.get("Retry-After") if exc.headers else None) or ""
        v = v.strip()
        if re.fullmatch(r"[0-9]{1,6}", v):  # not isdigit(): "\u00b2" passes it, int() rejects it
            return min(int(v), MAX_WAIT)
    return backoff


def _reason(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return str(exc) or type(exc).__name__


def call(attempt, *, what, max_tries=MAX_TRIES, sleep=time.sleep, log=None):
    log = log or (lambda m: print(m, file=sys.stderr, flush=True))
    n = 0
    while True:
        n += 1
        try:
            return attempt()
        except Exception as e:  # noqa: BLE001 - re-raised unless transient
            if n >= max_tries or not transient(e):
                raise
            wait = _wait(e, n)
            reason = _reason(e)
            if isinstance(e, urllib.error.HTTPError):
                e.close()
            log(f"{what}: attempt {n}/{max_tries} failed ({reason}); retrying in {wait}s")
            sleep(wait)
