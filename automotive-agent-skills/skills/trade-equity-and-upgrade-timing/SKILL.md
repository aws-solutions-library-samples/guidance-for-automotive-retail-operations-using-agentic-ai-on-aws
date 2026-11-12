---
name: trade-equity-and-upgrade-timing
description: >
  Reasoning skill for computing a customer's trade-in equity position, deciding whether and when an
  upgrade conversation is warranted, framing negative equity honestly, and triggering the repurchase/upgrade
  handoff (service to next sale) from repair-vs-replace, lease-end, or mileage signals. Use when an agent values a trade, compares
  valuation to payoff, computes payment delta into a target vehicle, decides keep / CPO / wholesale
  disposition, monitors owned vehicles for the upgrade moment, or recommends acquiring a service-drive
  vehicle for used inventory. Triggers include "trade-in value", "trade equity", "negative equity",
  "positive equity", "payoff", "upside down", "roll negative equity", "payment delta", "lease end",
  "lease-end window", "over mileage", "excess mileage", "residual value", "repair vs replace", "upgrade
  prospect", "equity mining", "service drive acquisition", "disposition", "CPO", "wholesale", "book value",
  "valuation range", "condition adjustment".
usage: Invoke when reasoning about a trade valuation, equity, payment delta, upgrade timing, or a repurchase/upgrade handoff (service to next sale) for an owned vehicle.
version: 1.0.0
tags: [skill, category:reasoning, retail, trade-in, used-vehicle, automotive]
---

# Trade Equity and Upgrade Timing — Reasoning Skill

## When to Use

- A journey reaches TRADE_IN, or an owner asks "what is my car worth" or "should I trade now".
- A service visit produces fresh mileage or condition that may change equity, or an estimate approaches the vehicle's value.
- The used-car desk must decide what to do with a trade or whether to buy a vehicle out of the service lane.

## Framework Selection

```
What is the question?
├─ Value this vehicle → valuation range (low / mid / high), source, expiry, condition adjustments
├─ Can this customer afford to move → equity = valuation − payoff; payment delta into the target with incentives and offer
├─ Is now the moment → upgrade triggers: positive equity crossed, lease-end ≤ 6 months, mileage over allowance,
│     repair estimate ≥ 50% of value, high-mileage service pattern
├─ What do we do with the vehicle → disposition: retail as CPO, retail as-is, wholesale, auction
└─ Should we buy it → acquisition: does it fit stock need (days supply by model) and margin at the offer
```

## Response Format

- Lead with the equity position (positive or negative, amount, confidence) and whether an upgrade conversation is warranted now (≤3 sentences).
- Then: valuation table (source, range, expiry, adjustments) → payoff and equity → payment delta scenarios → trigger status → recommended next step and who has it.
- Frame negative equity as a number and a set of options, never as a judgment. Decision trees are for internal reasoning only; do not narrate them.

## Core Procedure

1. **Identify the vehicle exactly:** VIN (decode year/make/model/trim), current mileage with `as_of` date, condition grade, options that move value, title status, and open recalls. A valuation on the wrong trim is worthless.
2. **Pull a valuation range from a named source** (dealer valuation provider, sandbox equivalent): low / mid / high, with `source`, `retrieved_at`, and `expires_at`. Quote the range and the source; never a single unsourced "book value".
3. **Apply condition adjustments explicitly** (tires, brakes, glass, paint, accident history, service history). Each adjustment is a line with a reason. Inspection findings from the service lane are the best condition input; use them when present.
4. **Get the payoff from the lender or DMS,** dated. A payoff more than 10 days old is stale; refresh before presenting equity.
5. **Compute equity = mid valuation − payoff.** Report also at low and high so the customer sees the range. Equity is a range, not a point.
6. **Classify:**
   ```
   equity ≥ 10% of mid value                  → strongly positive: upgrade conversation warranted
   0 ≤ equity < 10%                           → marginally positive: warranted with a target that fits payment
   −10% < equity < 0                          → marginally negative: show options; often manageable with incentives / longer term
   equity ≤ −10%                              → deeply negative: show honestly; rolling it needs lender LTV headroom and the customer's eyes open
   ```
7. **Lease vehicles:** equity = market value − (residual + remaining payments + purchase-option fee). Compare with the lease-end alternatives: return (with excess mileage and wear charges), buy out, or trade early. Mileage over allowance at the projected end is a trigger by itself.
8. **Compute payment delta** into a concrete target: target price − incentives − equity (or + negative equity) at the lender offer for the customer's tier, over the same or a stated term. Show at least two terms. Run the math in a sandbox with inputs, code, and result retained.
9. **Check lender constraints when rolling negative equity:** loan-to-value ceilings, tier limits, and GAP relevance. If it does not fit, say so and show what would (larger down payment, different target, wait).
10. **Evaluate the upgrade triggers** on every fresh mileage or valuation:
    - positive equity crossed (from ≤ 0 to > 0),
    - lease-end within 6 months,
    - projected excess mileage at lease end,
    - repair estimate ≥ 50% of mid value (repair-vs-replace),
    - service frequency or cost pattern rising for age.
    Emit the trigger; do not contact the customer from this skill.
11. **Repair-vs-replace:** present the repair as a legitimate option alongside the trade. Show repair cost, post-repair value, equity after repair, and the payment delta into a replacement. The customer chooses.
12. **Repurchase/upgrade handoff** (service to next sale) only with the evidence attached (valuation, payoff, trigger, estimate) and through the journey system, so the purchase journey is created with `source = service_lane` and a link to the prior journey. Never message the customer with a sales pitch from the service lane directly.
13. **Disposition:** decide retail-CPO / retail-as-is / wholesale / auction from age, mileage, condition, CPO eligibility, reconditioning estimate, and the store's days supply for that model. Record the reasoning.
14. **Acquisition from the service drive:** recommend an offer only when the vehicle fits a stock need (days supply below target for the model) and the offer leaves margin after reconditioning. Default autonomy is a recommendation to the used-car manager, not an automatic offer.
15. **Respect valuation expiry.** A range past `expires_at` is re-pulled before any customer-facing figure.
16. **Cite every number** to its source (valuation provider, lender payoff, incentive record, offer id). Missing source, no figure.

