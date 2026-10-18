# ut-translations

The Git backing of the Universal Till translation workbench — the
self-hosted Weblate at `translate.universaltill.com`
([ADR-0160](https://github.com/universaltill/ut-docs/blob/main/adr/0160-language-service-weblate-signed-bundles.md)).

English is **not** written here. It lives in
[`universal-till`](https://github.com/universaltill/universal-till)
(`web/locales/en.json`) together with each key's metadata
(`web/i18n-meta/en.meta.json`, `areas.json`), and a sync job copies it in.
Translations are written here by Weblate. Nothing in this repo ever writes
to `universal-till`.

## Flow

```
universal-till main ──(sync job, this repo)──▶ <area>/en.json + meta/
   en.json, en.meta.json,                            │ webhook: Weblate pulls
   areas.json, release tags                          ▼
                                     Weblate: one component per area
                                     translators + AI save, reviewers approve
                                                     │ Weblate commits + pushes
                                                     ▼
                                     <area>/<code>.json (this repo, main)
                                                     │ publish workflow (ut-docs#4367)
                                                     ▼
                                     signed com.universaltill.language-<code>
                                     bundle in the marketplace → tills
```

## Layout

| Path | Written by | What |
|---|---|---|
| `<area>/en.json` | sync job | Flat `key → English` for that screen area: the Weblate component's template. |
| `<area>/<code>.json` | Weblate | Translations, flat `key → text`, in the same format. |
| `meta/<area>.json` | sync job | Per key: `explanation`, `flags`, `labels`, `lines` (which till lines use it). Read by the provisioning script; kept outside the area folders so Weblate's `<area>/*.json` mask never sees it as a language. |
| `meta/areas.json` | sync job | Component name, description and key count per area. |
| `meta/source.json` | sync job | The universal-till commits the current files came from (rewritten only together with a real change). |
| `config/lines.json` | people | The source repo and the **supported till lines** (below). |
| `config/bundles.json` | people | Locales a bundle may be built for: Weblate code, name, script, `human_spot_check`, `min_coverage`. |
| `config/ai-accounts.json` | people | Weblate AI service accounts and the model each is tied to (ut-docs#4366 fills it in). |
| `tools/` | — | `sync_english.py`, `provision_weblate.py`, `i18n_map.py` (the shared mapping), `import_existing.py` (the one-off import below), and the bundle tools `build_bundle.py`, `bundle_checks.py`, `till_verify.py` (+ `till_harness/`), `publish_bundle.py`. Python 3 standard library only. |
| `tests/` | — | Unit tests; run in CI by `.github/workflows/test.yml`. |

### Areas (components)

The areas are universal-till's `web/i18n-meta/areas.json`: each namespace
belongs to exactly one area, a key's own `area` overrides it, and anything
else goes to `common`. An area is a **permission boundary**: a page-scoped
invite limits a Weblate team to those components (ADR-0160 §3).

| Component | Keys (main @ `725ee6bad`, line 0.34 @ `v0.34.7`) |
|---|---|
| `sale` | 255 |
| `catalog` | 828 |
| `orders` | 133 |
| `reports` | 251 |
| `staff` | 265 |
| `settings` | 685 |
| `tills` | 322 |
| `setup` | 113 |
| `selforder` (Self-order) | 42 |
| `receipts-and-legal` | 197 (all labelled `legal`) |
| `plugins` | 274 |
| `support` | 76 |
| `common` | 28 |
| **total** | **3,469** |

### Metadata → Weblate

| universal-till metadata | Weblate |
|---|---|
| `description` | the source string's **explanation** |
| `placeholders` (one meaning per argument) | appended to the explanation (`- %s: the barcode …`) |
| tokens in the English text: `%s`, `%[1]d`, `{{.X}}`, `{0}`, `{name}` | flag `placeholders:"%s":"{name}"` (enforced check) |
| `max_length` | flag `max-length:N` (enforced check) |
| `plural`, or ICU `plural`/`select`/`number` in the text | flag `icu-message-format` (enforced; the brace syntax is then checked by the ICU check, not listed as placeholders) |
| `review: legal`, or any key in a `legal_namespaces` namespace without a reviewed `review_exception` (deny by default) | label **`legal`** — the publish step (ut-docs#4367) needs a `human-reviewers` approval for these |
| a key only an older supported line uses | a note in the explanation |

## Supported till lines (ADR-0160 §5)

One language bundle serves every supported till line, so the components
hold the **union** of keys from `main` and from the newest final release
tag (`vMAJOR.MINOR.N`, no `-rc`) of each line in
`config/lines.json` → `supported_lines`. A key stays translatable while any
of them uses it; removing a line drops the keys only it used at the next
sync. `main`'s English and metadata win where they differ.

There is no written support-window policy yet, so the list is maintained by
hand (today: `["0.34"]`). Add a line when it ships to shops, remove it when
it is no longer supported.

## Running

Unit tests (no network; Weblate and the marketplace are fake servers on
127.0.0.1). The bundle tests also need Go and a universal-till checkout —
`$UTT_TILL_SOURCE`, else `../universal-till` — for its real guards,
installer and locale loader; without one they skip, and CI sets
`UTT_REQUIRE_TILL=1` so they never skip there:

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -v
```

Sync (reads universal-till's public clone, or a local one with `--source`):

```sh
python3 tools/sync_english.py                       # writes + commits when something changed
python3 tools/sync_english.py --no-commit           # writes only
python3 tools/sync_english.py --source ../universal-till --no-commit
```

In CI this is `.github/workflows/sync-english.yml`: hourly, by hand, or on a
`repository_dispatch` of type `universal-till-main`. It commits as
`github-actions[bot]` and pushes with the job's own `GITHUB_TOKEN`
(`contents: write` on this repo only).

Provision Weblate (idempotent; a second run makes no write call):

```sh
export WEBLATE_URL=https://translate.universaltill.com
read -rs WEBLATE_TOKEN && export WEBLATE_TOKEN   # a project-admin API token; never commit it
python3 tools/provision_weblate.py --dry-run     # read only: prints the plan
python3 tools/provision_weblate.py
```

It creates or updates:
- project `universal-till` with **dedicated reviewers** on;
- the `legal` label;
- one component per area — monolingual JSON (`file_format: json`),
  template `<area>/en.json`, files `<area>/*.json`, source language English,
  English read-only in Weblate, enforced checks `placeholders`,
  `max-length`, `icu_message_format`, rebase merge, push on commit, and the
  cleanup add-on. `common` owns the clone; the others link to it
  (`weblate://universal-till/common`);
- each English unit's explanation, flags and `legal` label (other labels
  people add are kept).

Weblate imports a new component in the background. Until it has, that
component is reported as `pending`; run the script again afterwards. Run it
again after every sync that changes `meta/` (until a scheduled run exists,
see follow-ups).

Optional env: `WEBLATE_REPO_URL` (pull, default the public https clone),
`WEBLATE_PUSH_URL` (push, default `git@github.com:universaltill/ut-translations.git`).

## Importing the translations we already have (ADR-0160 §9; ut-docs#4371, #4372)

`tools/import_existing.py` pre-fills `<area>/<code>.json` for every
translated locale in `config/bundles.json` from what already exists —
`ut-plugin-language-{de,es,pt}` `locales/<code>.json` and universal-till
`web/locales/{tr,ar,fa}.json` — read with `git show <ref>:` (default
`origin/main`) from sibling checkouts, never from their working trees. It
never translates: text is copied byte for byte (placeholders included).

- A key goes to the area whose `<area>/en.json` holds it; keys in no
  `en.json` are stale and dropped; empty values are skipped.
- A value identical to English is skipped unless the pack's reviewed
  `i18n-baseline/<code>.same-as-en.txt` lists the key. Weblate counts any
  non-empty target as translated, so an English copy would look done;
  core's tr/ar/fa have no such list and do hold English copies, so those
  keys stay untranslated in Weblate for a translator.
- A placeholder set that differs from English is imported as is and listed
  in the report (Weblate's enforced check and the bundle check catch it).
- Weblate owns these files afterwards: a file that exists and differs is
  refused (nothing is written) unless `--force`. Re-running over its own
  output changes nothing.

```sh
git -C ../universal-till fetch && git -C ../ut-plugin-language-de fetch   # and es, pt
python3 tools/import_existing.py --dry-run          # coverage report only
python3 tools/import_existing.py --report /tmp/import-report.json
```

## Language bundles (ADR-0160 §4, §5; ut-docs#4367)

`.github/workflows/publish-bundle.yml`, run by hand on `main` for one
locale, builds `com.universaltill.language-<code>`, checks it against the
till, and (with **publish** ticked) uploads and approves it in the
marketplace.

**What ships.** Only strings Weblate holds as *approved*, read from the
Weblate REST API (Weblate can't push to this repo yet; `--text-source git`
switches the text to `<area>/<code>.json` once it can, and a string then
ships only if git and Weblate agree). From each translation's change
history:
- the approval is an explicit *Translation approved* (or a suggestion
  accepted straight into approved) by a **different account** than the one
  that wrote the current text — a reviewer who rewrites and approves in one
  step approved their own text; bulk-approved strings have no approval
  event and are left out;
- the approver is, **deny by default**, an AI account listed in
  `config/ai-accounts.json` or a member of the `human-reviewers` team; any
  other account's approval is left out (`unknown_approver`). A translated
  locale is not built at all while `config/ai-accounts.json` lists no
  account (ut-docs#4366 fills it in);
- when author and approver are both AI accounts they must be on
  **different models** (`config/ai-accounts.json`); a Weblate bot account
  not listed there never counts;
- a **`legal`** key (from `meta/`, or the Weblate label) needs an approval
  from a member of the `human-reviewers` team who is not a bot, given after
  the last English change, against the English in this repo.

**Team membership is read at build time**, not at approval time: adding
someone to `human-reviewers` makes their earlier approvals count from the
next build, and removing someone fails closed — their approvals stop
counting at the next build, and their strings drop out until someone in the
team approves them again.

**`previous_source`.** A unit whose Weblate `previous_source` is set (the
English changed since it was translated) is omitted as `stale_source`, even
when its current approval is newer than the English change. That may be
stricter than needed, depending on when Weblate clears the field; **verify
it on the live instance before the first `de` publish** (approve a string
after an English change, then check the unit's `previous_source` and that
the string ships).

Then per string (`tools/bundle_checks.py`): the placeholder set equals
English (the till's own load-time printf rule plus every other token),
`max-length`, the locale's script, no markup/link/URL dropped, not blank.
**A failing string is omitted, never written as English**; the till shows
its built-in string or English. `report.json` names every omission and why.

**The `en` bundle** ships this repo's `<area>/en.json` as is, so its build
first checks that every `<area>/en.json` and `meta/<area>.json` (English
text, flags, `legal` labels, lines) equals what `tools/i18n_map.py` renders
from universal-till's `web/locales/en.json` + `web/i18n-meta/` at
`meta/source.json`'s commits — `main`, and each supported line's release
tag, whose commit must match. Any difference fails the build: re-run the
sync, never hand-edit English here.

**What fails the whole bundle.** universal-till's
`guard-compliance-claims.sh` (ADR-0040) and `guard-competitor-naming.sh`
(ADR-0106), read from universal-till **at the bundle's `source_commit`**
(`git show <commit>:<path>`) and run over the bundle's strings, a
one-line-per-string view of them (newlines, tags, non-breaking spaces and
Unicode dashes folded, so those can't hide a term) and the manifest, with no
token in their environment. A hit stops the build; a human fixes the string
in Weblate. A publish also stops when the bundle is empty, or below the
locale's `min_coverage` — set for de/es/pt, because their 2.x bundle
replaces the installed pack under the same id until ut-docs#4371 imports it.

**Format.** A `language` plugin package (ADR-0010), deterministic tar.gz:
`manifest.json` (id, version `2.<YYYYMMDD>.<second of day>` of
`generated_at` — above the 1.x packs, rising every build), `bundle.json`
(`locale`, `pos_lines`, `source_commit`, `generated_at`, `key_count`,
`approved_count`, `omitted` by reason) and `locales/<code>.json`.

**Whose code runs.** Every universal-till script or test this runs is taken
from `meta/source.json`'s `main.commit`, which must be on universal-till
`main` (an ancestor of `origin/main` after `git fetch origin main --tags`)
or be a `vX.Y.Z` tag's commit — GitHub serves a fork's commit by sha
through the parent repo. The workflows check that after the checkout, and
`till_verify.require_released_commit` checks it again before any till code
runs. In the workflow, the **build** job (Weblate token) runs no till code
at all (`--no-scans`) and uploads its artifact straight away; the
**verify** job (no environment, no secret) checks the artifact's sha256
against the build job's outputs and runs the guards
(`build_bundle.py --scan-bundle`) and the till verifier; **publish** runs
only after both, and re-checks the bundle's and `report.json`'s sha256
against the build job's outputs (the verify job could have replaced the
artifact). `review.md` is for people only; publish never reads it.

**Checked against the real till.** `tools/till_verify.py` copies
`tools/till_harness/utt_bundle_harness_test.go` into a throwaway copy of
universal-till at `source_commit` (Go's `internal/` rule rules out importing
it) and runs it: the bundle is signed the way the marketplace signs on
approval (ut-cloud `SignBundle`: the ADR-0165 `content_digest` over every
file but `manifest.json`, `content_signature` and `signature` — this repo
never holds a signing key), goes through the till's own
`MarketplaceInstaller.installBundleFile` and loads through
`Manager.syncLocales` as a fresh first-party layer; an unsigned copy, a
changed locale file or `bundle.json`, a changed manifest (even with a
matching checksum), a locale file swapped after signing (even with a
matching checksum: the content digest catches it) and another key's
signature are all refused.

```sh
export WEBLATE_URL=https://translate.universaltill.com
read -rs WEBLATE_TOKEN && export WEBLATE_TOKEN
python3 tools/build_bundle.py --locale de --till-source ../universal-till --out dist
# or, as the workflow does: build without the guards, then scan the bundle
python3 tools/build_bundle.py --locale de --till-source ../universal-till --out dist --no-scans
python3 tools/build_bundle.py --locale de --till-source ../universal-till \
    --scan-bundle dist/com.universaltill.language-de_<version>_universal.tar.gz
python3 tools/till_verify.py --till ../universal-till --commit <source_commit> \
    --bundle dist/com.universaltill.language-de_<version>_universal.tar.gz --locale de
```

**Environments** (GitHub → Settings → Environments, all limited to `main`):
- `bundle-build`: secret `WEBLATE_BUNDLE_TOKEN` (a read-only Weblate bot
  that can see users' teams and bot flag), variable `WEBLATE_URL`. No
  reviewer.
- `bundle-publish` (ar, es, fa, pt…): secret
  `MARKETPLACE_LANGUAGE_UPLOAD_TOKEN`, variable `MARKETPLACE_BASE_URL`.
  No reviewer: the AI review already gated each string.
- `bundle-publish-human` (en, de, tr): the same secret and variable, plus a
  **required reviewer**. Approving the deployment is the human spot-check:
  read the run's summary (`review.md`) or its artifact first.

## Security

- Public repo: GitHub-hosted runners only, never self-hosted.
- CODEOWNERS (`.github/CODEOWNERS`) puts the owner on every change to the
  publish path: the bundle tools and workflows, `config/`, `meta/` (which
  holds the source commit and the `legal` labels), every `<area>/en.json`,
  and the sync job that writes them.
- No secret in the repo or its workflows. The sync reads universal-till
  without a credential; the dispatch payload is ignored. The
  `repository_dispatch` sender in universal-till stays optional: the hourly
  schedule needs no secret, so the dispatch only makes the sync faster.
- Weblate pushes with its own SSH key, added here as the only write deploy
  key. The Weblate API token is a person's or a protected job's, from the
  environment only; the client refuses plain http (except loopback),
  never follows a redirect (urllib would re-send the token to any
  `Location`), rebases pagination links onto `WEBLATE_URL`, and never sends
  the token anywhere but `WEBLATE_URL`'s scheme and host.
- **Private commit email must be on** in Weblate (instance setting
  `PRIVATE_COMMIT_EMAIL_TEMPLATE`, and each user's "private commit email"
  left on). This repo is public: without it, every Weblate commit publishes
  the translator's Zitadel e-mail address.
- **Protect `main` with a ruleset that blocks force-push and deletion.**
  The sync job and Weblate both push to `main` directly, so `main` can't
  require pull requests; the ruleset keeps its history from being rewritten
  or removed.
- Area ids come from universal-till's `areas.json` and become folders and
  component slugs here, so both tools require `^[a-z0-9-]+$` (words joined
  by single hyphens) and refuse this repo's own folders (`meta`, `tools`,
  `tests`, `config`, `scripts`, `.github`); the sync fails on a bad id.
