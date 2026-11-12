---
name: ev-incentive-eligibility
description: >
  Reasoning skill for determining which vehicle purchase and EV incentives actually apply to a buyer,
  vehicle, and date, and for explaining expired versus active programs without quoting money that no
  longer exists. Covers the US federal landscape after the 30D / 25E credits ended for vehicles acquired
  after 2025-09-30, the 2025–2028 car-loan interest deduction, state and utility programs, and the German
  landscape after the Umweltbonus ended (E-Auto-Förderung 2026, Kfz-Steuer BEV exemption, THG-Quote,
  0.25% Dienstwagen taxation). Use when pricing a build, answering "what incentives do I get", structuring
  a payment with incentives, comparing EV vs ICE total cost of ownership, or checking a claim a customer
  read online. Triggers include "EV tax credit", "30D", "25E", "7500", "$7,500", "clean vehicle credit",
  "point of sale credit", "car loan interest deduction", "final assembly", "state EV rebate", "utility
  rebate", "charger rebate", "incentive stack", "Umweltbonus", "E-Auto-Förderung", "Kfz-Steuer", "THG-Quote",
  "Dienstwagen 0,25", "TCO", "total cost of ownership", "incentive eligibility", "MSRP cap", "income cap".
usage: Invoke when a price, payment, or comparison depends on which incentives apply to this buyer, vehicle, market, and date.
version: 1.0.0
tags: [skill, category:reasoning, retail, incentives, ev, automotive]
---

# EV and Purchase Incentive Eligibility — Reasoning Skill

## When to Use

- A build price, monthly payment, or EV-vs-ICE comparison must include incentives.
- A customer asks about a credit they read about, and the answer depends on the acquisition date and program status.
- An advisor needs to explain why a figure the customer expected no longer applies, and what does.

## Framework Selection

```
Which market?
├─ United States (market_id = us)
│   ├─ Federal: 30D / 25E ended for vehicles acquired after 2025-09-30 → treat as EXPIRED unless acquisition date is on or before
│   ├─ Federal: car-loan interest deduction, tax years 2025–2028, new vehicles with US final assembly → ACTIVE (income phase-outs apply)
│   ├─ State: program by state of registration (CO, NY, MA, IL and others) → check status, funding, and vehicle caps
│   └─ Utility: charger and off-peak rebates by electric utility → check service territory
└─ Germany (market_id = de)
    ├─ Umweltbonus ended 2023-12-17 → EXPIRED
    ├─ E-Auto-Förderung 2026 (socially graduated, up to EUR 6,000; applications since 2026-05-19, retroactive to 2026-01-01) → ACTIVE, income-tested
    ├─ Kfz-Steuer BEV exemption for first registrations through 2025; extension 2026–2030 ANNOUNCED → carry as announced, model TCO conservatively
    ├─ THG-Quote payout (annual, varies with certificate price) → ACTIVE, amount is an estimate
    └─ 0.25% Dienstwagen taxation for BEVs under the list-price cap → ACTIVE, company-car buyers only
```

## Response Format

- Lead with the incentives that apply to this buyer, vehicle, and date, with amounts and status (active / expired / announced) in ≤3 sentences.
- Then: eligibility table (program → status → amount → conditions met / not met → source) → how it changes the price or payment → what would change the answer (date, state, income, assembly).
- Always name expired programs the customer may expect, and say when they ended.
- Decision logic in this skill is for internal reasoning only; do not narrate the tree. State the result and its conditions.

## Core Procedure

1. **Fix the four inputs before anything else:** market and region (state or Bundesland), vehicle identity (model, trim, MSRP or list price, powertrain, final-assembly country, battery sourcing where relevant), buyer type (retail individual, business, lease, company car), and **acquisition or registration date** (contract date for US federal rules; first registration for German rules).
2. **Load incentive records for that market and region** with `status`, `valid_from`, `valid_to`, `source_urls`. Never rely on memory for amounts; programs change mid-year.
3. **Classify each record by status on the acquisition date:**
   ```
   valid_from ≤ date ≤ valid_to and status = active → applies if conditions met
   date > valid_to or status = expired               → EXPIRED: mention only to correct expectations
   status = announced                               → ANNOUNCED: show separately, exclude from payment math
   ```
