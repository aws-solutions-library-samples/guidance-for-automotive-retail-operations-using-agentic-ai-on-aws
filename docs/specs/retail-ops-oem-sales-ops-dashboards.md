# OEM Sales Ops Dashboard — Requirements

> **Status:** Draft for review (2026-11-10). Companion to
> `intelligent-sales-service-orchestration.md` (v1.3), which stays authoritative. This
> document details the **District Sales vs Target** dashboard the main spec lists in
> §4.3 (A6) and builds it in Amazon Quick, like the rest of the Quick-native scope
> (`retail-ops-quick-project.md`).
>
> **Scope rule:** only OEM-owned data that is already in the Quick space
> `Automotive Retail Operations` (`retail-ops-space`). No new lake tables, no
> generator changes, no dealer-owned rows. Every dataset SQL below was run against the
> dev lake (`retail_ops_dev_lake`) on 2026-11-10 and returned the expected rows.

## 1. Purpose and audience

One dashboard, **Retail Ops OEM Sales Performance**, that answers the questions OEM
sales operations asks every week:

1. Are we on target, and is the trend getting better or worse?
2. Which dealers are slipping, and is it **volume**, **customer experience** or both?
3. Which models make the money, and where does the profit come from (front-end gross
   or back-end)?
4. When we lose registrations, did **the market shrink** or did **we lose share**?
5. How aggressive are competitors' offers, and does that track our share?
6. Did our campaigns move retail sales of the model they promoted?

| Persona | Quick group | Main use |
|---|---|---|
| District Manager | `retail-ops-oem-dm` | Weekly dealer triage and visit planning (sheets 1, 2, 4) |
| Regional Sales Manager | `retail-ops-oem-regional` | Objective tracking, market and model view (sheets 1, 3, 5) |
| C&I planner | `retail-ops-oem-ci-planning` | Competitive offers, campaign effect (sheets 5, 6) |
| OEM marketing | `retail-ops-oem-marketing` | Campaign effect, share (sheets 5, 6) |

## 2. Data in the OEM space

| Quick dataset (in the space) | Lake table | Grain | Rows per market | Columns used |
|---|---|---|---|---|
| `retail-ops-lake-rdr` · Retail Ops · lake_rdr | `lake_rdr` | dealer × month × model | 1,848 (11 dealers × 24 months × 7 models) | dealer_id, district_id, month, model_id, model_name, units, units_py, target_units, gross_per_unit, pvr |
| `retail-ops-lake-csi` · Retail Ops · lake_csi | `lake_csi` | dealer × month | 264 | csi_sales, csi_service, region_avg |
| `retail-ops-lake-marketintel` · Retail Ops · lake_marketintel | `lake_marketintel` | district × segment × month | US 120 (5 segments), DE 72 (3 segments) | our_registrations, total_segment_registrations, competitor_offers (JSON array: competitor, apr_pct, lease_monthly, cash_incentive) |
| `retail-ops-lake-campaigns` · Retail Ops · lake_campaigns | `lake_campaigns` | campaign | 6 | campaign_id, name, objective, status, model_id, district_id, budget, start_at, end_at |
| `retail-ops-campaign-roi-sql` · Retail Ops · campaign ROI | (custom SQL) | campaign × source × dealer | — | journeys, sold, spend_allocated (already dealer-aggregated) |

Facts that shape the design:

- **Months** run 2024-12 to the current month. The current month is in the data but
  **not complete**: every "latest" figure uses the last complete month.
- **One district per market:** DIST-07 (US, 11 dealers) and DIST-SW (DE, 11 dealers).
  Comparisons are dealer vs district, not district vs district.
- **All lake columns are typed STRING** in Glue; the datasets below cast them.
- **Not in the space:** dealer names, tier, powertrain, and the model → segment
  mapping. Dealers show as their id (D-101 …), models by `model_name`; share is
  analysed by segment, separately from model sales (§9).
- `region_avg` in `lake_csi` is the market average of all dealers for that month.
- Campaigns run Aug–Dec 2026; several promote the same model at the same time.

