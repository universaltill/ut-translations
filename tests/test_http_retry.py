"""tools/http_retry.py: which failures are retried, the waits, the log
(ut-docs#5559)."""

import http.client
import io
import os
import socket
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import http_retry as H  # noqa: E402


def http_error(code, headers=None):
    import email.message
    m = email.message.Message()
    for k, v in (headers or {}).items():
        m[k] = v
    return urllib.error.HTTPError("https://x.example/p?secret=1", code, "msg", m, io.BytesIO(b""))


class Transient(unittest.TestCase):
    def test_table(self):
        yes = [TimeoutError(), socket.timeout(), ConnectionResetError(), ConnectionRefusedError(),
               urllib.error.URLError(TimeoutError("timed out")),
               urllib.error.URLError(ConnectionRefusedError()),
               http.client.IncompleteRead(b"par"), http.client.RemoteDisconnected("gone"),
               *[http_error(c) for c in (429, 502, 503, 504)]]
        no = [*[http_error(c) for c in (400, 403, 404, 500, 302)], urllib.error.URLError("ssl"),
              urllib.error.URLError(OSError("dns")), ValueError("json"), KeyError("k")]
        for e in yes:
            self.assertTrue(H.transient(e), repr(e))
            if isinstance(e, urllib.error.HTTPError):
                e.close()
        for e in no:
            self.assertFalse(H.transient(e), repr(e))
            if isinstance(e, urllib.error.HTTPError):
                e.close()


class Call(unittest.TestCase):
    def run_call(self, outcomes, **kw):
        sleeps, log = [], io.StringIO()
        it = iter(outcomes)

        def attempt():
            o = next(it)
            if isinstance(o, BaseException):
                raise o
            return o

        with redirect_stderr(log):
            try:
                out = H.call(attempt, what="thing", sleep=sleeps.append, **kw)
            except Exception as e:  # noqa: BLE001
                out = e
        return out, sleeps, log.getvalue()

    def test_success_first_try(self):
        self.assertEqual(self.run_call(["ok"]), ("ok", [], ""))

    def test_waits_grow_5_10_20(self):
        out, sleeps, log = self.run_call([TimeoutError(), TimeoutError(), TimeoutError(), "ok"])
        self.assertEqual((out, sleeps), ("ok", [5, 10, 20]))
        self.assertIn("attempt 2/4", log)
        self.assertIn("retrying in 10s", log)

    def test_last_exception_is_reraised_identical(self):
        errs = [TimeoutError("a"), TimeoutError("b"), TimeoutError("c"), TimeoutError("d")]
        out, sleeps, _ = self.run_call(errs)
        self.assertIs(out, errs[-1])
        self.assertEqual(sleeps, [5, 10, 20])

    def test_non_transient_raises_at_once(self):
        e = ValueError("x")
        out, sleeps, log = self.run_call([e, "ok"])
        self.assertIs(out, e)
        self.assertEqual((sleeps, log), ([], ""))

    def test_max_tries_one(self):
        out, sleeps, _ = self.run_call([TimeoutError(), "ok"], max_tries=1)
        self.assertIsInstance(out, TimeoutError)
        self.assertEqual(sleeps, [])

    def test_retry_after(self):
        for value, want in (("7", 7), ("999", 30), ("soon", 5), ("0", 0)):
            out, sleeps, _ = self.run_call([http_error(503, {"Retry-After": value}), "ok"])
            self.assertEqual((out, sleeps), ("ok", [want]), value)

    def test_retry_after_junk_falls_back_to_backoff(self):
        junk = ["\u00b2", "9" * 5000, "-1", "Wed, 21 Oct 2026 07:28:00 GMT", "abc", "", "1.5"]
        for value in junk:
            out, sleeps, _ = self.run_call([http_error(503, {"Retry-After": value}), "ok"])
            self.assertEqual(out, "ok", value[:20])
            self.assertEqual(sleeps, [5], value[:20])

    def test_incomplete_read_is_retried(self):
        out, sleeps, _ = self.run_call([http.client.IncompleteRead(b"x"), "ok"])
        self.assertEqual((out, sleeps), ("ok", [5]))

    def test_retry_after_only_for_429_503(self):
        out, sleeps, _ = self.run_call([http_error(502, {"Retry-After": "7"}), "ok"])
        self.assertEqual(sleeps, [5])

    def test_http_error_closed_before_sleep_final_left_open(self):
        first, last = http_error(503), http_error(503)
        closed = []
        first.close = lambda: closed.append("first")
        last.close = lambda: closed.append("last")
        out, _, _ = self.run_call([first, first, first, last])
        self.assertIs(out, last)
        self.assertEqual(closed, ["first"] * 3)

    def test_log_has_reason_but_no_url(self):
        _, _, log = self.run_call([http_error(503), "ok"])
        self.assertIn("HTTP 503", log)
        self.assertNotIn("secret", log)
        self.assertNotIn("x.example", log)
        self.assertIn("thing: attempt 1/4 failed", log)


if __name__ == "__main__":
    unittest.main()
