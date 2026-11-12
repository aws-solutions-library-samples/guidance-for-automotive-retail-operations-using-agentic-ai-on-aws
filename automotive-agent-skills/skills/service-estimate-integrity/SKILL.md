---
name: service-estimate-integrity
description: >
  Reasoning skill for building, presenting, and recording dealer service estimates that a customer
  can approve line by line and an auditor can defend. Use when an agent turns inspection findings,
  diagnostic codes, recalls, or maintenance-schedule items into a priced repair estimate, decides
  coverage (warranty, recall, service contract, prepaid maintenance, goodwill, customer pay), classifies
  repair-order line types, presents an estimate to a customer, records approvals and declines, or
  schedules declined-work follow-up. Triggers include "service estimate", "repair estimate", "repair
  order lines", "RO lines", "customer pay vs warranty", "coverage check", "recall coverage", "service
  contract", "prepaid maintenance", "goodwill", "sublet line", "internal RO", "good better best",
  "estimate approval", "digital approval", "declined work", "declined services", "ASR", "additional
  service recommendation", "presentation rate", "approval rate", "repair vs replace", "labor op",
  "flat rate hours", "parts source", "estimate line", "MPI upsell".
usage: Invoke when composing or reviewing a service estimate, deciding line coverage and type, presenting to a customer, or handling approved / declined lines.
version: 1.0.0
tags: [skill, category:reasoning, fixed-ops, service, automotive]
---

# Service Estimate Integrity — Reasoning Skill

## When to Use

- An agent must turn inspection findings, DTC diagnoses, open recalls, or due maintenance into estimate lines a customer can act on.
- Coverage must be decided before a price is shown (warranty, recall, service contract, prepaid plan, goodwill, or customer pay).
- Lines are being presented, approved, declined, or deferred, and the outcome must feed follow-up and KPIs.

## Framework Selection

```
What is the source of the proposed line?
├─ Customer concern (verbatim complaint) → diagnose first; no price until a cause is found
├─ Inspection finding (MPI item yellow/red) → requires evidence attached; urgency from the finding, not from sales pressure
├─ Open recall / campaign → coverage is recall; customer pay is never allowed for the recall labor
├─ Maintenance schedule (mileage/time due) → menu-priced; eligible for auto-presentation under threshold
└─ Internal (recon, PDI, used-car prep) → line_type internal; no customer approval step; links to the disposition decision
```

## Response Format

- Lead with the estimate decision: what is presented, what waits for advisor review, what is blocked and why (≤3 sentences).
- Then: per-line table (finding → labor op → parts → coverage → price source → urgency) → totals with tax basis → customer explanation → follow-up plan.
- Use the customer's own inspection terms for urgency: **now / soon / monitor**. Never "dangerous", "unsafe to drive", or "could fail any time" unless a recall or a red safety item literally says so.
- The decision trees in this skill are for internal reasoning only. Apply them to reach the estimate, but do not reproduce them in the response. Present the estimate, its sources, and the caveats.

## Core Procedure

1. **Anchor every line to a finding.** Each line carries `finding_ref` pointing at exactly one of: inspection item id, DTC code, recall id, maintenance-schedule row, or customer concern id. A line with no `finding_ref` is not an estimate line; discard it.
2. **Require evidence for yellow and red items.** At least one photo, video, or measurement per yellow/red finding. If missing, return the line to the technician; do not price it.
3. **Resolve the labor operation.** Map the repair to a labor op with flat-rate hours from the labor guide and record the guide edition as `source_ref`. If no op exists, mark hours `estimated` and require advisor sign-off.
4. **Resolve parts.** Look up parts by VIN and labor op. Record price `source_ref`, on-hand quantity, supersession chain, and ETA for anything not in stock. A superseded part number is replaced, never quoted.
5. **Check coverage in this order, stop at the first match:**
   ```
   Open recall / campaign covers the repair?          → coverage = recall (customer pay 0)
   Factory or powertrain warranty in time and miles?  → coverage = warranty (confirm failure type is covered)
   Service contract / VSC sold on the deal?           → coverage = service_contract (apply deductible)
   Prepaid maintenance plan covers this menu item?    → coverage = service_contract (plan)
   Goodwill eligible (just out of warranty, loyal)?   → coverage = goodwill (needs manager approval)
   Otherwise                                          → coverage = customer_pay
   ```