## 3. Global requirements

| ID | Requirement | Pass/fail check |
|---|---|---|
| DSH-G1 | Sheet-level filter controls on every sheet: **Market** (single select, default `us`), **Dealer** (multi select, default all). Sheets with model data add **Model**; sheet 5 adds **Segment** and **Competitor**. | Each control changes every visual on its sheet. |
| DSH-G2 | Markets are never added together. Money shows in the market currency without conversion: USD for `us`, EUR for `de`, chosen by a calculated currency label. | No visual shows a figure summed over both markets. |
| DSH-G3 | "Latest month" is parameter `pMonth`, default = last complete month (computed in the datasets as `is_complete`). Trend visuals show the 24 months but exclude the incomplete month. | On 2026-11-10 the KPI tiles cover 2026-10; no trend line ends in 2026-11. |
| DSH-G4 | Every visual names its source dataset in its subtitle and states "synthetic data". Row-level tables expose `source_ref` where the source has it. | Subtitle present on every visual. |
| DSH-G5 | Policy thresholds are parameters (§6), not literals. | Changing a parameter re-colours the visuals that use it. |
| DSH-G6 | Read-only for the four OEM groups; owned by `retail-ops-builders`; built and shared by `deploy_quick_content.py`, like `Retail Ops Campaign ROI & Funnel`. Quick content is not row-scoped (main spec §2.6, 2026-10-21): every allowed user sees every dealer. | Dashboard permissions match the existing dashboard's pattern. |

## 4. Datasets to build

Five custom-SQL datasets on the Athena data source `retail-ops-lake`, Direct query
(like the two existing ones). `{DATABASE}` is the lake database
(`retail_ops_<env>_lake`), as in `deploy_quick_content.py`. Add each to the space.

### D1 `retail-ops-oem-dealer-month` — Retail Ops · OEM dealer month

One row per market × dealer × month: RDR summed over models, joined to CSI, plus
rolling windows computed in SQL so the visuals stay simple.

```sql
WITH r AS (
  SELECT market, dealer_id, district_id, month,
         sum(CAST(units AS integer)) AS units,
         sum(CAST(units_py AS integer)) AS units_py,
         sum(CAST(target_units AS integer)) AS target_units,
         sum(CAST(units AS integer) * CAST(gross_per_unit AS double)) AS gross_total,
         sum(CAST(units AS integer) * CAST(pvr AS double)) AS pvr_total
  FROM {DATABASE}.lake_rdr GROUP BY 1, 2, 3, 4),
j AS (
  SELECT r.*, CAST(date_parse(r.month || '-01', '%Y-%m-%d') AS date) AS month_start,
         CAST(c.csi_sales AS double) AS csi_sales, CAST(c.csi_service AS double) AS csi_service,
         CAST(c.region_avg AS double) AS csi_region_avg
  FROM r LEFT JOIN {DATABASE}.lake_csi c
    ON c.market = r.market AND c.dealer_id = r.dealer_id AND c.month = r.month)
SELECT k.*,
       CAST(k.units_r3 AS double) / NULLIF(k.units_py_r3, 0) - 1 AS vs_py_r3,
       k.csi_sales - k.csi_region_avg AS csi_gap
FROM (
SELECT j.*,
       j.month_start < date_trunc('month', current_date) AS is_complete,
       sum(units) OVER (PARTITION BY market, dealer_id ORDER BY month
                        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS units_r3,
       sum(units) OVER (PARTITION BY market, dealer_id ORDER BY month
                        ROWS BETWEEN 5 PRECEDING AND 3 PRECEDING) AS units_prior_r3,
       sum(units_py) OVER (PARTITION BY market, dealer_id ORDER BY month
                           ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS units_py_r3,
       sum(CASE WHEN csi_sales < csi_region_avg THEN 1 ELSE 0 END)
           OVER (PARTITION BY market, dealer_id ORDER BY month
                 ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS csi_below_region_r3,
       sum(CASE WHEN units >= target_units THEN 1 ELSE 0 END)
           OVER (PARTITION BY market, dealer_id ORDER BY month
                 ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS target_hits_r12
FROM j) k
```

