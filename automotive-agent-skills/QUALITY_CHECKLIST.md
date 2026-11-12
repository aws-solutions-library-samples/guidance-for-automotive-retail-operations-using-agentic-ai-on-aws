# Skill Quality Checklist

Use this when authoring or reviewing a skill. Every item must hold before merge. Items with a property number (P*n*) are enforced by `tests/validate_skill.py`.

## Frontmatter (P1)

- [ ] `name` present and equal to the directory name
- [ ] `description` present, ends with a `Triggers include "…"` list
- [ ] `usage` present: one line saying when to invoke
- [ ] `version` is semver
- [ ] `tags` array present; first element is the literal `skill`
- [ ] `tags` includes `category:reasoning` or `category:pipeline` (P14)
- [ ] Pipeline skills: `validated_against` with date and key package versions

## Structure

- [ ] ≤ 500 lines (P12); reasoning skills ideally 150–350
- [ ] `## Response Format` (or `## Response Structure`) present (P13)
- [ ] ≥ 12 quoted trigger keywords in `description` (P15)
- [ ] Reasoning: decision tree (`├─` / `└─`) or ≥ 10 numbered steps (P16); pipeline: ≥ 1 fenced code block
- [ ] Reasoning skills with decision trees: Response Format says "do not narrate" or "internal reasoning" (P17)
- [ ] `## Common Mistakes` with ≥ 8 Wrong / Right / Why items (P18)
- [ ] `## References` present (P19)
- [ ] ≤ 10 tables; one decision domain per skill

## Content

- [ ] Teaches what the base model gets wrong (run two prompts without the skill; if the output is equivalent, cut the content)
- [ ] ≥ 5 niche numeric thresholds (dates, percentages, mm, 32nds, caps) with their source
- [ ] Every regulatory, tax, incentive, warranty, or safety claim is dated and cited; jurisdiction named
- [ ] Framework Selection comes before procedure; procedure has branch points
- [ ] One worked example with realistic values, no placeholders
- [ ] Urgency and safety language follows the inspection's own terms; no scare language
- [ ] Nothing in the skill instructs the agent to approve, spend, allocate, or contact on a human's behalf
- [ ] Synthetic brands and data only (Meridian Motors, Nordwerk Automobile); no real OEM, dealer, or customer identifiers

## Testing

- [ ] `python3 tests/validate_skill.py --skill <name>` passes
- [ ] Tested with ≥ 2 representative prompts; outputs recorded in the PR
- [ ] Prompts and outputs contain no PII

## Sign-off

- [ ] Author reviewed every item
- [ ] Reviewer reviewed every item