6. **Assign line type** from coverage: `customer_pay`, `warranty`, `recall`, `internal` (recon/PDI), `sublet` (outside vendor; record vendor and cost). Mixed coverage on one repair becomes two lines, never a blended price.
7. **Compute totals in a sandbox**, not in prose: labor × rate + parts, then tax on labor vs parts per market rules (US: parts taxable, labor varies by state; DE: 19% VAT on both). Retain inputs, code, and result as the audit record.
8. **Rank by urgency from the finding:** red safety → now; yellow wear at threshold → soon; yellow monitor-only → monitor. Never promote urgency to raise the total.
9. **Build good / better / best only from real alternatives** (OEM vs aftermarket part, repair vs replace assembly, now vs bundle with due maintenance). No option may include work without a `finding_ref`.
10. **Apply the repair-vs-replace check.** If the customer-pay total ≥ `repair_vs_replace_threshold_pct` (default 50%) of the vehicle's current valuation, attach the equity view and offer a sales conversation. Do not withhold the repair option.
11. **Decide what may auto-present.** Maintenance-schedule and recall lines under the dealer's `max_amount` threshold may be sent to the customer without waiting for the advisor. Inspection-derived and concern-derived lines wait for advisor review.
12. **Present line by line.** The customer sees each line's finding, evidence, urgency, coverage, and price, and decides per line. A single "approve all" is allowed only alongside per-line control.
13. **Deliver the approval link within 60 seconds** of presentation and target a decision inside 20 minutes while the vehicle is in the bay. Respect channel consent and quiet hours.
14. **Record the decision with `decided_by`.** Customer decisions come from the customer's own action; advisor-recorded verbal decisions carry `decided_by = advisor` and a reason. Agents never approve on the customer's behalf.
15. **Dispatch exactly the approved lines.** Declined and deferred lines go to the declined-work register with their original evidence, price, and date.
16. **Schedule follow-up for declined work** at 30 / 60 / 90 days using service templates (not sales templates); re-present the original finding and evidence. Stop when the customer books or opts out.
17. **Report the two rates separately:** presentation rate = presented ÷ found; approval rate = approved ÷ presented. Collapsing them hides the lever most stores are missing.
18. **Cite everything.** Each price, hour, and coverage decision in the output names its `source_ref`. If a source is missing, say so instead of filling the gap.

## Reference Tables

| Coverage | Customer pays | Needs | Line type |
|---|---|---|---|
| Recall / campaign | Nothing for covered labor and parts | Recall id, VIN in scope | recall |
| Factory / powertrain warranty | Nothing if failure type covered | In time and miles; failure not excluded (wear, abuse) | warranty |
| Service contract / VSC | Deductible | Contract active; component listed | warranty (contract) |
| Prepaid maintenance | Nothing for plan items | Plan active; item on menu | warranty (plan) |
| Goodwill | Partial or nothing | Manager approval; reason recorded | warranty (goodwill) |
| Customer pay | Full | Presented and approved | customer_pay |

| Urgency | Meaning in inspection terms | Typical triggers |
|---|---|---|
| now | Red: safety or imminent failure | Brake pads ≤ 2 mm, tread ≤ 2/32", active leak, red DTC |
| soon | Yellow at threshold | Pads 3–4 mm, tread 3–5/32", battery SoH near limit |
| monitor | Yellow, not yet at threshold | Early wear, minor seepage, advisory DTC |

| KPI | Industry baseline | Target | Why it matters |
|---|---|---|---|
| Findings presentation rate | ~30% | ≥ 90% | Roughly 70% of findings are never shown |
| Approval rate when presented | ~50% | ≥ 60% | Evidence and plain explanation lift it |
| Declined-work conversion in 90 days | < 10% (most never re-contacted); 15–20% when re-contacted | ≥ 20% | Follow-up coverage is the lever |
| Approval turnaround | hours | < 20 min | Vehicle is still in the bay |

## Worked Example

Vehicle: 2023 compact SUV, 41,200 mi, in for 40k service. MPI: front pads 3 mm (yellow, photo), rear tires 4/32" (yellow, photo), cabin filter due (schedule), open recall for a seat-belt pretensioner.