Columns: market, dealer_id, district_id, month (STRING), month_start (DATETIME), units,
units_py, target_units, gross_total, pvr_total, csi_sales, csi_service, csi_region_avg,
is_complete (BOOLEAN), units_r3, units_prior_r3, units_py_r3, csi_below_region_r3,
target_hits_r12, vs_py_r3, csi_gap. The last two are row-level so the watch flag
(§5) can be counted per dealer.

### D2 `retail-ops-oem-model-month` — Retail Ops · OEM model month

```sql
SELECT market, dealer_id, district_id, month,
       CAST(date_parse(month || '-01', '%Y-%m-%d') AS date) AS month_start,
       CAST(date_parse(month || '-01', '%Y-%m-%d') AS date) < date_trunc('month', current_date) AS is_complete,
       model_id, model_name,
       CAST(units AS integer) AS units, CAST(units_py AS integer) AS units_py,
       CAST(target_units AS integer) AS target_units,
       CAST(units AS integer) * CAST(gross_per_unit AS double) AS gross_total,
       CAST(units AS integer) * CAST(pvr AS double) AS pvr_total,
       source_ref
FROM {DATABASE}.lake_rdr
```

### D3 `retail-ops-oem-share` — Retail Ops · OEM market share

Share and its year-over-year split into a market effect and a share effect, computed
from the raw counts so the two effects add up exactly to the registration change.

```sql
WITH m AS (
  SELECT market, district_id, segment, month,
         CAST(date_parse(month || '-01', '%Y-%m-%d') AS date) AS month_start,
         CAST(our_registrations AS integer) AS our_reg,
         CAST(total_segment_registrations AS integer) AS total_reg
  FROM {DATABASE}.lake_marketintel),
p AS (
  SELECT m.*,
         lag(our_reg, 12) OVER (PARTITION BY market, district_id, segment ORDER BY month) AS our_reg_py,
         lag(total_reg, 12) OVER (PARTITION BY market, district_id, segment ORDER BY month) AS total_reg_py
  FROM m)
SELECT market, district_id, segment, month, month_start,
       month_start < date_trunc('month', current_date) AS is_complete,
       our_reg, total_reg, our_reg_py, total_reg_py,
       CAST(our_reg AS double) / NULLIF(total_reg, 0) AS share,
       CAST(our_reg_py AS double) / NULLIF(total_reg_py, 0) AS share_py,
       (total_reg - total_reg_py) * CAST(our_reg_py AS double) / NULLIF(total_reg_py, 0) AS market_effect,
       (CAST(our_reg AS double) / NULLIF(total_reg, 0)
        - CAST(our_reg_py AS double) / NULLIF(total_reg_py, 0)) * total_reg AS share_effect,
       our_reg - our_reg_py AS reg_change
FROM p
```

The first 12 months have no prior year (`our_reg_py` null); visuals that use the effects
show the last 12 months only.

### D4 `retail-ops-oem-competitor-offers` — Retail Ops · OEM competitor offers

One row per district × segment × month × competitor (US 360, DE 216).

```sql
SELECT m.market, m.district_id, m.segment, m.month,
       CAST(date_parse(m.month || '-01', '%Y-%m-%d') AS date) AS month_start,
       o.offer.competitor AS competitor, o.offer.apr_pct AS apr_pct,
       o.offer.lease_monthly AS lease_monthly, o.offer.cash_incentive AS cash_incentive,
       CAST(m.our_share_pct AS double) AS our_share_pct
FROM {DATABASE}.lake_marketintel m
CROSS JOIN UNNEST(CAST(json_parse(m.competitor_offers)
     AS array(row(apr_pct double, cash_incentive double, competitor varchar, lease_monthly double))))
     AS o(offer)
```

### D5 `retail-ops-oem-campaign-lift` — Retail Ops · OEM campaign lift

