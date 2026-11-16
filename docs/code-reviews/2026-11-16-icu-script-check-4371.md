# Review: script check reads ICU plural arms (ut-docs#4371)

Combined Tester (Sonnet) and Reviewer (Fable, a different model) subagents, one batch.

- Change: `bundle_checks.scripts_in()` flattens ICU plural/select messages so a translated
  Arabic/Persian/Chinese plural is no longer omitted as "no letters in Arab".
- Tester: PASS (every module green; the only error, `test_this_repos_english_matches_universal_till`,
  is a partial-till-checkout artefact that fails the same on main).
- Reviewer: one blocking finding, B1: `{n, number}` and `{name}` arguments were counted as Latin
  letters, so letter-free strings such as `{n, number} / {m, number}` were dropped in non-Latin
  locales. Fixed in the second commit (`ICU_ARG_ONLY_RE`), tests added for number/percent/select.
- Non-blocking, left: a half-translated plural passes (same as non-ICU strings today);
  `review_checks.check_rtl` still rejects ICU plurals in RTL locales (own card).
- Also closed two gate holes the old code had: Cyrillic arms in a Latin locale and an untranslated
  select in an Arabic locale now fail.
