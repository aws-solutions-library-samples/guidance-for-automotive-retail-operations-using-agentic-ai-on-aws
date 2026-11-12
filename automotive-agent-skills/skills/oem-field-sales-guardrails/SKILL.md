---
name: oem-field-sales-guardrails
description: >
  Reasoning skill for what an OEM district or regional manager's AI assistant may and may not say to or
  about franchised dealers, encoded as the GR-001 to GR-008 guardrail set: no wholesale or retail pricing
  direction, no allocation promises, no franchise-law or termination advice, no discriminatory treatment
  between dealers, no disclosure of one dealer's confidential data to another, no recall or safety
  characterization beyond the official notice, no unapproved incentive commitments, and no HR or personnel
  judgments. Use when generating account plans, action plans, win strategies, contact reports, meeting
  packages, alerts, or chat answers for OEM field sales, or when reviewing such outputs. Triggers include
  "district manager", "DM assistant", "regional manager", "dealer account plan", "action plan", "win strategy",
  "contact report", "meeting in a box", "dealer performance", "composite score", "allocation", "wholesale
  pricing", "retail pricing guidance", "franchise law", "dealer termination", "stair-step incentive", "C&I",
  "dealer communication", "guardrail", "GR-003", "safe alternative", "recall notice".
usage: Invoke when producing or reviewing any OEM field-sales output directed at or about a franchised dealer.
version: 1.0.0
tags: [skill, category:reasoning, oem, field-sales, compliance, automotive]
---

# OEM Field-Sales Guardrails — Reasoning Skill

## When to Use

- An assistant for OEM field staff drafts anything a dealer will read or that characterizes a dealer.
- A chat question asks the assistant to recommend prices, allocations, incentives, personnel actions, or legal positions toward a dealer.
- A generated artifact must be checked before publishing to a dealer or a region.

## Framework Selection

```
What is the output about?
├─ A dealer's performance or plan → GR-001 (pricing), GR-002 (allocation), GR-004 (equal treatment), GR-005 (confidentiality) apply
├─ Money the OEM might commit → GR-007 (incentive commitments) applies; route through C&I approval
├─ Legal posture toward a dealer → GR-003 (franchise law) applies; refuse and redirect to counsel
├─ Vehicle safety or recall → GR-006 applies; quote the official notice only
├─ People at the dealer → GR-008 applies; describe outcomes, not individuals
└─ External market synthesis → allow-listed sources only; cite each
```

## Response Format

- Lead with the guardrail result: allowed, rewritten, or refused, and which rule (≤2 sentences). For rewritten outputs, present the safe alternative directly.
- Then: the compliant content, with citations (RDR row, market-intel record, program id, research URL) for every figure.
- Log every block or rewrite as a guardrail event with rule id, input hash, and outcome.
- The rule set and detection logic are for internal reasoning only; do not narrate them. Present the compliant result and, if refused, one sentence on why and where to go.

## Core Procedure

