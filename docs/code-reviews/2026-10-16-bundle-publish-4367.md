# Review — language bundle publish workflow (ut-docs#4367)

- **Date:** 2026-10-16 · **Lane:** lane:local · **Built by:** Opus 5.5 · **Reviewed by:** Fable 5.1 (independent security review)
- **Design:** ADR-0160 §4, §5, §9, §10.
- **Scope:**
  - `tools/build_bundle.py`: an approved-only export from the Weblate API, with approval semantics from the change history: author ≠ approver, AI approvals need a different model, legal keys need a human reviewer on the current English, and approvers are deny-by-default.
  - `tools/bundle_checks.py`: per-string checks.
  - Universal-till's compliance and naming guards, pinned to a released `source_commit`.
  - `tools/till_verify.py`: the real till install and verify path in a throwaway checkout.
  - `tools/publish_bundle.py`.
  - The `publish-bundle.yml` jobs: build (Weblate token, no till code) → verify (no secrets) → publish (upload token, the `main`-only environments, a human reviewer for en/de/tr).
  - CODEOWNERS.

## Verified
- 152 tests, 0 skipped, with the real till harness running (`UTT_REQUIRE_TILL=1`); actionlint clean.
- The AC tests:
  - a tampered bundle is refused by the till's real install path;
  - a legal string without a human approval is omitted;
  - "GoBD-konform" fails the publish (Unicode look-alikes too).
- Mutation checks on the legal rule, the scans, self-approval, the same-model rule and token scrubbing.
- Real-repo dry run of all three jobs for `en`: 3,471/3,471 strings; the hashes, guards and till verifier pass.
- Not run against the live Weblate (no bot token yet).

## Findings
| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | High | `main` unprotected; CODEOWNERS decorative | Not fixable in code: GitHub refuses the Actions integration as a ruleset bypass actor. Owner decision recorded on ut-docs#4367 (sync via PR + ruleset, or rely on environments). **Hard stop: no bundle secrets until decided** |
| 2 | High | `source_commit` trusted blindly; till code ran in the job holding the Weblate token; `meta/` not code-owned | Fixed: released-commit check (ancestor of main or a `v` tag) in the workflows and in Python; CODEOWNERS widened; the verify job has no secrets; the guards also moved out of the token job (sudo-capable hosted runner) |
| 3 | Medium | AI identity deny-by-default only for bots | Fixed: `unknown_approver`; a non-English build fails while no AI accounts exist (#4366) |
| 4 | Medium | The till signature covers only `manifest.json` | Confirmed; ADR-0160 corrected (ut-docs#4547); fix card ut-docs#4546 |
| 5 | Low | The en bundle wasn't tied to universal-till | Fixed: the en build re-renders the till's English and metadata at `source_commit` and fails on any difference |
| 6 | Low | `previous_source` may be over-strict | Unchanged (fails closed); README says to verify on live before the first de publish |
| 7 | Low | Team membership read at build time | Documented |
| 8 | Nit | `extractfile()` None, report hash handoff | Fixed |

**Verdict:** safe to merge. The workflow is inert until the owner creates the environments, and finding 1 must be settled first.