Per campaign: retail units of the promoted model in the promoted district, in the
campaign's complete months vs the 3 months before it starts, and how many other
campaigns promoted the same model at the same time.

```sql
WITH c AS (
  SELECT market, campaign_id, name, objective, status, model_id, district_id,
         CAST(budget AS double) AS budget,
         from_iso8601_timestamp(start_at) AS start_ts, from_iso8601_timestamp(end_at) AS end_ts,
         date_trunc('month', CAST(from_iso8601_timestamp(start_at) AS date)) AS start_month,
         date_trunc('month', CAST(from_iso8601_timestamp(end_at) AS date)) AS end_month
  FROM {DATABASE}.lake_campaigns),
ov AS (
  SELECT a.market, a.campaign_id, count(b.campaign_id) AS overlapping_campaigns
  FROM c a LEFT JOIN c b
    ON b.market = a.market AND b.model_id = a.model_id AND b.campaign_id <> a.campaign_id
   AND b.start_ts <= a.end_ts AND b.end_ts >= a.start_ts
  GROUP BY 1, 2),
r AS (
  SELECT market, model_id, district_id, CAST(date_parse(month || '-01', '%Y-%m-%d') AS date) AS m,
         sum(CAST(units AS integer)) AS units
  FROM {DATABASE}.lake_rdr GROUP BY 1, 2, 3, 4)
SELECT c.market, c.campaign_id, c.name, c.objective, c.status, c.model_id, c.district_id, c.budget,
       c.start_month, c.end_month, ov.overlapping_campaigns,
       sum(CASE WHEN r.m BETWEEN c.start_month AND c.end_month AND r.m < date_trunc('month', current_date)
                THEN r.units END) AS units_during,
       count(DISTINCT CASE WHEN r.m BETWEEN c.start_month AND c.end_month
                            AND r.m < date_trunc('month', current_date) THEN r.m END) AS months_during,
       sum(CASE WHEN r.m >= date_add('month', -3, c.start_month) AND r.m < c.start_month
                THEN r.units END) AS units_before_3m
FROM c
JOIN ov ON ov.market = c.market AND ov.campaign_id = c.campaign_id
JOIN r ON r.market = c.market AND r.model_id = c.model_id AND r.district_id = c.district_id
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
```

Model names for D5 come from a join to D2 on `market, model_id` in the analysis, or
from `model_id` alone.

## 5. Calculated fields

| Name | Dataset | Expression (Quick) |
|---|---|---|
| `Attainment %` | D1, D2 | `sum({units}) / sum({target_units})` |
| `vs PY %` | D1, D2 | `sum({units}) / sum({units_py}) - 1` |
| `Gross / unit` | D1, D2 | `sum({gross_total}) / sum({units})` (unit-weighted) |
| `PVR` | D1, D2 | `sum({pvr_total}) / sum({units})` |
| `Back-end / unit` | D1, D2 | `PVR - Gross / unit` (F&I and other per-vehicle profit beyond front-end gross) |
| `Momentum %` | D1 | `sum({units_r3}) / sum({units_prior_r3}) - 1` |
| `vs PY r3 %` | D1 | `sum({units_r3}) / sum({units_py_r3}) - 1` |
| `CSI gap` | D1 | `avg({csi_sales}) - avg({csi_region_avg})` |
| `CSI sales–service gap` | D1 | `avg({csi_sales}) - avg({csi_service})` |
| `Share %` | D3 | `sum({our_reg}) / sum({total_reg})` |
| `Share change pts` | D3 | `(sum({our_reg}) / sum({total_reg}) - sum({our_reg_py}) / sum({total_reg_py})) * 100` |
| `Best competitor APR` | D4 | `min({apr_pct})` |
| `Lowest competitor lease` | D4 | `min({lease_monthly})` |
| `Highest competitor cash` | D4 | `max({cash_incentive})` |
| `Run rate during` | D5 | `{units_during} / {months_during}` |
| `Run rate before` | D5 | `{units_before_3m} / 3` |
| `Lift %` | D5 | `Run rate during / Run rate before - 1` |
| `Budget per incremental unit` | D5 | `ifelse(Run rate during > Run rate before, {budget} / ((Run rate during - Run rate before) * {months_during}), NULL)` |
| `Currency` | all | `ifelse({market} = 'de', 'EUR', 'USD')` |
| `Watch flag` | D1 | row-level: `ifelse({vs_py_r3} < ${pVolumeAlert} AND {csi_below_region_r3} >= ${pCsiMonths}, 'Volume + CSI', {vs_py_r3} < ${pVolumeAlert}, 'Volume', {csi_below_region_r3} >= ${pCsiMonths}, 'CSI', 'OK')` |

