#!/usr/bin/env python3
"""AI review of Weblate translations by a model of a different family than
the one that wrote them, with a human on legal (ADR-0160 §4, §10;
ut-docs#5420). A build-time tool: no till ever depends on it.

  export   one language's units from Weblate: a non-empty target in state
           translated, or approved by anyone but an AI reviewer of another
           family (the bootstrap approver's approvals are not a review).
           Units an AI reviewer of a different family already approved are
           skipped unless --include-reviewed-by names that account; human
           approvals are skipped (legal ones are still listed, needs-human).
           Pending suggestions and empty units are never in scope. Runs the
           deterministic pre-checks (tools/review_checks.py) and, with
           --till-source, the ADR-0040/0106 scans, then plans: a pre-check
           failure is needs-fix and a legal/tax/fiscal/money unit is
           needs-human, both without asking a model (decided.json); the
           rest go to batches/<component>-NNN.json for the model.
           --no-scans stops after units.json (the workflow's export job
           holds the Weblate token and runs no universal-till code).
  scan     the scans and the plan over an export's units.json (no secret).
  prompt   print the exact prompt for one batch, for an external sub-agent,
           which writes verdicts/<same file name> (schema in review_model).
  review   the same prompt through the Anthropic Messages API
           (ANTHROPIC_API_KEY), one verdict file per batch.
  apply    verdicts -> audit.jsonl + summary.md, and with --write the
           Weblate writes, as the reviewer account only:
             approve      state translated then approved, text untouched,
                          so Weblate records the reviewer as approver of
                          the current text (if the second call fails, the
                          unit is put back to approved)
             needs-fix    a suggestion with the correction (never an edit)
                          and a comment naming the failed check
             needs-human  nothing (a comment with --comment-needs-human)
           A model approve never overrides a failed pre-check or a
           needs-human unit; the model can only escalate. A correction must
           pass the pre-checks itself or it is dropped.

Fail closed (exit 2, zero writes, audit still written): --write with no
token, an account not in config/ai-accounts.json or not a reviewer, the
bootstrap approver, a verdict model of another family than the account's
model, or an unknown verdict model. Per unit (skipped, recorded): the
author is the reviewer account or of the same model family, the text
changed since export (stale), or the verdict file doesn't match its batch.
Dry run is the default.

Idempotent: an audit key (unit, source sha, target sha, prompt version)
already written:true in any audit.jsonl under --logs-root is never written
again; re-running resumes. --re-review ignores earlier audits.

Tokens: WEBLATE_REVIEWER_TOKEN, else the macOS Keychain service
weblate-claude-reviewer (export also takes WEBLATE_TOKEN for reading);
ANTHROPIC_API_KEY, else Keychain anthropic-api. Never printed or logged.

Exit: 0 done, 1 a write or model call failed, 2 refused or usage.
"""

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_bundle  # noqa: E402
import locales  # noqa: E402
import review_checks  # noqa: E402
import review_model  # noqa: E402
import review_prompt  # noqa: E402
from provision_weblate import WeblateClient, WeblateError  # noqa: E402
from review_checks import run_scans  # noqa: E402,F401  (patched in tests)
from review_model import AnthropicAdapter  # noqa: E402,F401  (patched in tests)

RUNNER_VERSION = "1.0.0"
PROJECT = "universal-till"
WEBLATE_URL = "https://translate.universaltill.com"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ACCOUNT = "bot-universal-till-ai-reviewer-claude"
BOOTSTRAP = "bot-universal-till-bootstrap-approver"
REVIEWER_KEYCHAIN = "weblate-claude-reviewer"
ANTHROPIC_KEYCHAIN = "anthropic-api"
TRANSLATED, APPROVED, READONLY = 20, 30, 100
MAX_CONSECUTIVE_FAILURES = 5
DETERMINISTIC = "none:deterministic-checks"


class RunnerError(Exception):
    pass


