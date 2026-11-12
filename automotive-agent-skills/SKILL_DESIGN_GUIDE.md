# Skill Design Guide

This repository follows the design guidance published with [awslabs/hcls-agent-skills](https://github.com/awslabs/hcls-agent-skills/blob/main/SKILL_DESIGN_GUIDE.md), which is grounded in a 410-prompt pairwise evaluation. The essentials, restated for automotive:

## Core principle

**Add what the agent lacks. Omit what it already knows.** Base models know that brakes wear and that EV credits exist. They do not know that 30D ended for acquisitions after 2025-09-30, that recall coverage beats customer pay regardless of phrasing, that a P1xxx code must be looked up by make, or that a district manager may not suggest a retail price. Write the second kind of content.

## What predicts a skill that helps

- Ordered procedures with branch points (15–25 numbered steps), not descriptions.
- Niche numeric thresholds with dates and sources (≥ 5), not thresholds the model already knows.
- Decision trees for reasoning skills, parameter tables for pipeline skills.
- 8–15 specific Common Mistakes as Wrong / Right / Why.
- A Response Format section that prescribes structure and says the trees are for internal reasoning (this is the main defense against incoherent output).
- Framework Selection first, so the agent picks the right frame before the details.
- One worked example with realistic values.
- ≥ 12 trigger keywords in the description, or the skill never loads.

## What predicts a skill that hurts

- Reciting well-known material.
- Encyclopedia mode: many topics, many tables, no "pick one path".
- Code without decision context.
- No output format.

## Automotive-specific rules

- Every regulatory, tax, incentive, warranty, and safety figure is **dated** and **jurisdictioned**. These change mid-year; an undated figure is a future defect.
- Urgency uses the inspection's own vocabulary (now / soon / monitor). No scare language, ever.
- Skills never instruct an agent to approve, spend, allocate, or contact a customer on a human's behalf; they instruct it to present, recommend, or route.
- Where a skill step maps to an agent tool, name the tool as in `TOOLS.md`.
- Synthetic brands and data only.

## Template

```markdown
---
name: skill-name
description: >
  [1–2 sentences]. Use when [triggers in domain jargon]. Triggers include "t1", "t2", … [≥ 12].
usage: Invoke when [one line].
version: 1.0.0
tags: [skill, category:reasoning, domain, automotive]
---

# Skill Name — Reasoning Skill

## When to Use
## Framework Selection      (tree or table, first)
## Response Format          (structure + "internal reasoning only")
## Core Procedure           (15–25 numbered steps with branches)
## Reference Tables         (≤ 10)
## Worked Example           (realistic values)
## Common Mistakes          (≥ 8 Wrong / Right / Why)
## When to Escalate to a Human
## References               (dated, jurisdictioned)
```

Length: reasoning 150–350 lines; pipeline 250–400; hard cap 500 (enforced).