## 6. Parameters

| Parameter | Type | Default | Used by |
|---|---|---|---|
| `pMonth` | Date | last complete month | KPI tiles and rankings (filter `month_start = pMonth`) |
| `pVolumeAlert` | Decimal | -0.05 (5% below prior year, rolling 3 months) | Watch flag, colours |
| `pCsiMonths` | Integer | 3 (months below region average, of the last 3) | Watch flag |
| `pAttainTarget` | Decimal | 1.00 | Attainment colours |

## 7. Sheets

### Sheet 1 — Network Pulse

*How are we doing, and which way is it going?*

| ID | Visual | Type | Fields | Notes |
|---|---|---|---|---|
| DSH-1.1 | Retail units | KPI | `sum(units)` at pMonth; comparison vs `sum(units_py)` | D1 |
| DSH-1.2 | Attainment % | KPI | `Attainment %` at pMonth | red below pAttainTarget |
| DSH-1.3 | vs PY (rolling 3 months) | KPI | `vs PY r3 %` at pMonth | |
| DSH-1.4 | Gross / unit · PVR | KPI ×2 | at pMonth, Currency label | |
| DSH-1.5 | Dealers on watch | KPI | count distinct dealer where Watch flag ≠ OK at pMonth | click → sheet 2 |
| DSH-1.6 | Units vs target vs prior year | Line | x month_start; y units, target_units, units_py | complete months only |
| DSH-1.7 | Momentum by dealer | Horizontal bar | y dealer_id; x `Momentum %`; sorted ascending | the slowest movers on top |
| DSH-1.8 | Monthly target hits | Heat map | rows dealer_id; columns month (last 12); value units ≥ target_units | 12 months at a glance |

Acceptance: at pMonth = 2026-10 and market `us`, DSH-1.1 equals
`SELECT sum(CAST(units AS integer)) FROM lake_rdr WHERE market='us' AND month='2026-10'`,
and DSH-1.5 shows 4 (us) / 6 (de) with the default parameters (see §10, question 1).

### Sheet 2 — Dealer Performance Matrix

*Is a dealer's problem volume, customer experience, or both? Where should the DM go?*

| ID | Visual | Type | Fields | Notes |
|---|---|---|---|---|
| DSH-2.1 | **Growth × Experience** | Scatter | x `vs PY r3 %`; y `CSI gap` (3-month avg); size units_r3; colour Watch flag; label dealer_id | reference lines at x = 0 and y = 0. Bottom-left = losing volume and below region on CSI: visit first |
| DSH-2.2 | Dealer scorecard | Table | dealer_id, units, Attainment %, vs PY r3 %, Momentum %, Gross / unit, Back-end / unit, CSI sales, CSI service, CSI gap, target_hits_r12, Watch flag | conditional colour by parameter; default sort `vs PY r3 %` ascending |
| DSH-2.3 | Dealer trend | Line (small multiples by dealer) | x month_start; y `vs PY %` and `CSI gap` | last 12 complete months |
| DSH-2.4 | Gap to district | Bar | dealer_id × (`Attainment %`, `Gross / unit`, `CSI sales`) minus district average | diverging bars |