def secret(service, *env_vars):
    """A token from the environment, else the macOS Keychain; None when
    neither has one. Never printed. UTT_NO_KEYCHAIN=1 skips the Keychain."""
    for var in env_vars:
        v = os.environ.get(var, "").strip()
        if v:
            return v
    if os.environ.get("UTT_NO_KEYCHAIN") == "1" or sys.platform != "darwin":
        return None
    try:
        v = subprocess.check_output(["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                                     "-s", service, "-w"], text=True, stderr=subprocess.DEVNULL).strip()
        return v or None
    except (OSError, subprocess.CalledProcessError):
        return None


def sha(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_accounts(path):
    return (load_json(path).get("accounts") or {}) if os.path.exists(path) else {}


def load_glossary(path):
    """The repo's review glossary; none (no hard terminology rules) when the
    file is absent, e.g. a fixture repo."""
    if not os.path.exists(path):
        print(f"no glossary at {path}: terminology rules off", file=sys.stderr)
        return {}
    return review_checks.load_glossary(path)


def model_of(name, accounts):
    """The account's model, or "human" for anyone not listed."""
    return (accounts.get(name) or {}).get("model") or "human"


def parties(history):
    """(current text, its author, its approver) from a unit's changes,
    oldest first: the same walk as build_bundle.review_verdict."""
    text, author, approver = None, None, None
    for ch in history:
        action = ch.get("action")
        if action in build_bundle.INVALIDATING:
            approver = None
            continue
        tgt = ch.get("target")
        if action in build_bundle.TEXT_ACTIONS and isinstance(tgt, str) and tgt != text:
            text = tgt
            author = build_bundle.username(ch.get("author")) or build_bundle.username(ch.get("user"))
            approver = None
        is_approval = action == build_bundle.A_APPROVE or (
            action == build_bundle.A_ACCEPT and (ch.get("details") or {}).get("state") == build_bundle.STATE_APPROVED)
        if is_approval and tgt == text:
            approver = build_bundle.username(ch.get("user"))
    return text, author, approver


def ai_reviewed(author, approver, accounts):
    """The current text carries an AI review by another family."""
    if not approver or approver == BOOTSTRAP or approver == author:
        return False
    acc = accounts.get(approver) or {}
    if acc.get("role") != "reviewer":
        return False
    return review_checks.family(acc.get("model")) != review_checks.family(model_of(author, accounts))


def audit_key(unit, src_sha, tgt_sha, prompt_version=review_prompt.PROMPT_VERSION):
    return f"{unit}:{src_sha}:{tgt_sha}:{prompt_version}"


def prior_written(logs_root):
    done = set()
    for path in glob.glob(os.path.join(logs_root, "**", "audit.jsonl"), recursive=True):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                if a.get("written"):
                    done.add(audit_key(a.get("unit"), a.get("src_sha"), a.get("tgt_sha"), a.get("prompt_version")))
    return done


def labels(unit):
    return sorted({(lab.get("name") if isinstance(lab, dict) else lab) for lab in unit.get("labels") or []})


def areas(repo, only):
    names = list(load_json(os.path.join(repo, "meta", "areas.json")))
    for a in names:
        build_bundle.i18n_map.validate_area_id(a)
    if only:
        unknown = sorted(set(only) - set(names))
        if unknown:
            raise RunnerError(f"unknown component(s): {', '.join(unknown)}")
        names = [a for a in names if a in only]
    return names


def weblate_client(*env_vars, services=(REVIEWER_KEYCHAIN,)):
    token = None
    for svc in services:
        token = secret(svc, *env_vars)
        if token:
            break
    if not token:
        return None
    return WeblateClient(os.environ.get("WEBLATE_URL") or WEBLATE_URL, token)


# --- export ---------------------------------------------------------------------

def export(args):
    lang = args.lang
    if not locales.CODE_RE.fullmatch(lang or ""):
        raise RunnerError(f"bad Weblate language code {lang!r}")
    client = weblate_client("WEBLATE_TOKEN", "WEBLATE_REVIEWER_TOKEN", services=(REVIEWER_KEYCHAIN, "weblate-codex"))
    if client is None:
        raise RunnerError("no Weblate token to read with: set WEBLATE_TOKEN or WEBLATE_REVIEWER_TOKEN")
    accounts = load_accounts(args.accounts)
    glossary = load_glossary(args.glossary)
    script = review_checks.script_for(lang, args.repo)
    include = set(args.include_reviewed_by or [])
    done = set() if args.re_review else prior_written(args.logs_root)
    records, approved_seen = [], []
    bots = {}

    def is_bot(name):
        """An unlisted Weblate bot's approval is no review (the publish gate
        rejects it as unknown_approver): such units stay in scope."""
        if name not in bots:
            u = client.get(f"/api/users/{urllib.parse.quote(name)}/") or {}
            if "is_bot" not in u:
                raise RunnerError(f"can't tell whether Weblate user {name!r} is a bot (the token needs user.view): "
                                  "a human approval can't be told from an unlisted bot's")
            bots[name] = bool(u["is_bot"])
        return bots[name]
    for comp in areas(args.repo, args.component):
        meta_path = os.path.join(args.repo, "meta", f"{comp}.json")
        meta = load_json(meta_path) if os.path.exists(meta_path) else {}
        base = f"/api/translations/{PROJECT}/{comp}/{lang}"
        units = client.list(f"{base}/units/?page_size=1000")
        if not units:
            continue
        info = client.get(f"{base}/") or {}
        plural_number = ((info.get("language") or {}).get("plural") or {}).get("number")
        history = {}
        for ch in client.list(f"{base}/changes/?page_size=1000") or []:
            uid = build_bundle.unit_id(ch.get("unit"))
            if uid is not None:
                history.setdefault(uid, []).append(ch)
        for chs in history.values():
            chs.sort(key=lambda c: (c.get("timestamp") or "", c.get("id") or 0))
        for u in units:
            state = u.get("state") or 0
            target = list(u.get("target") or [])
            if not (TRANSLATED <= state < READONLY) or not any(t.strip() for t in target):
                continue
            if state >= APPROVED:
                approved_seen.append(u)
            source = list(u.get("source") or [])
            key = u.get("context") or ""
            text, author, approver = parties(history.get(u.get("id"), []))
            if len(target) == 1 and text != target[0]:
                author = approver = None  # the history doesn't cover the current text (gate: no_history)
            m = meta.get(key) or {}
            human = review_checks.needs_human(key, comp, m, set(labels(u)), "\n".join(source))
            if approver and approver not in accounts and state >= APPROVED and not is_bot(approver):
                if not human:
                    continue  # a human approval counts as reviewed
            elif ai_reviewed(author, approver, accounts) and approver not in include:
                continue
            rec = {"id": u["id"], "key": key, "component": comp, "lang": lang, "state": state,
                   "source": source, "target": target, "src_sha": sha(source), "tgt_sha": sha(target),
                   "author": author, "approver": approver, "labels": labels(u), "flags": u.get("flags") or "",
                   "plural_number": plural_number}
            if audit_key(rec["id"], rec["src_sha"], rec["tgt_sha"]) in done:
                continue
            rec["checks"] = review_checks.check_unit(rec, lang, m, glossary, plural_number, script)
            rec["checks"]["scans"] = "pending"
            rec["needs_human"] = human
            records.append(rec)
            if args.limit and len(records) >= args.limit:
                break
        if args.limit and len(records) >= args.limit:
            break
    idx = review_checks.term_index(approved_seen)
    for rec in records:
        rec["hints"] = review_checks.terminology_hints(rec, idx)
    os.makedirs(args.out, exist_ok=True)
    for sub in ("batches", "verdicts"):
        for old in glob.glob(os.path.join(args.out, sub, "*.json")):
            os.remove(old)  # a fresh export replaces the previous plan
    for old in ("decided.json",):
        if os.path.exists(os.path.join(args.out, old)):
            os.remove(os.path.join(args.out, old))
    dump(os.path.join(args.out, "units.json"), {
        "schema": 1, "lang": lang, "generated_at": now(), "runner_version": RUNNER_VERSION,
        "prompt_version": review_prompt.PROMPT_VERSION, "batch_size": args.batch_size, "scans": "pending",
        "units": records})
    print(f"export {lang}: {len(records)} unit(s) in scope -> {args.out}/units.json")
    if args.till_source:
        scan_and_plan(args.out, args.till_source, args.repo)
    else:
        print("scans not run (--no-scans): run `review_runner.py scan --out DIR --till-source PATH` next")
    return 0


def scan_and_plan(out, till, repo):
    doc = load_json(os.path.join(out, "units.json"))
    units = doc["units"]
    entries = [(f"{u['id']}.{n}", form) for u in units for n, form in enumerate(u["target"])]
    hits = {}
    if entries:
        commit = build_bundle.read_source(repo)[0]
        hits = run_scans(till, commit, entries)
    for u in units:
        found = sorted({v for k, v in hits.items() if str(k).split(".")[0] == str(u["id"])})
        u["checks"]["scans"] = "; ".join(found) if found else "pass"
    doc["scans"] = "done"
    dump(os.path.join(out, "units.json"), doc)
    plan(out, doc)


def batch_digest(batch):
    return sha({k: batch.get(k) for k in ("prompt_version", "lang", "component", "units")})


def plan(out, doc):
    lang, size = doc["lang"], max(1, int(doc.get("batch_size") or 40))
    decided, pending = [], {}
    for u in doc["units"]:
        bad = review_checks.failed(u["checks"])
        if u.get("needs_human"):
            decided.append({**u, "verdict": "needs-human", "reasons": ["legal"] + bad})
        elif bad:
            decided.append({**u, "verdict": "needs-fix", "reasons": bad})
        else:
            pending.setdefault(u["component"], []).append(u)
    dump(os.path.join(out, "decided.json"), {"schema": 1, "lang": lang, "units": decided})
    n_batches = 0
    for comp, units in sorted(pending.items()):
        for i in range(0, len(units), size):
            chunk = units[i:i + size]
            batch = {"schema": 1, "prompt_version": review_prompt.PROMPT_VERSION, "lang": lang, "component": comp,
                     "units": chunk}
            batch["batch_sha"] = batch_digest(batch)
            dump(os.path.join(out, "batches", f"{comp}-{i // size + 1:03d}.json"), batch)
            n_batches += 1
    print(f"plan {lang}: {len(decided)} decided without a model "
          f"({sum(1 for d in decided if d['verdict'] == 'needs-human')} needs-human), "
          f"{sum(len(v) for v in pending.values())} unit(s) in {n_batches} batch(es) for the model")


def scan(args):
    doc = load_json(os.path.join(args.out, "units.json"))
    if doc.get("scans") == "done":
        print("scans already done for this export")
        return 0
    scan_and_plan(args.out, args.till_source, args.repo)
    return 0


# --- the model step ----------------------------------------------------------

def prompt(args):
    system, user = review_prompt.render(load_json(args.batch))
    sys.stdout.write(f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{user}\n")
    return 0


def review(args):
    key = secret(ANTHROPIC_KEYCHAIN, "ANTHROPIC_API_KEY")
    adapter = AnthropicAdapter(key, model=args.model, base_url=args.api_base)
    failures = 0
    for path in sorted(glob.glob(os.path.join(args.out, "batches", "*.json"))):
        dest = os.path.join(args.out, "verdicts", os.path.basename(path))
        if os.path.exists(dest) and not args.force:
            continue
        batch = load_json(path)
        system, user = review_prompt.render(batch)
        try:
            text, model = adapter.complete(system, user)
            reply = review_model.extract_json(text)
        except (review_model.ModelError, ValueError) as e:
            failures += 1
            print(f"review {os.path.basename(path)}: no verdicts ({e})", file=sys.stderr)
            continue
        if not isinstance(reply, dict):
            failures += 1
            print(f"review {os.path.basename(path)}: reply is not a JSON object", file=sys.stderr)
            continue
        reply["model"] = model
        dump(dest, reply)
        got, errs = review_model.parse_verdicts(reply, batch)
        print(f"review {os.path.basename(path)}: {len(got)}/{len(batch['units'])} verdicts from {model}"
              + (f", {len(errs)} rejected" if errs else ""))
    return 1 if failures else 0


# --- apply --------------------------------------------------------------------

class Writer:
    def __init__(self, client, interval, max_writes, sleep=time.sleep):
        self.c, self.interval, self.max_writes, self.sleep = client, interval, max_writes, sleep
        self.count, self._last, self.streak = 0, 0.0, 0

    def left(self):
        return self.max_writes is None or self.count < self.max_writes

    def call(self, method, path, body):
        """A write: throttled, counted, 404 raises."""
        return self._send(method, path, body, write=True)

    def fetch(self, path):
        """A GET with the same 429 handling (a full language is thousands
        of re-fetches), not throttled or counted; None on 404."""
        return self._send("GET", path, None, write=False)

    def _send(self, method, path, body, write):
        for _ in range(20):
            if write:
                wait = self._last + self.interval - time.monotonic()
                if wait > 0:
                    self.sleep(wait)
                self._last = time.monotonic()
            try:
                status, out = self.c.request(method, path, body)
            except WeblateError as e:
                if "HTTP 429" in str(e):
                    m = re.search(r"(\d+)\s*second", str(e))
                    self.sleep(min(int(m.group(1)) if m else 30, 300))
                    continue
                self.streak += write
                raise
            if status == 404:
                if not write:
                    return None
                self.streak += 1
                raise WeblateError(f"{method} {path}: 404")
            if write:
                self.count += 1
                self.streak = 0
            return out
        self.streak += write
        raise WeblateError(f"{method} {path}: throttled for too long")


def run_refusals(args, accounts, token, models):
    if not args.write:
        return []
    why = []
    if not token:
        why.append("no Weblate reviewer token (WEBLATE_REVIEWER_TOKEN / Keychain weblate-claude-reviewer)")
    acc = accounts.get(args.account)
    if args.account == BOOTSTRAP:
        why.append(f"{BOOTSTRAP} is the bootstrap approver, not an AI reviewer")
    elif acc is None:
        why.append(f"account {args.account} is not registered in config/ai-accounts.json (ut-docs#4366)")
    elif acc.get("role") != "reviewer":
        why.append(f"account {args.account} is not a reviewer (role {acc.get('role')!r})")
    if acc is not None:
        fam = review_checks.family(acc.get("model"))
        for m in sorted(models):
            if not m or not review_checks.family(m):
                why.append("a verdict file names no model: the reviewer can't be identified")
            elif review_checks.family(m) != fam:
                why.append(f"verdicts by {m} can't be recorded under {args.account} (model family "
                           f"{fam}, not {review_checks.family(m)})")
    return why


def final_verdict(u, v):
    """-> (verdict, reasons, correction, note) or None (no verdict)."""
    bad = review_checks.failed(u["checks"])
    if u.get("needs_human"):
        return "needs-human", ["legal"] + bad, None, u["needs_human"]
    if bad:
        corr = v["correction"] if v and v["verdict"] == "needs-fix" else None
        return "needs-fix", bad + [r for r in (v["reasons"] if v else []) if r not in bad], corr, v["note"] if v else ""
    if v is None:
        return None
    if u["checks"].get("scans") != "pass" and v["verdict"] == "approve":
        return "needs-fix", ["scans_not_run"], None, ""
    return v["verdict"], v["reasons"], v["correction"], v["note"]


def correction_problem(u, corr, glossary, script=None):
    if not corr:
        return None
    if corr == u["target"]:
        return "correction equals the current text"
    m = {"flags": u.get("flags") or ""}
    checks = review_checks.check_unit({**u, "target": corr}, u["lang"], m, glossary, u.get("plural_number"), script)
    bad = review_checks.failed(checks)
    return f"correction dropped: fails {', '.join(bad)}" if bad else None


def apply(args):
    out = args.out
    doc = load_json(os.path.join(out, "units.json"))
    lang = doc["lang"]
    accounts = load_accounts(args.accounts)
    glossary = load_glossary(args.glossary)
    decided = load_json(os.path.join(out, "decided.json"))["units"] if os.path.exists(
        os.path.join(out, "decided.json")) else []
    work, models, file_errors = [], set(), []
    for u in decided:
        work.append((u, None, None, True))
    for path in sorted(glob.glob(os.path.join(out, "batches", "*.json"))):
        batch = load_json(path)
        vpath = os.path.join(args.verdicts or os.path.join(out, "verdicts"), os.path.basename(path))
        got, model = {}, None
        intact = batch_digest(batch) == batch.get("batch_sha")
        if not intact:
            file_errors.append(f"{os.path.basename(path)}: batch content does not match its batch_sha "
                               "(edited after export): no verdicts used")
        if intact and os.path.exists(vpath):
            vdoc = load_json(vpath)
            got, errs = review_model.parse_verdicts(vdoc, batch)
            file_errors += [f"{os.path.basename(vpath)}: {e}" for e in errs]
            if got:
                model = vdoc.get("model") if isinstance(vdoc, dict) else None
                models.add(model or "")
        for u in batch["units"]:
            work.append((u, got.get(u["id"]), model, intact))
    token = secret(REVIEWER_KEYCHAIN, "WEBLATE_REVIEWER_TOKEN")
    refusals = run_refusals(args, accounts, token, models)
    client = WeblateClient(os.environ.get("WEBLATE_URL") or WEBLATE_URL, token) if token else None
    writer = Writer(client, args.write_interval, args.max_writes) if client else None
    done = set() if args.re_review else prior_written(args.logs_root)
    acc_family = review_checks.family((accounts.get(args.account) or {}).get("model"))
    script = review_checks.script_for(lang, args.repo)
    run_model =sorted(m for m in models if m)[0] if [m for m in models if m] else DETERMINISTIC
    lines, errors, stopped = [], 0, False
    for u, v, model, intact in work:
        fv = final_verdict(u, v) if intact else None
        verdict, reasons, corr, note = fv if fv else (None, [], None, "")
        line = {"ts": now(), "runner_version": RUNNER_VERSION, "prompt_version": review_prompt.PROMPT_VERSION,
                "prompt_sha256": review_prompt.prompt_sha256(lang), "reviewer_model": model or run_model,
                "account": args.account, "lang": lang, "component": u["component"], "unit": u["id"],
                "key": u["key"], "src_sha": u["src_sha"], "tgt_sha": u["tgt_sha"], "checks": u["checks"],
                "verdict": verdict, "reasons": reasons, "correction": None, "note": note, "action": "none",
                "planned": None, "written": False, "error": None}
        problem = correction_problem(u, corr, glossary, script) if verdict == "needs-fix" else None
        if problem:
            line["error"] = problem
        elif verdict == "needs-fix":
            line["correction"] = corr
        if verdict == "approve":
            line["planned"] = "approve"
        elif verdict == "needs-fix":
            line["planned"] = "suggest" if line["correction"] else "comment"
        elif verdict == "needs-human":
            line["planned"] = "comment" if args.comment_needs_human else None
        lines.append(line)
        if not intact:
            line["error"] = "batch edited after export: nothing done"
            continue
        if refusals:
            line["action"], line["error"] = "refused", "; ".join(refusals)
            continue
        if verdict is None or line["planned"] is None:
            continue
        if audit_key(u["id"], u["src_sha"], u["tgt_sha"]) in done:
            line["error"] = "already written by an earlier run"
            continue
        if stopped or (writer and not writer.left()):
            line["error"] = "stopped before this unit (max writes or repeated failures)"
            continue
        if client is not None:
            try:
                fresh = writer.fetch(f"/api/units/{u['id']}/")
            except WeblateError as e:
                line["error"] = f"re-fetch failed: {e}"
                errors += 1
                continue
            if not fresh or sha(list(fresh.get("source") or [])) != u["src_sha"] or \
                    sha(list(fresh.get("target") or [])) != u["tgt_sha"]:
                line["action"] = "stale"
                continue
        if not args.write:
            continue
        if verdict == "approve" and not u.get("author"):
            # Approving would make the reviewer the recorded author AND
            # approver of the text (self_approved at the publish gate).
            line["action"], line["error"] = "refused", ("no recorded author for the current text in Weblate's "
                                                        "history: the publish gate would omit it (no_history)")
            continue
        if u.get("author") == args.account:
            line["action"], line["error"] = "refused", "the reviewer account wrote this text"
            continue
        if review_checks.family(model_of(u.get("author"), accounts)) == acc_family:
            line["action"], line["error"] = "refused", f"author {u.get('author')} is of the reviewer's model family"
            continue
        try:
            line["action"] = write_unit(writer, u, fresh, line, args)
            line["written"] = True
            done.add(audit_key(u["id"], u["src_sha"], u["tgt_sha"]))
        except WeblateError as e:
            line["error"] = (line["error"] + "; " if line["error"] else "") + str(e)[:300]
            errors += 1
            if writer.streak >= MAX_CONSECUTIVE_FAILURES:
                stopped = True
    for e in file_errors:
        print(f"verdicts: {e}", file=sys.stderr)
    with open(os.path.join(out, "audit.jsonl"), "a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    summary = summarise(lang, lines, args, refusals, file_errors)
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)
    print(summary)
    for r in refusals:
        print(f"refused: {r}", file=sys.stderr)
    if refusals:
        return 2
    return 1 if errors else 0


def comment_text(line):
    reasons = ", ".join(line["reasons"]) or "-"
    text = (f"AI review ({line['reviewer_model']}, prompt {line['prompt_version']}): {line['verdict']} — "
            f"{reasons}.")
    if line.get("note"):
        text += f" {line['note']}"
    return text[:1000]


def write_unit(writer, u, fresh, line, args):
    uid = u["id"]
    path = f"/api/units/{uid}/"
    if line["verdict"] == "approve":
        target = list(fresh.get("target") or u["target"])
        if (fresh.get("state") or 0) >= APPROVED:
            writer.call("PATCH", path, {"target": target, "state": TRANSLATED})
            try:
                writer.call("PATCH", path, {"target": target, "state": APPROVED})
            except WeblateError as e:
                try:  # never leave a unit un-approved after a run
                    writer.call("PATCH", path, {"target": target, "state": APPROVED})
                except WeblateError as e2:
                    raise WeblateError(f"approve failed AND the restore failed: unit {uid} is left translated, "
                                       f"not approved: {e2}") from None
                raise WeblateError(f"approve failed, unit restored to approved: {e}") from None
        else:
            writer.call("PATCH", path, {"target": target, "state": APPROVED})
        return "approved"
    if line["verdict"] == "needs-fix":
        action = "commented"
        if line["correction"]:
            writer.call("POST", f"{path}suggestions/", {"target": line["correction"]})
            action = "suggested"
        writer.call("POST", f"{path}comments/", {"comment": comment_text(line), "scope": "translation"})
        return action
    writer.call("POST", f"{path}comments/", {"comment": comment_text(line), "scope": "translation"})
    return "commented"


def per_language(lang, lines):
    """One line: approved / needs-fix / needs-human / stale / refused."""
    def n(pred):
        return sum(1 for x in lines if pred(x))
    return (f"{lang}: approved {n(lambda x: x['action'] == 'approved')} "
            f"(approve verdicts {n(lambda x: x['verdict'] == 'approve')}), "
            f"needs-fix {n(lambda x: x['verdict'] == 'needs-fix')}, "
            f"needs-human {n(lambda x: x['verdict'] == 'needs-human')}, "
            f"no verdict {n(lambda x: x['verdict'] is None)}, "
            f"stale {n(lambda x: x['action'] == 'stale')}, refused {n(lambda x: x['action'] == 'refused')}")


def summarise(lang, lines, args, refusals, file_errors):
    from collections import Counter
    verdicts = Counter(line["verdict"] or "no verdict" for line in lines)
    actions = Counter(line["action"] for line in lines)
    rows = [f"## AI review: {lang} ({'write' if args.write else 'dry run'})", "", per_language(lang, lines), "",
            f"- account `{args.account}`, prompt {review_prompt.PROMPT_VERSION}, runner {RUNNER_VERSION}",
            f"- {len(lines)} unit(s); written {sum(1 for x in lines if x['written'])}"]
    if refusals:
        rows.append("- **refused, nothing written:** " + "; ".join(refusals))
    if file_errors:
        rows.append(f"- {len(file_errors)} verdict(s) rejected (see the log)")
    rows += ["", "| verdict | units |", "|---|---|"] + [f"| {k} | {n} |" for k, n in sorted(verdicts.items())]
    rows += ["", "| action | units |", "|---|---|"] + [f"| {k} | {n} |" for k, n in sorted(actions.items())]
    return "\n".join(rows) + "\n"


# --- CLI --------------------------------------------------------------------------

def parse(argv):
    ap = argparse.ArgumentParser(description="AI review of Weblate translations (ut-docs#5420).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--repo", default=REPO)
        p.add_argument("--accounts", help="default: <repo>/config/ai-accounts.json")
        p.add_argument("--glossary", help="default: <repo>/config/review-glossary.json")
        p.add_argument("--logs-root", help="where earlier audits live (default: <repo>/.review-logs)")

    e = sub.add_parser("export")
    common(e)
    e.add_argument("--lang", required=True, help="Weblate language code, e.g. fr, ms, zh_Hans")
    e.add_argument("--component", action="append", help="only this component (repeatable)")
    e.add_argument("--limit", type=int)
    e.add_argument("--batch-size", type=int, default=40)
    e.add_argument("--include-reviewed-by", action="append", metavar="ACCOUNT",
                   help="re-review units this AI reviewer already approved (repeatable)")
    e.add_argument("--re-review", action="store_true", help="ignore earlier audits")
    e.add_argument("--out")
    g = e.add_mutually_exclusive_group(required=True)
    g.add_argument("--till-source", help="universal-till checkout holding meta/source.json's commit (scans)")
    g.add_argument("--no-scans", action="store_true", help="stop after units.json; run `scan` later")

    s = sub.add_parser("scan")
    common(s)
    s.add_argument("--out", required=True)
    s.add_argument("--till-source", required=True)

    p = sub.add_parser("prompt")
    p.add_argument("batch")

    r = sub.add_parser("review")
    r.add_argument("--out", required=True)
    r.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL") or review_model.DEFAULT_MODEL)
    r.add_argument("--api-base", default="https://api.anthropic.com")
    r.add_argument("--force", action="store_true", help="redo batches that already have a verdict file")

    a = sub.add_parser("apply")
    common(a)
    a.add_argument("--out", required=True)
    a.add_argument("--account", default=DEFAULT_ACCOUNT)
    a.add_argument("--verdicts", metavar="DIR",
                   help="verdict files (one per batch, same file name); default <out>/verdicts")
    a.add_argument("--write", action="store_true", help="write to Weblate (default: dry run)")
    a.add_argument("--re-review", action="store_true")
    a.add_argument("--max-writes", type=int)
    a.add_argument("--write-interval", type=float, default=0.5)
    a.add_argument("--comment-needs-human", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(args, "repo"):
        args.accounts = args.accounts or os.path.join(args.repo, "config", "ai-accounts.json")
        args.glossary = args.glossary or os.path.join(args.repo, "config", "review-glossary.json")
        args.logs_root = args.logs_root or os.path.join(args.repo, ".review-logs")
    if args.cmd == "export" and not args.out:
        args.out = os.path.join(args.logs_root, f"{dt.date.today():%Y-%m-%d}-{args.lang}")
    if getattr(args, "batch_size", 1) < 1:
        ap.error("--batch-size must be at least 1")
    return args


def main(argv=None):
    args = parse(argv)
    try:
        return {"export": export, "scan": scan, "prompt": prompt, "review": review, "apply": apply}[args.cmd](args)
    except review_model.ModelError as e:
        print(f"review: {e}", file=sys.stderr)
        return 2
    except (RunnerError, WeblateError, build_bundle.BundleError, OSError, ValueError, KeyError) as e:
        print(f"{args.cmd} failed: {e}", file=sys.stderr)
        return 2 if isinstance(e, RunnerError) else 1


if __name__ == "__main__":
    sys.exit(main())