4. **US federal 30D / 25E:** vehicles acquired after 2025-09-30 do not qualify (P.L. 119-21). For acquisitions on or before that date, apply the historical rules (MSRP caps of $80,000 for vans/SUVs/pickups and $55,000 for others; AGI caps; final assembly and battery-sourcing tests; point-of-sale transfer). Do not quote $7,500 for a purchase today.
5. **US car-loan interest deduction (2025–2028):** deduction for interest on a loan for a new personal-use vehicle with final assembly in the US, up to $10,000 of interest per year, phased out above income thresholds (single $100,000, joint $200,000; confirm current-year figures). It reduces taxable income, not the price; show it as an estimated annual tax effect, never as a discount off MSRP. Leases do not qualify.
6. **US state programs:** check program status and remaining funding, vehicle MSRP caps, new vs used, income tiers, and whether the rebate is point-of-sale or post-purchase. A program listed on a website may be paused for funding; the record's `status` governs.
7. **US utility programs:** identify the customer's electric utility by service address, not by state. Charger rebates usually require a qualified installer and a networked charger; some require enrolling in an off-peak rate.
8. **Germany E-Auto-Förderung 2026:** socially graduated by household income; up to EUR 6,000; retroactive to registrations from 2026-01-01; check income tier and vehicle list-price limits; applications opened 2026-05-19. Show as a post-registration grant unless the dealer is pre-financing it.
9. **Germany Kfz-Steuer:** BEVs first registered through 2025 are exempt for ten years (to 2030 at the latest). The extension for 2026–2030 registrations is announced but not enacted at the time of this skill; model TCO with the exemption flagged as announced and a conservative fallback (assume 50% of the standard rate) until enacted.
10. **Germany THG-Quote:** an annual payout for BEV owners via a quota trader; amount varies with the certificate price. Present a range and mark it estimate; do not fold it into the monthly payment.
11. **Germany company cars:** 0.25% of gross list price per month as the taxable benefit for BEVs under the list-price cap (0.5% above the cap or for PHEVs meeting range/CO2 conditions). Applies to employees with a company car, not retail buyers.
12. **Stack in the right order and avoid double counting:** point-of-sale credits and OEM cash reduce the capitalized cost; post-purchase grants and tax effects are shown separately as "after purchase"; a state rebate that excludes vehicles receiving another rebate is not stacked.
13. **Leases:** in the US, the lessor (captive) is the taxpayer; incentives appear as lease cash from the captive, if at all. In Germany, the E-Auto-Förderung eligibility for leases depends on term; check the record.
14. **TCO comparison:** use the market's default fuel and electricity prices for the region (US: EIA by state; DE: EC Oil Bulletin and Eurostat), the market's annual distance default, and only active incentives. Show announced items in a separate line.
15. **Cite every amount** to the incentive record and its source URL. If a record has `verify = true`, say the figure needs confirmation.
16. **Answer the expectation directly** when a customer names an expired program: state that it ended, the date, and what replaced it or applies instead.
17. **Never present an incentive as guaranteed** when eligibility depends on facts you do not have (income, tax liability, utility, employer). State the condition and the range.
18. **Re-check on date change.** If the contract date moves across a `valid_to` or a funding pause, recompute; the answer is date-dependent by construction.

## Reference Tables

| Program (US) | Status (Sep 2026) | Amount | Key conditions |
|---|---|---|---|
| 30D new clean vehicle credit | Expired for acquisitions after 2025-09-30 | was up to $7,500 | Historical only |
| 25E used clean vehicle credit | Expired for acquisitions after 2025-09-30 | was up to $4,000 | Historical only |
| Car-loan interest deduction | Active 2025–2028 | up to $10,000 interest/yr deductible | New, personal use, US final assembly; income phase-out; not leases |
| State rebates (e.g., CO, NY, MA, IL) | Varies; check funding | varies | MSRP caps, income tiers, POS vs post-purchase |
| Utility charger rebates | Varies by utility | varies | Service territory, qualified charger/installer |
| OEM cash / APR / lease specials | Per bulletin | varies | Model, region, dates |

| Program (DE) | Status (Sep 2026) | Amount | Key conditions |
|---|---|---|---|
| Umweltbonus | Expired 2023-12-17 | — | Historical only |
| E-Auto-Förderung 2026 | Active | up to EUR 6,000 | Income-graduated; list-price limit; registrations from 2026-01-01 |
| Kfz-Steuer BEV exemption | Active for ≤ 2025 registrations; 2026–2030 announced | 10-year exemption | Model 2026+ conservatively until enacted |
| THG-Quote | Active | estimate, varies annually | Via quota trader |
| 0.25% Dienstwagen | Active | tax benefit | Company cars under list-price cap |

## Worked Example

US buyer, Colorado, contract date 2026-10-02, new mid-size EV SUV, MSRP $47,900, US final assembly, financing $40,000 at 6.9% over 60 months, household income $150,000 joint.

