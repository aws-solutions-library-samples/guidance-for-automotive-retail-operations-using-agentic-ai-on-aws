# Contributing

## Adding a skill

1. Pick a title from `ROADMAP.md` or propose a new one in an issue. One decision domain per skill.
2. Copy the structure of an existing skill (for example `skills/service-estimate-integrity/SKILL.md`): frontmatter → When to Use → Framework Selection → Response Format → Core Procedure (numbered, with branch points) → Reference Tables (≤ 10) → Worked Example → Common Mistakes (≥ 8) → When to Escalate → References.
3. Apply the core principle from the design guide: **add what the agent lacks, omit what it already knows.** Run your two test prompts without the skill first; anything the base model already gets right does not belong.
4. Date and cite every regulatory, tax, incentive, warranty, or safety figure, and name the jurisdiction. These change; a skill without dates decays silently.
5. Run `python3 tests/validate_skill.py --skill <name>` until it passes, then walk `QUALITY_CHECKLIST.md`.
6. If the skill maps to agent tools, reference them by the names in `TOOLS.md` so readers can trace skill → tool.
7. Open a PR with the two test prompts and the outputs with and without the skill.

## Updating TOOLS.md

`TOOLS.md` is generated. Change the requirements spec's §3 tool tables and run `python3 scripts/extract_tools.py`. Do not edit `TOOLS.md` directly.

## Customizing for your organisation

Fork and edit thresholds, coverage order, guardrail sets, and market rules to match your OEM program, your MPI form, and your jurisdictions. Keep the skill name and structure so upstream improvements merge cleanly; bump the `version` and add a `customized_for` tag.

## Style

- Plain declarative sentences; no marketing language.
- Numbers with units and dates; ranges where the truth is a range.
- Synthetic data only. Reserved example domains, 555 / 0151 numbers, fictional streets on real cities.
- No em dashes in body text; tables and trees carry structure.

## Code of conduct

Be direct and courteous. Review the content, not the person.
