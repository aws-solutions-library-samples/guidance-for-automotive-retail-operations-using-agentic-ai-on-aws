# Skills Landscape (checked 2026-09-23)

What already exists, so this repository fills a gap instead of duplicating one.

## Vertical skill collections

| Repository | Domain | Relevance |
|---|---|---|
| [awslabs/hcls-agent-skills](https://github.com/awslabs/hcls-agent-skills) | Healthcare and life sciences, 42 skills, 13 domains | The template this repo follows: `SKILL.md` + frontmatter, reasoning vs pipeline categories, `install.sh` for Kiro / Claude Code / Quick, multi-agent Kiro configs, validator, 410-prompt pairwise eval. Companion AWS blog post published 2026-09. |
| [pangzhenying2025/hermes-automotive-skills](https://github.com/pangzhenying2025/hermes-automotive-skills) | Automotive **engineering**: ADAS, AUTOSAR, BMS, functional safety, diagnostics protocols, V2X, China GB standards | Different half of the industry. No overlap with retail, F&I, fixed operations, or OEM field sales. Confirms the retail / dealer gap. |
| [anthropics/skills](https://github.com/anthropics/skills) | Horizontal: documents (docx, pptx, xlsx, pdf), frontend design, MCP builder, brand | Useful as authoring reference; no domain content. |
| [agentskills/agentskills](https://github.com/agentskills/agentskills) | The open specification | Frontmatter and directory contract we follow. |

Search on GitHub, the Agent Skills spec site, and the vercel-labs `npx skills` index found **no collection for automotive retail, dealer service, F&I, or OEM field sales**. This repository would be the first.

## Build-time skills AWS already ships (reuse, do not duplicate)

| Source | Covers | Install |
|---|---|---|
| [aws/agent-toolkit-for-aws](https://github.com/aws/agent-toolkit-for-aws) `aws-core` | Service selection, CDK / CloudFormation, serverless, containers, storage, observability, billing, SDKs, deployment | `npx skills add aws/agent-toolkit-for-aws/skills` (Kiro reads `~/.kiro/skills/`) |
| same, `aws-agents` | Amazon Bedrock, AgentCore Runtime / Gateway / Memory / harness, Strands | same |
| same, `aws-data-analytics` | S3 Tables, Glue, Athena, lake / ETL | same |
| [awslabs/agentcore-samples](https://github.com/awslabs/agentcore-samples) `06-strands-with-skills` | Strands `AgentSkills` plugin: `SKILL.md` discovery and activation at runtime | reference |
| [AgentCore harness skills](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-skills.html) | Attaching skills from Git, S3, or the curated AWS catalog to a hosted harness | `agentcore` TUI |
| [Amazon Quick desktop skills](https://docs.aws.amazon.com/quick/latest/userguide/desktop-skills.html) | Upload `SKILL.md`; skills can bundle tools and reference files; scheduled tasks | GUI |

Consequence for Track 2: our build skills cover only Retail Ops conventions and the seams AWS documents in separate places (Cognito + Federate + group mapping; Identity Center → Quick embedding + Quick groups from Cognito roles + `postMessage` navigation; Gateway → Quick tool bridge; event rules and schedules; autonomy and recommendations).

## Regulatory landscape that changes skill content (US retail)

- **CARS Rule**: vacated by the Fifth Circuit 2025-01-27 on procedural grounds (no ANPRM); formally withdrawn by the FTC 2026-02-12 (91 FR 6507). Skills must **not** cite CARS as binding.
- **FTC Act §5 enforcement resumed**: 97 warning letters to dealer groups on 2026-03-13 and follow-up guidance on advertised price transparency: mandatory dealer charges belong in the headline price; conditional discounts (first responder, dealer financing) cannot be the primary advertised price; rebates only if the reader qualifies; sold vehicles must come down promptly; financing-contingent terms disclosed up front. This is the basis for the new `dealer-advertising-and-price-transparency` skill and constrains `price_build`, `compose_message`, and `structure_deal`.
- **Consent**: TCPA (status of the one-to-one consent rule must be checked at authoring time), CAN-SPAM, and GDPR for the `de` market; these are hard rules in the nurture skill.
- **Credit**: TILA / Reg Z disclosure, ECOA adverse-action notices, OFAC screening, FTC Red Flags identity verification, and the Safeguards Rule for F&I data. Encoded as a `validate_handoff(AGREE)` gate in the spec and as the `deal-structuring-and-disclosure` skill.

## Industry signals that suggest new skills

- Technician workforce retiring (PartsTech 2025 survey: 59% of shops report high or moderate impact of the shortage) and AI diagnostic tablets positioned as "knowledge backup": supports `technician-knowledge-capture`.
- EV service: battery state-of-health communication is where customers are most often alarmed unnecessarily; supports `ev-battery-health-communication`.
- Dealer AI risk: trade press (CBT News 2026-09) warns dealers adopt AI tools before understanding data handling and liability; supports keeping `autonomy-ladder-and-hard-rules` and the guardrail skills in the P0/P1 tier.

## Evaluation

hcls-agent-skills backs its claims with a pairwise harness (`eval/run.py`, blind judge, per-dimension deltas). Generic equivalents exist ([adewale/skill-eval-harness](https://github.com/adewale/skill-eval-harness)). This repository makes no performance claim until `skills-build/skill-eval-harness` exists and has been run on a prompt pack for the shipped skills.
