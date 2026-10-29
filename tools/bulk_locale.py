#!/usr/bin/env python3
"""Translate and approve one Weblate language in bulk (ut-docs#4371).

An owner-run, build-time tool (ADR-0160 §10: never a runtime dependency).

  translate   every empty string of the language -> a local Ollama model ->
              saved as "translated" by the translator bot. A result that
              changes a placeholder, tag or link, or is in the wrong script,
              is NOT saved (tools/bundle_checks.check_string); it is listed
              in the report for a human.
  approve     strings waiting in state "translated" -> approved by the
              approver bot (a different account from the translator, as the
              bundle build requires). `review: legal` strings are never
              approved here: a human-reviewers member does that.
              --include-approved also re-approves strings Weblate already
              shows as approved but that carry no approval event (the
              imported ones): reset to translated, then approved.
  all         translate, then approve.

Tokens are read from the macOS Keychain (services below) or from the
environment; they are never printed or put on a command line. Weblate's API
throttles per account: a 429 is waited out and retried, so a big language
takes a while but finishes.

  python3 tools/bulk_locale.py all --locale fr --ollama-url http://192.168.1.118:11434 \\
      --ollama-model qwen3.8:27b
  python3 tools/bulk_locale.py approve --locale fa --include-approved

--dry-run reads and reports; it writes nothing to Weblate.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bundle_checks  # noqa: E402
from provision_weblate import WeblateClient, WeblateError  # noqa: E402

PROJECT = "universal-till"
WEBLATE_URL = "https://translate.universaltill.com"
TRANSLATED, APPROVED, READONLY = 20, 30, 100
TRANSLATOR_KEYCHAIN = "weblate-translator"
APPROVER_KEYCHAIN = "weblate-bootstrap-approver"
SKIP_COMPONENTS = {"glossary"}
MAX_CONSECUTIVE_FAILURES = 8

PERSONA = ("You are an experienced software localiser translating the user interface of "
           "Universal Till, a point-of-sale (till) system used by cashiers and shop managers "
           "in cafes, restaurants and retail shops.")
STYLE = ("Short, clear UI text as used on a touchscreen till. Use the standard retail/POS terms "
         "of the target language (receipt, refund, void, tender, cash drawer). Keep every "
         "placeholder (%s, %d, {name}, {{.X}}) and HTML tag exactly as in the source. Never add "
         "explanations or quotes; return only the translation. Keep the length close to the English.")
LANGUAGE_NOTES = {
    "de": "Address the user formally with 'Sie'. Retail terms: Kasse, Bon/Beleg, Storno, Rückgabe, Schublade, Artikel, Warengruppe.",
    "es": "Use 'usted' (formal), neutral Spanish for Spain. Retail terms: caja, ticket/recibo, devolución, anulación, cajón portamonedas, artículo, categoría.",
    "fr": "Use 'vous' (formal). Retail terms: caisse, ticket de caisse, remboursement, annulation, tiroir-caisse, article, catégorie. Put a non-breaking space before : ; ! ? as French typography requires.",
    "pt": "European Portuguese (Portugal), not Brazilian. Prefer the formal impersonal form. Retail terms: caixa, talão/recibo, devolução, anulação, gaveta, artigo, categoria.",
    "tr": "Address the user formally with 'siz'; use 'Lütfen' for requests. Retail terms: kasa, fiş, iade, iptal, ürün, kategori. Keep Turkish characters correct.",
    "ar": "Modern Standard Arabic, right-to-left, formal address. Use Western digits (0-9) to match the till.",
    "fa": "Persian (Iran), right-to-left, formal. Use the zero-width non-joiner correctly. Use Western digits (0-9) to match the till.",
    "ms": "Standard Bahasa Malaysia (not Indonesian). Use 'anda'. Keep common English tech words (Bluetooth, Wi-Fi, PIN) unchanged.",
    "zh_Hans": "Simplified Chinese for mainland China. Concise imperative UI phrasing, full-width Chinese punctuation; keep placeholders and HTML unchanged.",
}
SCRIPTS = {"ar": "Arab", "fa": "Arab", "zh_Hans": "Hans"}


def secret(service, env_var):
    """A token from the environment, else the macOS Keychain. Never printed."""
    v = os.environ.get(env_var, "").strip()
    if v:
        return v
    try:
        return subprocess.check_output(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", service, "-w"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit(f"no token: set {env_var} or store one in the Keychain service '{service}'")


def is_plural(unit):
    return len(unit.get("source") or []) != 1


def labels(unit):
    out = set()
    for lab in unit.get("labels") or []:
        out.add(lab.get("name") if isinstance(lab, dict) else lab)
    return out


def is_legal(unit):
    return "legal" in labels(unit)


def needs_translation(unit):
    """An empty, not-yet-translated, single-form string."""
    return ((unit.get("state") or 0) < TRANSLATED and not is_plural(unit)
            and not any((unit.get("target") or [""])))


def needs_approval(unit, include_approved=False):
    state = unit.get("state") or 0
    if is_legal(unit) or is_plural(unit) or not any(unit.get("target") or []):
        return False
    if state == TRANSLATED:
        return True
    return include_approved and APPROVED <= state < READONLY


def messages(locale, unit):
    english = unit["source"][0]
    key = unit.get("context") or ""
    system = f"{PERSONA}\n{STYLE}\n{LANGUAGE_NOTES.get(locale, '')}".strip()
    user = (f"Translate this UI string into the language with code '{locale}'. "
            f"Where it appears (key): {key}\nReturn only the translation.\n\n{english}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class Ollama:
    def __init__(self, url, model, token=None, timeout=300):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self.token = token

    def chat(self, msgs):
        body = json.dumps({"model": self.model, "stream": False, "think": False,
                           "options": {"temperature": 0.2}, "messages": msgs}).encode()
        req = urllib.request.Request(self.url + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["message"]["content"].strip()


def clean(text):
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'«»“”":
        text = text[1:-1].strip()
    return text


def looks_like_chatter(english, text):
    """A model that explains instead of translating: reasoning tags, extra
    lines, or an answer several times longer than the English."""
    if "<think" in text or "</think" in text:
        return "reasoning text in the answer"
    if "\n" in text.strip() and "\n" not in english:
        return "more than one line for a one-line string"
    if len(text) > 4 * len(english) + 40:
        return "answer far longer than the English"
    low = text.lower()
    if any(low.startswith(p) for p in ("okay", "sure,", "here is", "here's", "the user", "translation:")):
        return "the model is talking, not translating"
    return None


def translate_one(chat, locale, unit, script):
    """-> (text, None) when it may be saved, else (None, reason)."""
    english = unit["source"][0]
    why = None
    for attempt in range(2):
        msgs = messages(locale, unit)
        if attempt:
            msgs[-1]["content"] += (f"\n\nYour previous answer was rejected ({why}). Keep every "
                                    "placeholder, tag and link exactly as in the English.")
        try:
            text = clean(chat(msgs))
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            return None, f"model error: {e}"
        chatter = looks_like_chatter(english, text)
        bad = ("chatter", chatter) if chatter else bundle_checks.check_string(
            english, text, {"flags": unit.get("flags") or ""}, script)
        if bad is None and script != "Latn" and text == english and bundle_checks.scripts_in(english):
            bad = ("untranslated", "the model returned the English text")
        if bad is None:
            return text, None
        why = f"{bad[0]}: {bad[1]}"
    return None, why


def patch(client, unit_id, body):
    for _ in range(90):
        try:
            return client.request("PATCH", f"/api/units/{unit_id}/", body)
        except WeblateError as e:
            if "429" in str(e):
                time.sleep(60)
                continue
            raise
    raise WeblateError("throttled for too long")


def units_of(client, component, locale, query):
    path = f"/api/translations/{PROJECT}/{component}/{locale}/units/?page_size=1000"
    if query:
        path += "&q=" + query
    return client.list(path) or []


def components(client):
    return [c["slug"] for c in client.list(f"/api/projects/{PROJECT}/components/")
            if c["slug"] not in SKIP_COMPONENTS]


class Run:
    def __init__(self, args, translator, approver, chat):
        self.a, self.translator, self.approver, self.chat = args, translator, approver, chat
        self.script = args.script or SCRIPTS.get(args.locale, "Latn")
        self.stats = {"translated": 0, "approved": 0, "rejected": 0, "skipped_legal": 0,
                      "skipped_plural": 0, "failed": 0}
        self.rejected, self.failed, self._streak = [], [], 0

    def left(self):
        return self.a.limit is None or (self.stats["translated"] + self.stats["approved"]) < self.a.limit

    def write(self, client, unit, body, what):
        try:
            patch(client, unit["id"], body)
            self._streak = 0
            return True
        except WeblateError as e:
            self.stats["failed"] += 1
            self._streak += 1
            self.failed.append({"unit": unit["id"], "key": unit.get("context"), "what": what, "error": str(e)[:160]})
            if self._streak >= MAX_CONSECUTIVE_FAILURES:
                self.finish()
                sys.exit(f"stopping: {MAX_CONSECUTIVE_FAILURES} writes failed in a row, see the report")
            return False

    def translate(self, client):
        for comp in components(client):
            for u in units_of(client, comp, self.a.locale, "state%3A%3Ctranslated"):
                if not self.left():
                    return
                if is_plural(u):
                    self.stats["skipped_plural"] += 1
                    continue
                if not needs_translation(u):
                    continue
                text, why = translate_one(self.chat, self.a.locale, u, self.script)
                if text is None:
                    self.stats["rejected"] += 1
                    self.rejected.append({"unit": u["id"], "key": u.get("context"), "why": why})
                    continue
                if self.a.dry_run or self.write(self.translator, u, {"target": [text], "state": TRANSLATED}, "translate"):
                    self.stats["translated"] += 1
            print(f"translate {comp}: {self.stats['translated']} saved, {self.stats['rejected']} rejected", flush=True)

    def approve(self, client):
        for comp in components(client):
            for u in units_of(client, comp, self.a.locale, ""):
                if not self.left():
                    return
                if is_legal(u) and ((u.get("state") or 0) >= TRANSLATED):
                    self.stats["skipped_legal"] += 1
                    continue
                if not needs_approval(u, self.a.include_approved):
                    continue
                if self.a.dry_run:
                    self.stats["approved"] += 1
                    continue
                target = u["target"]
                if (u.get("state") or 0) >= APPROVED and not self.write(
                        self.approver, u, {"target": target, "state": TRANSLATED}, "reset"):
                    continue
                if self.write(self.approver, u, {"target": target, "state": APPROVED}, "approve"):
                    self.stats["approved"] += 1
            print(f"approve {comp}: {self.stats['approved']} approved", flush=True)

    def finish(self):
        path = self.a.report or os.path.join(tempfile.gettempdir(), f"bulk-{self.a.locale}-report.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"locale": self.a.locale, "stats": self.stats, "rejected": self.rejected,
                       "failed": self.failed}, f, ensure_ascii=False, indent=2)
        print("summary:", json.dumps(self.stats), "\nreport:", path)


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=["translate", "approve", "all"])
    p.add_argument("--locale", required=True, help="Weblate language code, e.g. fr, es, zh_Hans")
    p.add_argument("--weblate-url", default=os.environ.get("WEBLATE_URL", WEBLATE_URL))
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://192.168.1.118:11434"))
    p.add_argument("--ollama-model", default=os.environ.get("OLLAMA_MODEL", "qwen3.8:27b"))
    p.add_argument("--script", help="ISO 15924 script of the language (default from the locale)")
    p.add_argument("--limit", type=int, help="stop after this many strings (a trial run)")
    p.add_argument("--include-approved", action="store_true",
                   help="also re-approve strings shown approved but with no approval event (imports)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report", help="where to write the JSON report")
    return p.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    doing_translate = a.command in ("translate", "all")
    doing_approve = a.command in ("approve", "all")
    reader = WeblateClient(a.weblate_url, secret(APPROVER_KEYCHAIN, "WEBLATE_APPROVER_TOKEN"))
    translator = WeblateClient(a.weblate_url, secret(TRANSLATOR_KEYCHAIN, "WEBLATE_TRANSLATOR_TOKEN")) \
        if doing_translate else None
    chat = Ollama(a.ollama_url, a.ollama_model, os.environ.get("OLLAMA_API_TOKEN")).chat if doing_translate else None
    run = Run(a, translator, reader, chat)
    if doing_translate:
        run.translate(reader)
    if doing_approve:
        run.approve(reader)
    run.finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