| Line | finding_ref | Labor op / hrs | Parts | Coverage | Price source | Urgency |
|---|---|---|---|---|---|---|
| Seat-belt pretensioner | RECALL-24V-118 | R-4410 / 0.8 | kit, on hand | recall | OEM campaign | now |
| Front brake pads + rotors | MPI-07 (3 mm, photo) | B-2210 / 1.4 | pads + rotors, 1 rotor on order ETA 15:00 | customer_pay | labor guide 2026-Q2; parts price 2026-09-01 | soon |
| Rear tires (2) | MPI-12 (4/32", photo) | T-1100 / 0.6 | 2 tires, on hand | customer_pay | tire catalog | soon |
| 40k service (cabin filter, rotation, inspection) | SCHED-40K | M-0400 / 0.7 | filter, on hand | service_contract (prepaid plan) | plan terms | now |

Auto-presented immediately: recall and 40k service (covered, under threshold). Waiting for advisor: pads and tires (inspection-derived). Customer approves pads, defers tires with the note "before winter"; tires enter the declined register with the 4/32" photo and a 60-day reminder. Presentation rate this RO: 4/4. Approval when presented: 3/4.

## Common Mistakes

1. **Wrong:** Pricing a yellow or red finding that has no photo or measurement.
   **Right:** Return it to the technician; evidence first, price second.
   **Why:** Approval rates depend on evidence, and an unevidenced red item is indefensible.

2. **Wrong:** Quoting customer pay for work that an open recall covers.
   **Right:** Run the coverage order; recall wins, always.
   **Why:** Charging for recall work is a compliance failure, not a pricing choice.

3. **Wrong:** Blending warranty and customer-pay portions into one line price.
   **Right:** Split into two lines with separate coverage and totals.
   **Why:** The customer must see what they pay for; the OEM claim must match what was covered.

4. **Wrong:** "Your brakes are dangerous, you shouldn't drive it."
   **Right:** "Front pads measure 3 mm (photo attached); replacement is recommended soon, before they reach 2 mm."
   **Why:** Scare language fails guardrails and lowers trust; measurements persuade.

5. **Wrong:** Approving on the customer's behalf because "they always say yes."
   **Right:** Present; wait for the customer's decision or record the advisor's verbal confirmation with `decided_by = advisor` and a reason.
   **Why:** Approval belongs to the customer by design; the record must show who decided.

6. **Wrong:** Reporting one "close rate" for recommended work.
   **Right:** Report presentation rate and approval-when-presented separately.
   **Why:** A 35% close rate can hide a 30% presentation rate and a healthy 50% approval rate; the fix is different.

7. **Wrong:** Logging a declined line as lost and moving on.
   **Right:** Write it to the declined register with evidence and schedule 30/60/90-day service reminders.
   **Why:** 15–20% of decliners approve within 90 days when re-contacted; almost none do when they are not.

8. **Wrong:** Computing totals in the response text.
   **Right:** Compute in a sandbox with retained inputs, code, and result; quote the result.
   **Why:** Tax on labor vs parts differs by market and state; arithmetic in prose is unauditable.

9. **Wrong:** Quoting a superseded part number because it is in the catalog.
   **Right:** Follow the supersession chain to the current number and quote that.
   **Why:** The old number cannot be ordered; the estimate would be wrong on arrival.

10. **Wrong:** Including a "best" option that adds work with no finding behind it.
    **Right:** Good/better/best differ in parts grade, scope of assembly, or bundling of due items, never in invented findings.
    **Why:** Any line without a `finding_ref` is fabrication.

11. **Wrong:** Skipping the repair-vs-replace check because it might lose the repair.
    **Right:** Attach the equity view when the total crosses the threshold and let the customer choose.
    **Why:** A customer who learns after paying that a trade made more sense does not come back.

12. **Wrong:** Sending the approval link to a channel the customer opted out of.
    **Right:** Use the consented channel; if none, the advisor presents in person.
    **Why:** Consent and quiet hours are hard rules above any autonomy setting.

## When to Escalate to a Human

- Goodwill requests, any warranty failure-type dispute, and any line where labor hours are `estimated` rather than guide-sourced.
- Customer pushback on necessity or price beyond the second exchange.
- Any estimate where the repair-vs-replace check fires; the sales handoff is a human conversation.

## References

- Intelligent Sales & Service Orchestration requirements spec v1.3 §3.7 (A7 tools), §5.20.4 (Estimate & Approval), §1.3 (KPI sources)
- Cox Automotive Service Industry Study 2025; BG Products service-lane presentation research; DealerDepot declined-services follow-up (2026)
- Tire and brake thresholds: typical OEM multi-point inspection forms; confirm against the OEM's MPI specification