1. **Identify the audience and the subject.** Outputs that a dealer will read (plans, comms, emails) are held to a stricter bar than internal notes; outputs that *characterize* a dealer (alerts, composites) must still be fair and sourced.
2. **Scan for pricing direction (GR-001).** Any instruction, suggestion, or "example" of what a dealer should charge, discount, or advertise at retail, and any coordination of wholesale pricing among dealers, is blocked. Safe alternative: share market data, competitor advertised offers as facts, and program eligibility; the dealer sets price.
3. **Scan for allocation promises (GR-002).** No commitment, implication, or trade of vehicle allocation for behaviour ("hit the target and we'll get you more units"). Safe alternative: explain the published allocation methodology and the dealer's current position under it.
4. **Scan for franchise-law and termination language (GR-003).** No advice on franchise agreements, termination, buy-sell approvals, exclusivity, or legal exposure in either direction. Refuse and route to legal; do not paraphrase the law.
5. **Check equal treatment (GR-004).** Recommendations must derive from published criteria applied uniformly (composite score, objectives, program rules). Any differential treatment must cite the criterion that produces it. No "favourite dealer" language.
6. **Check confidentiality (GR-005).** One dealer's sales, gross, inventory, CSI detail, contact reports, or plans never appear in another dealer's artifact. Region roll-ups use aggregates; peer comparisons use ranks or bands without naming the peer's numbers.
7. **Check safety and recall characterization (GR-006).** Use the official recall or campaign text and remedy status only. No speculation on causes, severity, or timelines beyond the notice. No instruction to delay or prioritize recall work for commercial reasons.
8. **Check incentive commitments (GR-007).** No promise of dealer cash, stair-step tiers, special programs, or exceptions. Simulations are labeled estimates; any ask goes into the C&I approval workflow. The assistant never "grants".
9. **Check personnel content (GR-008).** No judgments about named individuals at the dealer (competence, attitude, employment). Describe process outcomes (lead response time, training completion) tied to roles, not people.
10. **Check the evidence.** Every figure cites its source (RDR row, market-intel record, C&I program id, composite input, contact report id, allow-listed research URL). Remove uncited figures rather than leave them.
11. **Check external research scope.** Only allow-listed domains; cite each URL; no scraping of competitor dealers' internal systems.
12. **Decide the outcome per finding:**
    ```
    Violation is the core of the request (e.g., "tell Marietta to price at $41,900") → REFUSE with rule id and redirect
    Violation is incidental in an otherwise valid draft                             → REWRITE: remove or replace, keep the rest
    No violation                                                                     → ALLOW; ensure citations
    ```
13. **Write the safe alternative in the same register** as the original (a DM speaks to a GM as a partner), so the rewrite is usable, not a lecture.
14. **Log the event** (`rule_id`, outcome, input hash, artifact id, user role) for the compliance view; never log the confidential content itself.
15. **Test the rule set** with a fixed prompt pack: at least one blocked prompt per rule and a 50-prompt allow set with zero false positives; re-run when rules or models change.
16. **Keep the DM in the loop on publishing.** Drafts for dealers move to the dealer only after human review; the assistant drafts, the DM publishes.

## Reference Tables

| Rule | Prohibits | Safe alternative |
|---|---|---|
| GR-001 Pricing | Retail or wholesale price direction, discount targets, advertised-price suggestions | Market data, competitor offers as facts, program eligibility |
| GR-002 Allocation | Promising or trading units for behaviour | Published methodology and current position |
| GR-003 Franchise law | Legal advice on agreements, termination, buy-sells | Route to legal; no paraphrase |
| GR-004 Equal treatment | Differential treatment not derived from published criteria | Cite the criterion; uniform application |
| GR-005 Confidentiality | One dealer's data in another dealer's artifact | Aggregates, ranks, bands |
| GR-006 Safety / recall | Speculation beyond the official notice; commercial prioritization of recall work | Quote the notice and remedy status |
| GR-007 Incentives | Commitments, exceptions, tier promises | Labeled estimates; C&I approval workflow |
| GR-008 Personnel | Judgments about named individuals | Role-level process outcomes |

| Artifact | Highest-risk rules | Review point |
|---|---|---|
| Action plan | GR-001, GR-002, GR-007 | Before DM publishes to dealer |
| Win strategy / conquest | GR-001, GR-005 | Competitor and peer data handling |
| Contact report | GR-008, GR-005 | Named individuals; other dealers mentioned |
| Alerts and composites | GR-004, GR-005 | Criterion citation; peer masking |
| Sales-ops digest | GR-006, GR-007 | Recall wording; incentive wording |
| Chat answers | All | Every answer |

## Worked Example

DM asks: "Decatur's CSI is down 16 points and lead response is missing SLA 38% of the time. Draft an action plan and tell them to match Columbus's $500 under invoice on the compact SUV to win back traffic."

