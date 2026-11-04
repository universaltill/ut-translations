"""The model side of the AI reviewer runner (ADR-0160 §4, §10;
ut-docs#5420): the strict verdict schema, and a standard-library adapter
for the Anthropic Messages API.

parse_verdicts(doc, batch) -> ({unit id: verdict}, [errors]). Anything that
doesn't match the schema exactly gives no verdict for that unit, and a file
for another batch or prompt version gives none at all: no verdict is never
an approval.

AnthropicAdapter.complete(system, user) -> (text, "anthropic/<model>"),
the model id taken from the response, not the request. 429/529 are retried
up to 5 tries in all, honouring retry-after; a refusal or anything else is
a ModelError (that batch gets no verdicts). No fallback model: the review
is recorded under the model that actually answered. The API key travels
over https only (plain http to loopback for tests), and redirects are never
followed.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import review_prompt

VERDICTS = ("approve", "needs-fix", "needs-human")
NOTE_MAX = 300
DEFAULT_MODEL = "claude-opus-5-5"
API_VERSION = "2026-10-21"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
RETRY_STATUS = {429, 529}


class ModelError(Exception):
    pass


def extract_json(text):
    """The model's reply as JSON. A single surrounding code fence is
    tolerated; anything else that isn't one JSON value raises ValueError."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return json.loads(t)


def _bad_verdict(v, units):
    if not isinstance(v, dict):
        return "verdict is not an object"
    uid = v.get("unit")
    if not isinstance(uid, int) or isinstance(uid, bool) or uid not in units:
        return f"unit {uid!r} is not in this batch"
    if v.get("tgt_sha") != units[uid].get("tgt_sha"):
        return f"unit {uid}: tgt_sha does not match the batch"
    if v.get("verdict") not in VERDICTS:
        return f"unit {uid}: verdict {v.get('verdict')!r} unknown"
    reasons = v.get("reasons", [])
    if not isinstance(reasons, list) or not all(isinstance(r, str) and len(r) <= 40 for r in reasons):
        return f"unit {uid}: reasons must be a list of short strings"
    corr = v.get("correction")
    if corr is not None:
        n = len(units[uid].get("target") or [])
        if not isinstance(corr, list) or len(corr) != n or not all(isinstance(c, str) for c in corr):
            return f"unit {uid}: correction must be null or {n} string(s)"
    note = v.get("note", "")
    if not isinstance(note, str) or len(note) > NOTE_MAX:
        return f"unit {uid}: note must be a string of at most {NOTE_MAX} characters"
    return None


def parse_verdicts(doc, batch):
    if isinstance(doc, (str, bytes)):
        try:
            doc = json.loads(doc)
        except ValueError as e:
            return {}, [f"not JSON: {e}"]
    if not isinstance(doc, dict):
        return {}, ["not a JSON object"]
    if doc.get("schema") != 1:
        return {}, [f"schema {doc.get('schema')!r} is not 1"]
    if doc.get("batch_sha") != batch.get("batch_sha"):
        return {}, ["batch_sha does not match the batch (re-exported since?)"]
    if doc.get("prompt_version") != batch.get("prompt_version") or doc.get("prompt_version") != review_prompt.PROMPT_VERSION:
        return {}, [f"prompt_version {doc.get('prompt_version')!r} is not {review_prompt.PROMPT_VERSION}"]
    vs = doc.get("verdicts")
    if not isinstance(vs, list):
        return {}, ["verdicts is not a list"]
    units = {u["id"]: u for u in batch.get("units") or []}
    out, errors, dup = {}, [], set()
    for v in vs:
        why = _bad_verdict(v, units)
        if why:
            errors.append(why)
            continue
        uid = v["unit"]
        if uid in out or uid in dup:
            dup.add(uid)
            out.pop(uid, None)
            errors.append(f"unit {uid}: more than one verdict")
            continue
        out[uid] = {"verdict": v["verdict"], "reasons": list(v.get("reasons") or []),
                    "correction": v.get("correction"), "note": v.get("note", "")}
    return out, errors


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnthropicAdapter:
    def __init__(self, api_key, model=DEFAULT_MODEL, base_url="https://api.anthropic.com", timeout=600,
                 sleep=time.sleep, max_tries=5, effort="high", max_tokens=16000):
        if not api_key:
            raise ModelError("no Anthropic API key (ANTHROPIC_API_KEY)")
        u = urllib.parse.urlparse(base_url)
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in LOOPBACK):
            raise ModelError(f"refusing {u.scheme}:// for {u.hostname}: the API key must travel over https")
        self._key = api_key
        self.model, self.base, self.timeout = model, base_url.rstrip("/"), timeout
        self.sleep, self.max_tries, self.effort, self.max_tokens = sleep, max_tries, effort, max_tokens
        self._opener = urllib.request.build_opener(_RefuseRedirects)

    def _post(self, body):
        req = urllib.request.Request(self.base + "/v1/messages", data=json.dumps(body).encode(), method="POST")
        req.add_header("x-api-key", self._key)
        req.add_header("anthropic-version", API_VERSION)
        req.add_header("content-type", "application/json")
        with self._opener.open(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def complete(self, system, user):
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                "thinking": {"type": "adaptive"}, "output_config": {"effort": self.effort},
                "messages": [{"role": "user", "content": user}]}
        for attempt in range(1, self.max_tries + 1):
            try:
                resp = self._post(body)
                break
            except urllib.error.HTTPError as e:
                with e:
                    retry_after = e.headers.get("retry-after")
                if e.code in RETRY_STATUS and attempt < self.max_tries:
                    try:
                        wait = min(float(retry_after), 120) if retry_after else min(2 ** attempt, 60)
                    except ValueError:
                        wait = min(2 ** attempt, 60)
                    self.sleep(wait)
                    continue
                raise ModelError(f"Messages API HTTP {e.code}") from None
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise ModelError(f"Messages API: {e}") from None
        if resp.get("stop_reason") not in ("end_turn", "stop_sequence"):
            raise ModelError(f"model stopped with {resp.get('stop_reason')!r}: no verdicts")
        text = "".join(b.get("text", "") for b in resp.get("content") or [] if b.get("type") == "text")
        model = resp.get("model")
        if not model:
            raise ModelError("the response names no model")
        return text, "anthropic/" + model
