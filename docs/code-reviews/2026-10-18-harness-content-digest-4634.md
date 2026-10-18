# Till harness signs like the ADR-0165 marketplace (ut-docs#4634)

## What shipped
- `tools/till_harness/utt_bundle_harness_test.go`: `uttSignBundle` now does what ut-cloud `internal/signing.SignBundle` does since ADR-0165. It sets `content_digest`, signs the content form (digest kept) into `content_signature`, and signs the legacy form (all three fields emptied) into `signature`.
  - The digest comes from the new `uttContentDigest`. It runs over the tar entries, mirroring ut-cloud's `contentDigest`, so the till's on-disk `ComputeContentDigest` is checked against an independent computation.
- A new sub-test: a locale file swapped after signing is refused by the content digest, even when the download checksum is recomputed. It is listed in `tests/test_till_verifier.py`.
- Two existing sub-tests now pin their exact refusal:
  - "unsigned bundle is refused" checks `errors.Is(err, ErrManifestUnsigned)`.
  - "tampered manifest" checks `manifest signature verification failed`.
- README "Checked against the real till" now describes the ADR-0165 signing and the new refusal.

## Root cause
- universal-till 8529058bf (ADR-0165) makes `VerifyManifest` require `content_digest` and `content_signature` when a key is configured.
- The harness still emulated the pre-ADR-0165 marketplace signer, so the till refused the untampered bundle. `main` went red once the sync job moved `meta/source.json` to `fef82eb`.
- The card's AC named `build_bundle.py` as the place to fix. That is not where the fix belongs:
  - This repo never holds a signing key. Bundles stay unsigned until the marketplace approves them (ADR-0006), and ut-cloud already sets both content fields at approval.
  - So `build_bundle.py` and the publish workflow are unchanged, and no placeholder key exists anywhere.

## Review
- Fable was out of usage credits, so Sonnet reviewed independently (the author model was Opus 5.5).
- Verdict: safe to merge; no blockers or majors.
- Confirmed:
  - The signed bytes match ut-cloud's `signManifest` and the till's `legacySignedBytes`/`contentSignedBytes`.
  - The digest matches ut-cloud's and the till's (`./` prefixes, directory entries skipped).
  - The new sub-test can only fail through the digest.
- Findings, all fixed:
  1. nit: "unsigned bundle is refused" only checked `err != nil`. It now checks `ErrManifestUnsigned`.
  2. nit: "tampered manifest" matched any "signature" error. It is now pinned to the legacy signature message.
  3. minor: the README did not mention the content digest or the new refusal. Updated.

## Verified beyond the unit run
- Reproduced first: on `main`, `test_bundle_installs_loads_and_tampered_copies_are_refused` fails because the till refuses the signed bundle.
- Full suite (`UTT_REQUIRE_TILL=1 -W error::ResourceWarning`) against universal-till at the pinned `fef82eb`: 165 OK.
- The till-verifier tests against universal-till `main` (`7bead31`): OK.
- Negative control: with the till's `actual != m.ContentDigest` check disabled in a throwaway tree, only the new sub-test fails ("want a content digest refusal, got <nil>"). Restored, all pass.
- `gofmt -l tools/till_harness`: clean.

## Deferred
- None.