## Reference Tables

| Signal | Threshold | Action |
|---|---|---|
| Equity crossed positive | equity > 0 after ≤ 0 | Upgrade Pitch play at the dealer's autonomy level |
| Lease-end window | ≤ 6 months to maturity | Present return / buy / trade comparison |
| Excess mileage | projected > allowance at maturity | Quantify charge; compare to early trade |
| Repair-vs-replace | customer-pay estimate ≥ 50% of mid value | Attach equity view to the estimate; offer sales conversation |
| Stale payoff | > 10 days | Refresh before showing equity |
| Stale valuation | past `expires_at` | Re-pull |

| Disposition | Typical fit |
|---|---|
| Retail CPO | Age and mileage within CPO program; recon within budget; model in demand |
| Retail as-is | Outside CPO but clean; demand exists |
| Wholesale | Outside retail profile, or model over-stocked |
| Auction | Condition or history unsuitable for retail |

## Worked Example

Owner of a 2021 mid-size SUV, 61,000 mi (fresh from today's RO), lease ends in 5 months, allowance 45,000 mi total; today's estimate: struts and tires, customer pay $2,850. Valuation mid $23,400 (range $22,100–$24,600, provider X, expires in 14 days). Lease residual $19,900 + 5 remaining payments $2,350 + purchase option $350 = $22,600 → lease equity ≈ +$800 at mid (range −$500 to +$2,000). Projected mileage at maturity ≈ 63,500 → 18,500 over allowance at $0.25/mi ≈ $4,600 excess charge on return. Repair-vs-replace: $2,850 ÷ $23,400 = 12%, below the 50% threshold; not a trigger. Lease-end and excess-mileage triggers fire. Recommendation: present the three lease-end paths with the excess-mileage charge quantified; early trade avoids the charge and uses the marginal equity; the repair remains a valid option if the customer keeps the vehicle. Handoff to sales with evidence; the service advisor does not pitch.

## Common Mistakes

1. **Wrong:** Quoting a single "book value" with no source or range.
   **Right:** Range with provider, retrieval date, and expiry.
   **Why:** Valuations move weekly; a point estimate misleads and is unauditable.

2. **Wrong:** Computing equity against a payoff from last month.
   **Right:** Refresh the payoff; interest accrues and payments post.
   **Why:** Stale payoffs overstate or understate equity by hundreds.

3. **Wrong:** Valuing the base trim when the VIN decodes to a premium trim (or the reverse).
   **Right:** Decode the VIN; confirm options that move value.
   **Why:** Trim error is the largest single valuation mistake.

4. **Wrong:** Hiding negative equity inside the new payment.
   **Right:** Show the negative equity as a number and the options for handling it.
   **Why:** Concealed negative equity is a trust and compliance failure.

5. **Wrong:** Rolling negative equity without checking lender LTV headroom.
   **Right:** Check the tier's LTV ceiling; if it does not fit, show what would.
   **Why:** A deal the lender will not fund wastes everyone's time.

6. **Wrong:** Treating lease equity as market value minus residual.
   **Right:** Include remaining payments and the purchase-option fee.
   **Why:** The buyout is the whole obligation, not the residual alone.

7. **Wrong:** Skipping the excess-mileage projection because the customer is under allowance today.
   **Right:** Project to maturity from the mileage trend.
   **Why:** The charge is assessed at return; today's number is not the answer.

8. **Wrong:** Withholding the repair option once repair-vs-replace fires.
   **Right:** Show repair, repair-then-keep equity, and the replacement side by side.
   **Why:** The customer chooses; steering them is not advising them.

9. **Wrong:** Texting the owner a trade offer from the service lane.
   **Right:** Emit the trigger with evidence; the sales journey and its consent rules handle contact.
   **Why:** Consent, quiet hours, and channel rules live in the journey system, not the service desk.

10. **Wrong:** Recommending acquisition of every clean service-drive vehicle.
    **Right:** Only when days supply for that model is below target and the offer leaves margin after recon.
    **Why:** Buying inventory the store does not need is a cost, not an opportunity.

11. **Wrong:** Using a valuation past its expiry because it is "close enough".
    **Right:** Re-pull; expiries exist because the market moved.
    **Why:** An expired figure on a customer document is a liability.

12. **Wrong:** Ignoring inspection findings when grading condition.
    **Right:** Use today's MPI: tires, brakes, leaks, and damage are the condition.
    **Why:** The best condition data in the building is the inspection you already have.

## When to Escalate to a Human

- Deeply negative equity (≤ −10%) with a customer under payment stress: F&I manager and, where applicable, the customer's own advisor.
- Title issues, undisclosed accident history, open liens, or odometer discrepancies.
- Any acquisition offer above the used-car manager's standing authority.

## References

- Intelligent Sales & Service Orchestration requirements spec v1.3 §3.4 (A4 tools incl. `monitor_equity`, `recommend_acquisition`), §2.1.1 (repurchase/upgrade handoff, the vehicle lifecycle loop), §3.7 (`handoff_to_sales`), §5.6 ext. (Inventory & Trade Desk), §4.10.1 (autonomy defaults)
- Valuation sources per market configuration (dealer valuation provider; sandbox fake in development)
- Cox Automotive Service Industry Study 2025 (service-lane retention context)