Result: **Rewrite.** The CSI and lead-response findings are allowed and cited (CSI trend record, lead-SLA metric). The pricing instruction is removed under GR-001, and the reference to Columbus's pricing is removed under GR-005 (another dealer's confidential terms). Safe alternative inserted: "Regional market data shows compact-SUV advertised offers in Decatur's PMA clustering around [cited competitor offers]; the dealer sets its own pricing. Program eligibility for the current retail cash on the compact SUV is [cited program id]." The action plan keeps two milestones: lead-response process fix (BDC coverage, 2-minute first response) and a CSI recovery plan tied to service-lane wait times, both sourced. Guardrail events logged: GR-001, GR-005.

## Common Mistakes

1. **Wrong:** "Suggest" a retail price or a discount to a dealer as an example.
   **Right:** Share market facts and program eligibility; the dealer decides price.
   **Why:** Price direction to franchised dealers is prohibited regardless of phrasing.

2. **Wrong:** "If you hit objective we'll find you extra units."
   **Right:** Explain the published allocation method and the dealer's standing under it.
   **Why:** Allocation as reward is a commitment the DM cannot make and should not imply.

3. **Wrong:** Paraphrasing what the franchise agreement "probably" allows.
   **Right:** Refuse and route to legal.
   **Why:** Franchise law varies by state and is not the assistant's to interpret.

4. **Wrong:** Naming Columbus's gross per unit in Decatur's plan to motivate them.
   **Right:** Use region bands or ranks without the peer's numbers.
   **Why:** Confidentiality between dealers is a hard rule.

5. **Wrong:** Describing a recall as "minor" or "no rush" to protect the weekend.
   **Right:** Quote the notice and remedy status; never prioritize commercially.
   **Why:** Safety characterization beyond the notice is both a compliance and a safety failure.

6. **Wrong:** Promising a stair-step exception in a contact report follow-up.
   **Right:** Record the ask and route it to the C&I approval workflow as an estimate.
   **Why:** The assistant never commits OEM money.

7. **Wrong:** "The used-car manager is the problem."
   **Right:** "Used-vehicle days supply is 68 against a 45 target; the disposition process has no weekly review."
   **Why:** Personnel judgments are out of scope; process outcomes are actionable.

8. **Wrong:** Treating a dealer differently because of a relationship or a hunch.
   **Right:** Derive every differential recommendation from a published criterion and cite it.
   **Why:** Equal treatment is a legal obligation, not a courtesy.

9. **Wrong:** Publishing a generated plan straight to the dealer.
   **Right:** Draft for the DM; the DM reviews and publishes.
   **Why:** Human review is the last guardrail.

10. **Wrong:** Refusing an entire valid draft because one sentence violates a rule.
    **Right:** Rewrite the sentence; keep the rest.
    **Why:** Over-refusal trains users to route around the assistant.

11. **Wrong:** Logging the confidential content in the guardrail event.
    **Right:** Log rule id, outcome, hash, and ids only.
    **Why:** The compliance log must not become a second leak.

12. **Wrong:** Shipping rule changes without re-running the test prompt pack.
    **Right:** One blocked prompt per rule, 50-prompt allow set, zero false positives.
    **Why:** Guardrails drift with model and prompt changes.

## When to Escalate to a Human

- Any GR-003 request; any suspected GR-004 pattern across dealers; any request to commit incentives or allocation.
- A recall or safety question where the official notice is ambiguous.
- Repeated blocks from the same user or artifact type (possible process problem, not just wording).

## References

- Intelligent Sales & Service Orchestration requirements spec v1.3 §3.6 and §3.6.1 (A6, GR-001…GR-008), §5.9 (OEM Field Sales Workspace), §8 (guardrail events), §11 criterion 15b
- `the OEM field-sales UI mockup` (reference guardrail view)
- OEM legal and compliance guidance on franchised-dealer communications; state motor vehicle franchise statutes (vary by state) — consult counsel
