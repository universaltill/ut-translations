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
| `tools/` | — | `sync_english.py`, `provision_weblate.py`, `i18n_map.py` (the shared mapping). Python 3 standard library only. |
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

Unit tests (no network; the Weblate tests use a fake server on 127.0.0.1):

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

## Security

- Public repo: GitHub-hosted runners only, never self-hosted.
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