Acceptance (both markets, pMonth = 2026-10): in DSH-2.1 the dealer with the highest
`vs PY r3 %` is D-104 (us) / D-204 (de) and the lowest is D-103 / D-203; D-101 /
D-201 have the lowest `CSI gap` and sit bottom-left with D-103 / D-203. (Matches the
storyline in `OEM_Accounts.condition`: top_performer, volume_down_conquest_pressure,
csi_decline_lead_sla; checked in Athena 2026-11-10.)

### Sheet 3 — Model Mix and Profitability

*Which models sell, and where does the profit per vehicle come from?*

| ID | Visual | Type | Fields | Notes |
|---|---|---|---|---|
| DSH-3.1 | Units by model | Bar | model_name; units, units_py | at pMonth, or rolling 3 months by toggle |
| DSH-3.2 | **Volume × margin** | Scatter | x units (trailing 12 months); y `Gross / unit`; size `PVR`; label model_name | top-right = volume and margin |
| DSH-3.3 | Profit stack per vehicle | Stacked bar | model_name; `Gross / unit` + `Back-end / unit` | shows models that rely on back-end profit |
| DSH-3.4 | Mix shift | 100% stacked area | x month_start; share of units by model_name | 24 complete months |
| DSH-3.5 | Model × dealer attainment | Heat map | rows dealer_id; columns model_name; `Attainment %` at pMonth | gaps a DM can raise per model |

Acceptance: for any model, `Gross / unit` + `Back-end / unit` in DSH-3.3 equals `PVR`.

### Sheet 4 — Customer Experience (CSI)

*Who is losing customers' goodwill, and is it the sales floor or the service lane?*

| ID | Visual | Type | Fields | Notes |
|---|---|---|---|---|
| DSH-4.1 | CSI vs region | Line | x month_start; y avg csi_sales, avg csi_service, avg csi_region_avg; dealer filter | |
| DSH-4.2 | Below-region streak | Table | dealer_id, csi_below_region_r3, CSI gap, months below region in last 12 | sorted by streak |
| DSH-4.3 | Sales vs service CSI | Scatter | x csi_sales; y csi_service at pMonth; diagonal reference; label dealer_id | off-diagonal = one department lags |
| DSH-4.4 | CSI change over 24 months | Bar | dealer_id; csi_sales at pMonth minus csi_sales 24 months earlier | finds slow declines a monthly view hides |
| DSH-4.5 | Does CSI follow volume? | Scatter | x `vs PY r3 %`; y `CSI gap`, one point per dealer per month (last 12) | |

Acceptance: D-101 / D-201 show `csi_below_region_r3 = 3` at pMonth = 2026-10.

### Sheet 5 — Market Share and Competitive Pressure

*When we lose registrations, is it the market or us, and how hard are competitors
pushing?*

| ID | Visual | Type | Fields | Notes |
|---|---|---|---|---|
| DSH-5.1 | Share trend by segment | Line + bar | x month_start; line `Share %` by segment; bar total_reg | D3 |
| DSH-5.2 | **Market or us?** | Waterfall | per segment, last complete month: our_reg_py → market_effect → share_effect → our_reg | D3; the two effects add up to `reg_change` |
| DSH-5.3 | Share change map | Heat map | rows segment; columns month (last 12); `Share change pts` | |
| DSH-5.4 | Competitor offer tracker | Line | x month_start; y apr_pct / lease_monthly / cash_incentive (selector); colour competitor | D4 |
| DSH-5.5 | Offer pressure vs our share | Scatter | x `Highest competitor cash` per segment-month; y our_share_pct | does more competitor cash coincide with lower share? |
| DSH-5.6 | Offer table | Table | segment, competitor, apr_pct, lease_monthly, cash_incentive at pMonth | sorted by cash_incentive descending |

Acceptance: for every segment and month in DSH-5.2, `market_effect + share_effect`
equals `reg_change` within 0.01 (checked in Athena: −8.21 + 6.21 = −2 for US Midsize
Cars, 2026-11).

### Sheet 6 — Campaign Effectiveness

*Did the campaign sell more of the model it promoted?*

