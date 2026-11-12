---
name: multi-point-inspection-triage
description: >
  Reasoning skill for triaging dealer multi-point inspection (MPI) results, diagnostic trouble codes,
  and technician notes into evidenced, prioritized findings, including the EV-specific inspection
  template and technician-role constraints. Use when an agent validates an inspection submission,
  assigns green / yellow / red status from measurements, decides which findings need evidence, maps
  a DTC to likely causes and a labor operation, prioritizes findings by urgency, dispatches work by
  technician certification, or prepares findings for the estimate. Triggers include "multi-point
  inspection", "MPI", "vehicle inspection", "inspection template", "green yellow red", "tread depth",
  "brake pad thickness", "battery state of health", "SoH", "DTC", "diagnostic trouble code", "TSB",
  "technical service bulletin", "repair procedure", "labor operation", "technician dispatch",
  "HV certified", "high voltage", "EV inspection", "charge port", "inspection evidence", "walk-around".
usage: Invoke when validating or prioritizing inspection findings, diagnosing from DTCs, choosing an inspection template, or dispatching inspection work to technicians.
version: 1.0.0
tags: [skill, category:reasoning, fixed-ops, service, ev, automotive]
---

# Multi-Point Inspection Triage — Reasoning Skill

## When to Use

- A technician submits (or an agent must validate) an MPI with item statuses, measurements, and notes.
- A customer concern or a red finding needs a cause: DTC lookup, related TSBs, tests, and a labor op.
- Work must be dispatched to technicians and the vehicle type (ICE, hybrid, EV) changes what is inspected and who may touch it.

## Framework Selection

```
Which inspection template applies?
├─ Battery-electric vehicle → EV template: HV battery SoH, charge port and pins, HV cable insulation,
│     coolant loops (battery + power electronics), regen brake wear pattern, 12 V auxiliary battery;
│     NO engine oil, spark, exhaust, or fuel items
├─ Plug-in or conventional hybrid → Hybrid template: ICE items + HV battery SoH + regen brake wear
├─ ICE → Standard template: fluids, filters, belts, brakes, tires, suspension, exhaust, battery
└─ Express service (menu only) → No MPI; concern-driven checks only
Is the visit a recall-only or express visit? → Do not require a full MPI; record `mpi_not_applicable`.
```

## Response Format

- Lead with the triage result: how many findings by urgency, which need evidence or a second look, and any dispatch constraint (≤3 sentences).
- Then: findings table (item → measurement → status → evidence → urgency → next step) → DTC diagnosis (code → likely causes → tests → labor op, with TSB citations) → dispatch notes.
- The decision trees and thresholds in this skill are for internal reasoning only. Apply them, but do not narrate them. Present findings, evidence status, and the recommended next step.

## Core Procedure

