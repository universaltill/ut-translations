# Review: list the Weblate AI reviewer account (ut-docs#4366, #4367)

Scope: `config/ai-accounts.json` lists `bot-universal-till-ai-reviewer-codex` (Weblate user 12, the only AI account that exists) with model label `codex`. Without an entry the bundle build refuses every translated locale ("lists no AI accounts").

- No code change. The build already treats an approver as valid only if it is a listed AI account or a `human-reviewers` member; this entry adds exactly one approver.
- Legal keys still need a human-reviewers member (`legal_needs_human`); the reviewer bot never approves them.
- The model label is the family. A future translator account must name a different model or its strings are omitted (`same_model`).
- Owner file (CODEOWNERS): the pull request is the owner's review point.

Verified: the full suite on main passes (165 tests). The effect on real coverage is measured by the dry run after merge, not assumed.

Verdict: safe to merge.
