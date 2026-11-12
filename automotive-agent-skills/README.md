# Automotive Agent Skills

Open-source **agent skills** and a **tools reference** for automotive retail and dealer service workflows, following the [Agent Skills open standard](https://agentskills.io) (`SKILL.md` + YAML frontmatter). Modeled on [awslabs/hcls-agent-skills](https://github.com/awslabs/hcls-agent-skills).

Skills encode the methodology, thresholds, and failure modes that domain experts internalize and that base models get wrong: how to build a repair estimate a customer can approve line by line, which EV incentives still exist on a given date, when a trade-in equity position justifies an upgrade conversation, what an OEM district manager's assistant must never say to a franchised dealer, and how to pick the right AWS delivery model per persona. Skills work across Kiro, Amazon Quick, Amazon Bedrock AgentCore, AWS Strands Agents SDK, Claude Code, and any platform that reads the standard.

Two artifacts:

- **`skills/`** — reasoning and pipeline skills (`SKILL.md`) that shape how an agent thinks.
- **`TOOLS.md`** — the reference of **121 agent tools across 7 agents** (the callable functions an agent runtime exposes), generated from the reference architecture's requirements spec. Skills reference tools by name where a procedure step maps to a call.

The reference architecture behind both is **Intelligent Sales & Service Orchestration**, the orchestration layer of *Automotive Retail Operations powered by Agentic AI* ("Retail Ops" below): two business processes (the 8-stage retail purchase journey and the 8-stage service & ownership journey) run by seven agents on a deliberately mixed stack (custom agents on Amazon Bedrock AgentCore, Amazon Quick agents/Flows/Index/Research, QuickSight analytics, deterministic read models, brand customer web), with a per-dealer autonomy ladder and hard rules above configuration.

> All examples use synthetic brands (Meridian Motors, Nordwerk Automobile) and synthetic data. No real OEM trademarks, dealer data, or customer data. Not legal, tax, or financial advice.

## Why Agent Skills for Automotive

Dealers lose the customer at handoffs: between online and showroom, between sales and F&I, between the service lane and the sales floor. Roughly 70% of inspection findings are never presented to the customer; the $7,500 federal EV credit customers still ask about ended for vehicles acquired after 2025-09-30; a trade valuation on the wrong trim is worthless. Base models know the vocabulary but not the procedure: which coverage wins for a repair line, why negative equity must be shown as a number, what a district manager may not say about pricing. A skill encodes that procedure once, versioned and portable, so every agent on every platform applies the same standard.

## Skill Catalog

**6 skills shipped, 19 planned** across 6 domains (3 added after a 2026-09-23 landscape check; see `docs/LANDSCAPE.md`). Status: ✅ shipped · 🔜 planned (title and scope fixed; see `ROADMAP.md`). A second track of **build-time skills** for our own coding agents is scoped in `ROADMAP.md` Track 2.

### Fixed Operations (service)

| Name | Category | Description | Status |
|---|---|---|---|
| `service-estimate-integrity` | reasoning | Finding-anchored estimate lines, coverage order (recall → warranty → contract → goodwill → customer pay), line types, evidence rule, auto-present thresholds, declined-work follow-up, presentation vs approval rates | ✅ |
| `multi-point-inspection-triage` | reasoning | ICE / hybrid / EV templates, status from measurement, evidence rule, DTC-by-vehicle diagnosis with TSB citations, urgency, HV-certification dispatch gate, technician data scope | ✅ |
| `service-morning-work` | reasoning | 05:00 shop-load: dispatch by skill and capacity, parts staging with confidence and cap, advisor briefs, promised-time re-forecast | 🔜 |
| `service-retention-and-declined-work` | reasoning | Retention math by vehicle age, declined-work cadence, service-template messaging vs sales messaging, recall proactive booking | 🔜 |
| `repair-order-dms-mirroring` | pipeline | RO mirror patterns against CDK / Reynolds-style DMS: create, line update, close, invoice, idempotency, system-of-record rules | 🔜 |

### Retail Sales and F&I

| Name | Category | Description | Status |
|---|---|---|---|
| `ev-incentive-eligibility` | reasoning | US federal (30D/25E expired after 2025-09-30; loan-interest deduction 2025–2028), state and utility programs; German E-Auto-Förderung 2026, Kfz-Steuer, THG-Quote, 0.25% company car; stacking and TCO | ✅ |
| `trade-equity-and-upgrade-timing` | reasoning | Valuation ranges with sources, payoff freshness, equity classification, lease-end and excess-mileage math, repair-vs-replace, disposition, service-drive acquisition | ✅ |
| `deal-structuring-and-disclosure` | reasoning | Payment scenarios, state tax and doc fees, TILA/Reg Z disclosure text, F&I menu presentation, credit-compliance gate (OFAC, Red Flags, adverse action) | 🔜 |
| `objection-handling-on-the-floor` | reasoning | Objection taxonomy (range, payment, trade value, interest rate, incentive confusion), sub-2-second response contract, success-rate grounding | 🔜 |
| `lead-response-and-nurture-policy` | reasoning | Four-stage lead funnel, 2-minute first response, ≤2 touches/week, quiet hours, consent (TCPA / CAN-SPAM / GDPR), stop conditions, human handoff triggers | 🔜 |

### Vehicle Discovery and TCO

| Name | Category | Description | Status |
|---|---|---|---|
| `ev-vs-ice-tco` | reasoning | 5-year TCO with regional energy and fuel prices, market units (MPG/MPGe vs l/100 km and kWh/100 km), active incentives only, announced items separated | 🔜 |
| `open-data-vehicle-anchors` | pipeline | Deriving synthetic catalogs and market anchors from EPA, AFDC, EIA, KBA, EEA, BNetzA, Eurostat with `source_ref` on every number | 🔜 |

### OEM Field Sales

| Name | Category | Description | Status |
|---|---|---|---|
| `oem-field-sales-guardrails` | reasoning | GR-001…008: no pricing direction, allocation promises, franchise-law advice, unequal treatment, cross-dealer confidentiality leaks, recall speculation, incentive commitments, or personnel judgments; refuse / rewrite / allow with safe alternatives | ✅ |
| `dealer-account-plan-generation` | reasoning | Composite score inputs, gap analysis, action-plan milestones, meeting package, every figure cited to RDR / market-intel / C&I rows | 🔜 |
| `market-intelligence-from-open-data` | pipeline | Registrations and share proxies from KBA / AFDC / EEA; PMA construction; competitor offer capture within an allow-list | 🔜 |

### Marketing and Attribution

| Name | Category | Description | Status |
|---|---|---|---|
| `campaign-attribution-to-delivery` | reasoning | Last-touch / linear / position models, journey-event lake, delivered-vehicle join, budget-shift recommendation as an approval, never auto-spend | 🔜 |

### Agentic Architecture on AWS

| Name | Category | Description | Status |
|---|---|---|---|
| `agentic-delivery-model-selection` | reasoning | Rules R1–R7 for AgentCore custom agent vs AgentCore real-time path vs Amazon Quick vs QuickSight vs read models vs brand web vs outreach provider; composition rules; autonomy ladder; hard rules; anti-patterns | ✅ |
| `autonomy-ladder-and-hard-rules` | reasoning | Action registry, observe / recommend / act_with_approval / act_and_notify, per-dealer scope, observation fortnight, constraints (caps, channels), what may never be configurable | 🔜 |
| `agent-outcome-loop` | pipeline | Logging shown → accepted → converted per recommendation, context hashing, ranking from aggregates, lake export with hashed ids | 🔜 |
| `two-supervisor-orchestration` | reasoning | One supervisor per state machine, specialists as tools, supervisors meet through events, handoff validation, journey supersession | 🔜 |

## Tools Reference

`TOOLS.md` lists every agent tool in the reference architecture with signature, source or behaviour, and whether it is proactive (event- or schedule-triggered). Regenerate from the spec:

```bash
python3 scripts/extract_tools.py --spec ../docs/specs/intelligent-sales-service-orchestration.md
```

| Agent | Role | Delivery model | Tools |
|---|---|---|---|
| A1 | Personalized Vehicle Discovery | AgentCore | 13 |
| A2 | Purchase Journey Orchestration (supervisor) | AgentCore | 21 |
| A3 | Dealer Sales Advisor | AgentCore (floor + assistant, one agent; Quick Index as retrieval tool) | 13 |
| A4 | Trade-In & Upgrade Advisor | AgentCore | 8 |
| A5 | Campaign ROI Optimizer | Quick / QuickSight + AgentCore recommendations | 9 |
| A6 | OEM Sales Administrator | Amazon Quick (agent, Flows, Index, Research) | 32 |
| A7 | Service Operations (supervisor) | AgentCore | 25 |

Skills and tools relate but are not the same thing: a tool is what the agent *can call*; a skill is how the agent *decides* what to call and how to interpret the result. `service-estimate-integrity`, for example, orchestrates `check_coverage`, `get_labor_time`, `lookup_parts`, `build_estimate`, `present_estimate`, and `record_approval`.

## Installation

```bash
git clone <this repo>
cd automotive-agent-skills
./install.sh --target kiro          # copies skills to ~/.kiro/skills/ and the agent config to ~/.kiro/agents/
./install.sh --target claude-code   # symlinks into ./.claude/skills/
./install.sh --target quick-desktop # prints upload instructions
```

Or with the universal CLI once published: `npx skills add <org>/automotive-agent-skills`.

### Kiro

```bash
./install.sh --target kiro
# In Kiro CLI:
/agent automotive
# Or load one skill:
/context add ./automotive-agent-skills/skills/service-estimate-integrity/SKILL.md
```

Multi-agent mode routes to three specialists (fixed ops, retail, OEM and architecture) to keep context small:

```bash
./install.sh --target kiro --mode multiagent
/agent automotive-multiagent
```

### Amazon Quick

Upload individual `SKILL.md` files: Settings → Capabilities → Skills → Upload. Start with one domain (for example, fixed operations) and add more. `./install.sh --target quick-desktop` prints the recommended order.

### AWS Strands Agents SDK and Amazon Bedrock AgentCore

```python
from strands import Agent, AgentSkills
from strands.models.bedrock import BedrockModel

model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514", region_name="us-east-1")
skills = AgentSkills(skills="./automotive-agent-skills/skills/")
agent = Agent(model=model, plugins=[skills])
```

Skills activate on query content. The same agent deploys to AgentCore Runtime; skills can also be configured at the AgentCore harness level so every agent in the environment has them.

## Quick Start

```
> A technician just submitted the MPI on a 2022 EV crossover: pads 3 mm with photo, charge port pins discolored, pending U-code. Build the estimate.
[agent loads multi-point-inspection-triage, then service-estimate-integrity]

> Customer bought in October 2026 and is asking where her $7,500 credit is.
[agent loads ev-incentive-eligibility]

> Draft an action plan for Decatur and tell them to match Columbus's pricing.
[agent loads oem-field-sales-guardrails; rewrites, logs GR-001 and GR-005]
```

## Evaluation

`eval/` is a blind pairwise harness (same design as hcls-agent-skills): each prompt is answered with and without the skill, a separate judge model scores both in randomized order on correctness, critical thinking, compliance, and coherence, and `build_review.py` renders every pair for inspection. 33 prompts across the 6 shipped skills.

```bash
python3 eval/run.py --provider mock --version smoke        # harness self-test, no credentials
python3 eval/run.py --provider bedrock --version v1        # real run (Bedrock credentials required)
python3 eval/build_review.py --version v1
```

**No performance claim is made yet.** A committed `eval/results/<version>/summary.json` from a `bedrock` run is the only thing that would justify one. See `eval/README.md` and `skills-build/skill-eval-harness/SKILL.md`.

## Validation

```bash
python3 tests/validate_skill.py            # all skills, including skills-build/
python3 tests/validate_skill.py --skill service-estimate-integrity
```

Checks frontmatter (P1), length (P12), response-format section (P13), category tag (P14), ≥12 trigger keywords (P15), decision tree or ≥10 steps (P16), "do not narrate" instruction (P17), ≥8 Common Mistakes (P18), References section (P19). See `QUALITY_CHECKLIST.md` and `SKILL_DESIGN_GUIDE.md`.

## Customization and Contributing

Skills are meant to be edited for your dealer group or OEM: thresholds from your MPI form, your coverage order, your guardrail set, your state's tax rules. See `CONTRIBUTING.md`. New skills welcome; the planned list in `ROADMAP.md` is a good place to start.

## License

MIT-0. See `LICENSE`.

## Disclaimer

This repository is for demonstration and reference. It is not legal, tax, financial, or safety advice. Incentive programs, tax rules, warranty terms, and franchise regulations change and vary by jurisdiction; verify against current official sources before relying on any figure. Users are responsible for compliance with applicable consumer-protection, privacy (including TCPA, CAN-SPAM, GDPR), and motor-vehicle franchise laws.