1. **Select the template by powertrain** (see Framework Selection). An EV inspected on the ICE template is invalid; reject and re-open on the EV template.
2. **Validate completeness.** Every template item has a status. Items marked "not inspected" need a reason (component not accessible, express visit).
3. **Derive status from the measurement, not the checkbox.** Where a measurement exists, the status is computed from it (tables below). A yellow checkbox with a red measurement becomes red.
4. **Enforce the evidence rule.** Every yellow or red item has at least one photo, video, or numeric measurement attached. Missing evidence returns the item to the technician; the inspection cannot be submitted as complete.
5. **Check consistency across items.** Uneven tread wear (>2/32" difference across an axle) with no alignment or suspension finding is a gap; ask for a look. Brake pad wear asymmetry left vs right suggests a caliper issue.
6. **Map each customer concern to a diagnostic path:** reproduce → scan for DTCs → pull freeze-frame → lookup code → related TSBs → recommended tests → confirm cause → labor op. Do not price a concern before the cause is confirmed.
7. **Look up DTCs by code and vehicle,** not by code alone. The same P- or U-code has different likely causes by make and model family. Cite the reference row and any TSB used.
8. **Rank likely causes by prior and cost of test.** Test the cheap, common cause first unless a safety item points elsewhere. Record tests performed and results.
9. **Treat pending and history codes differently from current codes.** A history code with no current fault and no symptom is `monitor`, not a repair line.
10. **Assign urgency:**
    ```
    Red safety item (brakes ≤ 2 mm, tread ≤ 2/32", active fluid leak on brake/fuel, HV insulation fault, red airbag/ABS/HV DTC) → now
    Yellow at threshold (pads 3–4 mm, tread 3–5/32", battery SoH within 5 points of limit, coolant slightly low) → soon
    Yellow below threshold or advisory (early wear, minor seepage, pending DTC) → monitor
    ```
11. **Never inflate urgency.** A monitor item stays monitor; the estimate skill decides presentation, not the triage.
12. **EV high-voltage gate.** Any item touching the HV system (battery pack, orange cabling, inverter, charge port internals, HV coolant loop that requires de-energizing) is dispatched only to a technician with a current high-voltage certification. This gate is not configurable.
13. **Dispatch by skill, capacity, promised time, and parts.** Match required skills to technician certifications; sequence so that ROs with parts on hand and near promised times go first; hold ROs waiting on parts.
14. **Protect technician data scope.** Technician views show VIN, model, tag, concerns, and lines. They do not show customer contact data, pricing margins, or coverage cost splits. Enforce at the data layer, not by hiding fields.
15. **Record the inspection as immutable once submitted.** Corrections are new versions with a reason; the original stays.
16. **Hand findings to the estimate step** with `finding_ref`, measurement, evidence ids, urgency, and the labor op candidate. The triage does not price.
17. **Track presentation.** A finding that never reaches the customer is recorded as `not_presented` with a reason; this feeds the presentation-rate KPI (industry ~30%).
18. **On EVs, report battery SoH with the measurement method** (BMS-reported, or capacity test) and the warranty threshold for that model (commonly 70% at 8 years / 100k miles; confirm per OEM). Do not call a battery "failing" above the warranty threshold.

## Reference Tables

| Tire tread depth | Status | Note |
|---|---|---|
| ≥ 6/32" (≥ 4.8 mm) | green | |
| 3–5/32" (2.4–4.0 mm) | yellow | Recommend soon; winter or wet climate may move 4/32" to now |
| ≤ 2/32" (≤ 1.6 mm) | red | Legal minimum in most US states and the EU |

| Brake pad thickness | Status |
|---|---|
| ≥ 5 mm | green |
| 3–4 mm | yellow |
| ≤ 2 mm | red |

| 12 V battery (conductance test) | Status |
|---|---|
| ≥ 80% of rated CCA, no fault | green |
| 60–79% or "charge and retest" | yellow |
| < 60% or "replace" | red |

| EV item | Green | Yellow | Red |
|---|---|---|---|
| HV battery SoH | > warranty threshold + 5 pts | within 5 pts of threshold | below threshold, or BMS fault |
| Charge port | pins clean, latch ok, no heat marks | corrosion or wear, slow charge reports | heat damage, arcing marks, no lock |
| HV insulation | no isolation fault | intermittent isolation warning | active isolation fault (do not energize) |
| Coolant loops | levels correct | slightly low, top up | leak or contamination |

| DTC prefix | System | Typical first checks |
|---|---|---|
| P0xxx | Powertrain, generic | Freeze frame, related sensors, TSBs for the platform |
| P1xxx / P3xxx | Powertrain, manufacturer-specific | OEM service info required; do not guess from generic tables |
| B | Body (airbag, seat, HVAC) | Airbag codes are red until cleared by procedure |
| C | Chassis (ABS, ESC, steering) | Wheel-speed sensors and wiring before modules |
| U | Network / communication | Power and ground to the module before replacing it |

Thresholds above are typical of OEM multi-point inspection forms; where the OEM's own MPI specification differs, the OEM form wins.

## Worked Example

2022 EV crossover, 38,400 mi, concern "charging stops at 80% on DC fast charge". EV template. Findings: HV battery SoH 94% (BMS, warranty threshold 70%) → green; charge port pins show discoloration on DC+ → yellow with photo; pending DTC U11xx (charge communication, manufacturer-specific) → lookup by VIN → TSB 24-EV-031 "DC charge port pin wear causes early termination" → recommended test: pin resistance → labor op E-3320 (charge port assembly) → dispatch to the HV-certified technician only. Tires 6/32" all around → green. 12 V battery 78% → yellow, retest. Result: 1 soon (charge port, TSB-cited, HV-gated), 1 monitor (12 V battery), rest green; the SoH is reported as 94% against a 70% threshold, not as "battery degradation".

## Common Mistakes

1. **Wrong:** Inspecting an EV on the ICE template and marking oil items "n/a".
   **Right:** Use the EV template so HV battery, charge port, and coolant loops are actually inspected.
   **Why:** The items that fail on EVs are absent from the ICE form.

2. **Wrong:** Accepting a yellow or red item with no photo or measurement.
   **Right:** Return it to the technician; evidence is mandatory for yellow and red.
   **Why:** Unevidenced findings are not presented, or are presented and declined.

3. **Wrong:** Trusting the checkbox when the measurement says otherwise.
   **Right:** Compute status from the measurement; the checkbox is a hint.
   **Why:** Inconsistent status undermines the whole inspection.

4. **Wrong:** Diagnosing a manufacturer-specific P1xxx code from a generic OBD table.
   **Right:** Look up by code and vehicle, cite the OEM reference or TSB.
   **Why:** Manufacturer-specific codes differ by make; generic tables are wrong often enough to matter.

5. **Wrong:** Replacing a module for a U-code before checking power and ground.
   **Right:** Verify supply, ground, and network integrity first.
   **Why:** Most communication codes are wiring or power, not the module.

6. **Wrong:** Dispatching charge-port or pack work to the next free technician.
   **Right:** HV work goes only to a technician with current HV certification; if none is on shift, hold the RO.
   **Why:** Safety and OEM policy; the gate is not a scheduling preference.

7. **Wrong:** Calling a battery at 88% SoH "degraded" or "failing".
   **Right:** Report SoH, method, and the warranty threshold; classify against the threshold.
   **Why:** Normal degradation is not a defect; alarming language fails guardrails and misleads.

8. **Wrong:** Treating a history DTC with no symptom as a repair line.
   **Right:** Record as monitor; note it for the next visit.
   **Why:** Repairing history codes is selling work with no current fault.

9. **Wrong:** Promoting a monitor item to soon to fill the estimate.
   **Right:** Urgency comes from the measurement; the estimate skill decides presentation.
   **Why:** Inflated urgency is the fastest way to lose the customer and the audit.

10. **Wrong:** Showing the technician the customer's phone number and the margin on parts.
    **Right:** Technician scope is vehicle and work only; enforce server-side.
    **Why:** Data minimization and role separation are compliance requirements, not UI preferences.

11. **Wrong:** Overwriting an inspection after submission to fix a mistake.
    **Right:** Create a new version with a reason; keep the original.
    **Why:** The inspection is evidence; evidence is not edited in place.

12. **Wrong:** Ignoring uneven tread across an axle because each tire is individually green.
    **Right:** Flag the pattern and ask for an alignment or suspension look.
    **Why:** Patterns across items are where experienced technicians find the real fault.

## When to Escalate to a Human

- Any HV isolation fault, airbag code, or brake hydraulic finding: technician and shop foreman decide before anything else proceeds.
- Any diagnosis where tests contradict the TSB or the reference row.
- Any inspection where the technician disputes a computed status.

## References

- Intelligent Sales & Service Orchestration requirements spec v1.3 §3.7 (A7 `record_inspection`, `prioritize_findings`, `lookup_dtc`, `dispatch_ro`), §5.20.3, §5.20.5 (technician masking), §4.2 (`DEALER_InspectionTemplates`, `DEALER_DTCReference`)
- Typical OEM multi-point inspection forms for tire, brake, and battery thresholds; OEM service information and TSBs for manufacturer-specific DTCs
- EV battery warranty thresholds: OEM warranty booklets (commonly 70% capacity at 8 years / 100,000 miles in the US)
