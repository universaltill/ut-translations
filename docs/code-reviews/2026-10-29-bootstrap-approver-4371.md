# Review: bootstrap approver account (ut-docs#4371)

Scope: one entry in `config/ai-accounts.json` listing the Weblate bot
`bot-universal-till-bootstrap-approver` (model label `owner-bootstrap`).

Why: the imported de/es/pt/tr strings carry no approval trail, so the
bundle build omits them (`no_approval`). The owner decided (2026-10-29) to
accept the existing translations as they are, to re-review later. The owner
cannot approve them: a bulk edit under their account at import makes them the
recorded author (`self_approved`). The approver must be another account.

What this does not weaken (checked against `review_verdict`):
- author = approver is still refused;
- two AI accounts on the same model are still refused (the label is unique);
- `review: legal` keys still need a `human-reviewers` member, so the bot's
  approval leaves them out (`legal_needs_human`);
- an unlisted bot is still `unknown_ai_account`.

What it exposes: the bot can approve any non-legal string in Weblate
(`Reviewers` role, site-wide). Its token lives in the owner's Keychain only
and is revoked when the bulk run is finished.

Honesty: the note states plainly that this is not an AI review. Remove the
entry when the re-review is done.

Verified: unit tests pass. Reviewer: reviewed by the owner's explicit
decision on the card; no code changed.
Verdict: safe to merge. Deferred: the real re-review (card #4371).
