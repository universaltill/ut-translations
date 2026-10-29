# Review: bulk translate-and-approve tool (ut-docs#4371)

Scope: `tools/bulk_locale.py` (translate empty strings with a local Ollama
model, approve waiting ones), `scripts/bulk-locales.sh` (runs it in a Docker
Desktop container, one language after another), `tests/test_bulk_locale.py`,
and the translator bot entry in `config/ai-accounts.json`.

Rules kept: the translator and the approver are different Weblate accounts;
legal keys are never approved by the tool; a model answer that changes a
placeholder, tag or link, is in the wrong script, echoes the English for a
non-Latin language, or is chatter (reasoning text, extra lines, far too long)
is not saved and is listed in the report; plural strings are left for a human.

Found while testing against the real NAS model: it answered with its own
reasoning ("Okay, the user wants..."), which the repo's placeholder checks
alone would have saved as a translation. Fixed with the chatter guard and
tests. The NAS CPU model also runs at about 1.5 tokens/s: too slow for bulk,
so translation is meant for the owner's GPU Ollama.

Exposure: tokens come from the Keychain / Key Vault and reach the container as
inherited environment variables (visible to the local Docker user only), never
on a command line or in a file; the repo is mounted read-only. The approver
bot is the bootstrap account from PR 9 (owner-directed, not an AI review).

Verified: 179 unit tests pass; the runner started in Docker and approved a
real component. Verdict: safe to merge. Deferred: add fr, zh_Hans and ms to
`config/bundles.json` (and the workflow choice list) before they can publish.

## Addendum: re-authoring imported strings
The first German build showed 197 `self_approved` strings (the whole
`receipts-and-legal` component): their history had no text event before the
approval, so the build read the approver as the author. A reset alone did not
help; the text itself must change. `--include-approved` now has the
translator bot (Weblate Reviewers role) save the text with a marker appended
and then restore it, which makes it the recorded author; the approver then
approves. Verified on a real unit: verdict None. Added `--component`.