- 30D: expired for this acquisition date → not applied; the customer's "$7,500" expectation is corrected with the 2025-09-30 end date.
- Car-loan interest deduction: qualifies (new, personal, US assembly, income under the joint phase-out); first-year interest ≈ $2,600 → estimated tax effect ≈ $570 at a 22% marginal rate, shown as "after purchase, estimate", not off MSRP.
- Colorado state EV tax credit: check record status and MSRP cap; if active and under cap, show amount and whether point-of-sale.
- Utility: Xcel Energy territory → charger rebate with qualified installer; shown as post-install.
- OEM: current APR special per bulletin reduces the payment; shown in the payment math.
Result: the capitalized cost reflects only OEM cash; the state credit, deduction, and utility rebate appear as after-purchase items with conditions and sources.

## Common Mistakes

1. **Wrong:** Quoting a $7,500 federal credit on a 2026 purchase.
   **Right:** State that 30D ended for vehicles acquired after 2025-09-30 and move to what applies.
   **Why:** The single most common wrong number in showroom conversations today.

2. **Wrong:** Showing the loan-interest deduction as a price reduction.
   **Right:** Show it as an estimated annual tax effect after purchase, with the income condition.
   **Why:** It reduces taxable income; presenting it as cash misstates the deal.

3. **Wrong:** Applying the interest deduction to a lease.
   **Right:** Leases do not qualify; check OEM lease cash instead.
   **Why:** The deduction is for loan interest on a purchased vehicle.

4. **Wrong:** Assuming a state rebate is available because the program page exists.
   **Right:** Check the record's status and funding; programs pause.
   **Why:** A paused program is an expired program for this customer.

5. **Wrong:** Picking the utility rebate by state.
   **Right:** Identify the utility by service address.
   **Why:** Utility territories do not follow state lines.

6. **Wrong:** Quoting the Umweltbonus to a German buyer.
   **Right:** It ended 2023-12-17; evaluate the E-Auto-Förderung 2026 by income tier.
   **Why:** Wrong program, wrong amount, wrong conditions.

7. **Wrong:** Modeling a 2027 German BEV registration as Kfz-Steuer-exempt for ten years as if enacted.
   **Right:** Show the exemption as announced and a conservative fallback in TCO until enacted.
   **Why:** TCO built on an announced measure overstates savings.

8. **Wrong:** Folding the THG-Quote into the monthly payment.
   **Right:** Present it as an annual, variable, estimated payout.
   **Why:** It is neither fixed nor part of the financing.

9. **Wrong:** Stacking a state rebate with a program it explicitly excludes.
   **Right:** Read the stacking rule on the record; apply the higher when exclusive.
   **Why:** Double counting produces a payment the customer cannot get.

10. **Wrong:** Using national average fuel and electricity prices in TCO.
    **Right:** Use the region's default from the market data (state, Bundesland) and say which.
    **Why:** Regional price spreads change the EV vs ICE answer.

11. **Wrong:** Presenting an income-tested incentive as certain without knowing income.
    **Right:** State the condition and show the range or the test.
    **Why:** Eligibility you cannot verify is not a promise you can make.

12. **Wrong:** Answering from memory when the record has `verify = true`.
    **Right:** Quote the recorded figure and flag it as needing confirmation.
    **Why:** Programs change mid-year; the record's flag exists for this reason.

## When to Escalate to a Human

- Tax outcomes for a specific customer (marginal rate, filing status) belong to a tax professional; present estimates only.
- Business and fleet buyers (Section 179, depreciation, company-car rules) beyond the tables above.
- Any incentive record marked `verify = true` that materially changes the decision.

## References

- Intelligent Sales & Service Orchestration requirements spec v1.3 §2.5, §2.5.1, §3.1 (`get_incentives`, `price_build`, `build_tco_comparison`); `sample-data/open/curated/us/incentives.json`, `sample-data/open/curated/de/incentives.json`, `sample-data/open/SOURCES.md`
- US: IRS guidance on 30D / 25E termination (P.L. 119-21, acquisitions after 2025-09-30); IRS final regulations on the car-loan interest deduction (2026-09-08); fueleconomy.gov tax credit lists (historical)
- Germany: BMWK / BAFA E-Auto-Förderung 2026 programme pages; Kraftfahrzeugsteuergesetz §3d; Umweltbundesamt THG-Quote guidance
- Energy prices: EIA (US), EC Weekly Oil Bulletin and Eurostat nrg_pc_204 (EU)