| ID | Visual | Type | Fields | Notes |
|---|---|---|---|---|
| DSH-6.1 | Campaign table | Table | campaign_id, name, objective, status, model_id, budget, start_month, end_month, months_during, Run rate before, Run rate during, Lift %, overlapping_campaigns | D5 |
| DSH-6.2 | Lift by campaign | Bar | campaign_id; `Lift %`; colour objective | campaigns with `overlapping_campaigns > 0` hatched or labelled "shared with N" |
| DSH-6.3 | Budget per incremental unit | Bar | campaign_id; `Budget per incremental unit` | blank when there is no lift |
| DSH-6.4 | Model sales around the campaign | Line | x month_start (D2); y units of the campaign's model; reference band from start_month to end_month | |
| DSH-6.5 | Journey view of the same campaign | Table | from `retail-ops-campaign-roi-sql`: campaign, journeys, sold, spend_allocated | the funnel-attributed view next to the RDR lift |

Rules: a campaign with `months_during = 0` shows "not started" instead of a lift; a
campaign with `months_during < 2` is labelled "early read". RDR has no campaign
attribution, so lift is a before/after estimate on model sales, not causal; overlapping
campaigns on one model share the same lift.

Acceptance: on 2026-11-10 the DE campaigns show `months_during = 1` and the "early
read" label.

## 8. Natural-language questions (topic)

Topic **Retail Ops Sales Performance** (`retail-ops-sales-performance`) over D1, D3 and
D4, with custom instructions that define the fields and synonyms (units: sales, retail,
RDR; csi_sales: CSI, satisfaction; share: market share, penetration). A Topic only
takes datasets of the new data preparation experience loaded into SPICE (the Funnel fix
of 2026-11-10), so the topic gets SPICE copies of D1, D3 and D4, the same way
`retail-ops-journey-events-topic` copies the journey SQL.

Acceptance: "Which dealers were below the region average on CSI for the last 3 months
in the US?" returns D-101 and the other dealers with `csi_below_region_r3 = 3` in
DSH-4.2.

## 9. Names, ids and build

| Resource | Name | Id |
|---|---|---|
| Dashboard / analysis | Retail Ops OEM Sales Performance | `retail-ops-oem-sales-performance` |
| Datasets | Retail Ops · OEM dealer month / model month / market share / competitor offers / campaign lift | `retail-ops-oem-dealer-month`, `-oem-model-month`, `-oem-share`, `-oem-competitor-offers`, `-oem-campaign-lift` |
| Topic | Retail Ops Sales Performance | `retail-ops-sales-performance` |

Built by `deploy_quick_content.py` (new steps next to `dashboard`), shared per DSH-G6,
added to `retail-ops-space`. Sheet order as §7.

## 10. Limits and open questions

Limits of the data in the space (by design of this scope, not to be fixed here):

- No dealer names, tiers or powertrain: dealers show as ids, EV vs ICE cannot be
  split, peer groups are the district.
- No model → segment mapping: share (sheet 5) and model sales (sheet 3) are not joined.
- `target_units` is generated close to actual units, so `Attainment %` sits between
  about 87% and 100% for every dealer; the storyline shows in `vs PY` and `CSI gap`,
  which is why the matrix and the watch flag use those.
- Campaigns are only 3 months old (US) and 1 month old (DE) and overlap on the same
  models.

Open questions:

1. Parameter defaults (§6): with −5% vs prior year **or** 3 months below region, the
   watch list at 2026-10 is 4 of 11 US dealers (D-101, D-103, D-105, D-110) and 6 of 11
   DE dealers (D-201, D-202, D-203, D-208, D-210, D-211). Because `region_avg` is the
   network mean, about half the dealers are always below it; a tighter CSI rule (for
   example 3 points or more below region) would keep the list to the storyline dealers.
   Which rule does Sales Ops want?
2. Is a dealer-name lookup worth adding to the space later (one small dataset), or are
   ids fine for the OEM users?
3. Should the topic (§8) be part of the first build or follow it?
