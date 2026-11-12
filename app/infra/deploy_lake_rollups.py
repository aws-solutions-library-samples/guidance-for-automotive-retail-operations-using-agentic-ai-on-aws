#!/usr/bin/env python3
"""Generate the derived analytics lake roll-ups (spec §3.7) and publish to S3 +
Athena + AWS Quick: rdr/, csi/, marketintel/, leads/.

Unlike deploy_lake_analytics.py (which mirrors existing generator tables), these
four prefixes are DERIVED time-series facts with no backing table. They are
computed here, deterministically, anchored to the REAL dealers/accounts/catalog
already in DynamoDB so the roll-ups agree with the live data and the District-7
storyline (per the account `condition` field):

  rdr/          22 dealers x 24 months x 7 models — units, gross/unit, PVR,
                target, PY-units. Shaped by each dealer's condition + health.
  csi/          22 dealers x 24 months — CSI sales + service score + region avg;
                a csi_decline dealer trends DOWN over the window.
  marketintel/  per district x segment x 24 months — our registrations + share
                vs competitors, plus competitor advertised offers.
  leads/        mirror of DEALER_Leads (funnel-by-source), market-partitioned.

Determinism (spec §1.5 + project rule): all randomness seeds from
`Random(f"{SEED}:{market}:{...}")` so re-runs are byte-identical. Every generated
value carries a `source_ref` (project rule) so Quick citations resolve.

Reuses the publish plumbing: writes NDJSON to the lake bucket under <prefix>/
market=<m>/, registers one Glue external table per prefix, repairs partitions,
then (optionally) creates the Quick dataset via deploy_quick_datasets.

Usage:
  python3 -m app.infra.deploy_lake_rollups            # dry-run (counts only)
  python3 -m app.infra.deploy_lake_rollups --apply    # generate + publish + register
  python3 -m app.infra.deploy_lake_rollups --status
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
DATABASE = naming.name_us("lake")
WORKGROUP = naming.name("lake")
LAKE_CONFIG = naming.config_path("lake")
SEED = 42
MONTHS = 24
MARKETS = ("us", "de")
ENV_PREFIX = naming.PREFIX

PREFIXES = ("rdr", "csi", "marketintel", "leads", "campaigns")


def _bucket() -> str:
    if LAKE_CONFIG.exists():
        b = json.loads(LAKE_CONFIG.read_text()).get("bucket")
        if b:
            return b
    import boto3
    acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    return naming.name(f"event-lake-{acct}")


def _month_labels(n: int = MONTHS) -> list[str]:
    """Last n year-month labels ending this month, oldest first (YYYY-MM)."""
    import datetime as dt
    today = dt.date.today().replace(day=1)
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


# --- source anchors from DynamoDB -------------------------------------------

def _dealer_accounts() -> list[dict]:
    from ..services.common import ddb
    rows = [r for r in ddb.scan_all("OEM_Accounts", ENV_PREFIX)
            if r.get("account_type") == "dealer"]
    out = []
    for r in rows:
        out.append({
            "dealer_id": r.get("dealer_id"),
            "district_id": r.get("district_id"),
            "market_id": r.get("market_id"),
            "condition": r.get("condition") or "normal",
            "sales_health": float(r.get("sales_health_index") or 0.8),
            "service_health": float(r.get("service_health_index") or 0.8),
            "days_supply": int(float(r.get("days_supply") or 50)),
            "tier": r.get("tier") or "B",
            "name": r.get("name"),
        })
    return out


def _models_by_market() -> dict[str, list[dict]]:
    from ..services.common import ddb
    out: dict[str, list[dict]] = {"us": [], "de": []}
    for r in ddb.scan_all("OEM_CatalogModels", ENV_PREFIX):
        m = r.get("market_id")
        if m in out:
            out[m].append({"model_id": r.get("model_id"), "name": r.get("name"),
                           "segment": r.get("segment")})
    return out


def _leads_rows() -> list[dict]:
    """Mirror of DEALER_Leads (funnel-by-source), flattened for analytics."""
    from ..services.common import ddb
    out = []
    for r in ddb.scan_all("DEALER_Leads", ENV_PREFIX):
        out.append({
            "lead_id": r.get("lead_id"),
            "market_id": r.get("market_id"),
            "dealer_id": r.get("dealer_id"),
            "source": r.get("source"),
            "vehicle_of_interest": r.get("vehicle_of_interest") or r.get("voi"),
            "received_at": r.get("received_at"),
            "first_response_at": r.get("first_response_at"),
            "status": r.get("status"),
            "source_ref": r.get("source_ref") or "mirror:DEALER_Leads",
        })
    return out


# --- condition -> trend shape (the District-7 storyline) --------------------

def _trend_factor(condition: str, i: int, n: int) -> float:
    """A multiplicative shape across the 24-month window per storyline condition.
    i in [0, n-1], oldest..newest. Keys off the real OEM_Accounts.condition values."""
    t = i / max(n - 1, 1)   # 0..1
    if condition == "csi_decline_lead_sla":            # Decatur: declining
        return 1.08 - 0.22 * t
    if condition == "volume_down_conquest_pressure":    # Marietta: -22% volume
        return 1.05 - 0.27 * t
    if condition == "top_performer":                    # Athens: strong, rising
        return 0.95 + 0.18 * t
    if condition == "high_days_supply_ev_aging":        # Columbus: soft, flat
        return 0.90 + 0.04 * t
    if condition == "ev_cert_incomplete_noncompliant":  # Macon: slightly soft
        return 0.93 + 0.05 * t
    if condition == "compliance_conditional":           # Rome: flat
        return 0.95 + 0.05 * t
    if condition == "strong_weekend_weak_monday":       # Savannah: mild growth
        return 0.96 + 0.08 * t
    return 0.97 + 0.06 * t                               # normal: gentle growth


# --- generators -------------------------------------------------------------

def gen_rdr(dealers: list[dict], models_by_market: dict) -> list[dict]:
    rows = []
    labels = _month_labels()
    for d in dealers:
        rng = random.Random(f"{SEED}:{d['market_id']}:rdr:{d['dealer_id']}")
        models = models_by_market.get(d["market_id"], [])
        base = 6 + int(d["sales_health"] * 10)   # ~monthly units per model baseline
        for mi, month in enumerate(labels):
            shape = _trend_factor(d["condition"], mi, len(labels))
            for mdl in models:
                units = max(0, int(base * shape * rng.uniform(0.6, 1.4)))
                py = max(0, int(units / max(shape, 0.5) * rng.uniform(0.85, 1.1)))
                gross = round(rng.uniform(1800, 4200) * (0.9 + 0.2 * d["sales_health"]), 2)
                pvr = round(gross + rng.uniform(600, 1600), 2)
                target = max(1, int(units * rng.uniform(1.0, 1.15)))
                rows.append({
                    "market_id": d["market_id"], "dealer_id": d["dealer_id"],
                    "district_id": d["district_id"], "month": month,
                    "model_id": mdl["model_id"], "model_name": mdl["name"],
                    "units": units, "units_py": py, "target_units": target,
                    "gross_per_unit": gross, "pvr": pvr,
                    "attainment_pct": round(100.0 * units / max(target, 1), 1),
                    "source_ref": "synthetic:rdr; anchor:OEM_Accounts.sales_health_index + OEM_CatalogModels",
                })
    return rows


def gen_csi(dealers: list[dict]) -> list[dict]:
    rows = []
    labels = _month_labels()
    # region average per market per month (computed after per-dealer scores)
    by_market_month: dict[tuple, list[float]] = {}
    staged = []
    for d in dealers:
        rng = random.Random(f"{SEED}:{d['market_id']}:csi:{d['dealer_id']}")
        for mi, month in enumerate(labels):
            shape = _trend_factor(d["condition"], mi, len(labels))
            sales_csi = round(min(100.0, max(60.0, 100 * d["sales_health"] * shape * rng.uniform(0.97, 1.03))), 1)
            svc_csi = round(min(100.0, max(60.0, 100 * d["service_health"] * shape * rng.uniform(0.97, 1.03))), 1)
            staged.append((d, month, sales_csi, svc_csi))
            by_market_month.setdefault((d["market_id"], month), []).append((sales_csi + svc_csi) / 2)
    region_avg = {k: round(sum(v) / len(v), 1) for k, v in by_market_month.items()}
    for d, month, sales_csi, svc_csi in staged:
        rows.append({
            "market_id": d["market_id"], "dealer_id": d["dealer_id"],
            "district_id": d["district_id"], "month": month,
            "csi_sales": sales_csi, "csi_service": svc_csi,
            "region_avg": region_avg[(d["market_id"], month)],
            "source_ref": "synthetic:csi; anchor:OEM_Accounts.(sales|service)_health_index",
        })
    return rows


def gen_marketintel(dealers: list[dict], models_by_market: dict) -> list[dict]:
    rows = []
    labels = _month_labels()
    competitors = ["Competitor A", "Competitor B", "Competitor C"]
    # per district x segment x month: our registrations + share, + competitor offers
    districts: dict[str, dict] = {}
    for d in dealers:
        districts.setdefault(d["district_id"], d)   # a representative dealer per district
    for dist_id, d in districts.items():
        segments = sorted({m["segment"] for m in models_by_market.get(d["market_id"], []) if m.get("segment")})
        for seg in segments:
            rng = random.Random(f"{SEED}:{d['market_id']}:mi:{dist_id}:{seg}")
            for mi, month in enumerate(labels):
                shape = _trend_factor(d["condition"], mi, len(labels))
                our_reg = max(0, int(40 * shape * rng.uniform(0.7, 1.3)))
                total_reg = our_reg + sum(int(our_reg * rng.uniform(0.6, 1.8)) for _ in competitors)
                share = round(100.0 * our_reg / max(total_reg, 1), 1)
                offers = [{
                    "competitor": c,
                    "apr_pct": round(rng.uniform(0.9, 6.9), 1),
                    "lease_monthly": round(rng.uniform(249, 619), 0),
                    "cash_incentive": round(rng.uniform(500, 4000), 0),
                } for c in competitors]
                rows.append({
                    "market_id": d["market_id"], "district_id": dist_id, "segment": seg,
                    "month": month, "our_registrations": our_reg,
                    "total_segment_registrations": total_reg, "our_share_pct": share,
                    "competitor_offers": json.dumps(offers, sort_keys=True),
                    "source_ref": "synthetic:marketintel; anchor:OEM_CatalogModels.segment + OEM_Accounts.district",
                })
    return rows


def _campaigns_rows() -> list[dict]:
    """Mirror of OEM_Campaigns for the A5 ROI dashboard (joins to journey_events
    by dealer/source). Nested fields JSON-encoded for the flat lake table."""
    from ..services.common import ddb
    out = []
    for r in ddb.scan_all("OEM_Campaigns", ENV_PREFIX):
        out.append({
            "campaign_id": r.get("campaign_id"),
            "market_id": r.get("market_id"),
            "name": r.get("name"),
            "audience": r.get("audience"),
            "objective": r.get("objective"),
            "status": r.get("status"),
            "district_id": r.get("district_id"),
            "model_id": r.get("model_id"),
            "budget": r.get("budget"),
            "start_at": r.get("start_at"),
            "end_at": r.get("end_at"),
            "incentive_ids": json.dumps(r.get("incentive_ids") or [], sort_keys=True),
            "source_ref": r.get("source_ref") or "mirror:OEM_Campaigns",
        })
    return out


def generate() -> dict[str, list[dict]]:
    dealers = _dealer_accounts()
    models = _models_by_market()
    return {
        "rdr": gen_rdr(dealers, models),
        "csi": gen_csi(dealers),
        "marketintel": gen_marketintel(dealers, models),
        "leads": _leads_rows(),
        "campaigns": _campaigns_rows(),
    }


# --- publish (S3 + Glue + Athena) -------------------------------------------

def _ndjson(rows: list[dict]) -> bytes:
    return ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows)
            + ("\n" if rows else "")).encode("utf-8")


def _columns(rows: list[dict]) -> list[str]:
    return sorted({k for r in rows for k in r if k != "market_id"})


def publish(data: dict[str, list[dict]], apply: bool) -> dict[str, dict]:
    import boto3
    bucket = _bucket()
    s3 = boto3.client("s3", region_name=REGION) if apply else None
    summary: dict[str, dict] = {}
    for prefix, rows in data.items():
        if not rows:
            continue
        # partition rows by market
        by_market: dict[str, list[dict]] = {}
        for r in rows:
            by_market.setdefault(r.get("market_id") or "us", []).append(r)
        cols = _columns(rows)
        summary[prefix] = {"rows": len(rows), "markets": sorted(by_market), "columns": cols}
        print(f"  [{'s3' if apply else 'dry-run'}] {prefix}: {len(rows)} rows "
              f"across {sorted(by_market)} ({len(cols)} cols)")
        if apply:
            for market, mrows in by_market.items():
                s3.put_object(Bucket=bucket, Key=f"{prefix}/market={market}/data.ndjson",
                              Body=_ndjson(mrows), ContentType="application/x-ndjson")
    return summary


def register_and_repair(summary: dict[str, dict], apply: bool) -> None:
    import boto3
    import time
    bucket = _bucket()
    glue = boto3.client("glue", region_name=REGION)
    athena = boto3.client("athena", region_name=REGION)
    for prefix, info in summary.items():
        table = f"lake_{prefix}"
        cols = [{"Name": c, "Type": "string"} for c in info["columns"]]
        table_input = {
            "Name": table, "TableType": "EXTERNAL_TABLE",
            "Parameters": {"classification": "json", "typeOfData": "file"},
            "PartitionKeys": [{"Name": "market", "Type": "string"}],
            "StorageDescriptor": {
                "Columns": cols, "Location": f"s3://{bucket}/{prefix}/",
                "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                "SerdeInfo": {"SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
                              "Parameters": {"ignore.malformed.json": "true"}},
            },
        }
        if not apply:
            print(f"  [dry-run] would register {DATABASE}.{table} ({len(cols)} cols)")
            continue
        try:
            glue.create_table(DatabaseName=DATABASE, TableInput=table_input)
            print(f"  [glue] created table {table}")
        except glue.exceptions.AlreadyExistsException:
            glue.update_table(DatabaseName=DATABASE, TableInput=table_input)
            print(f"  [glue] updated table {table}")
        r = athena.start_query_execution(
            QueryString=f"MSCK REPAIR TABLE {DATABASE}.{table}",
            WorkGroup=WORKGROUP, QueryExecutionContext={"Database": DATABASE})
        qid = r["QueryExecutionId"]
        for _ in range(30):
            st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
            if st in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(1)
        print(f"  [athena] repaired {table} ({st})")


def status() -> None:
    import boto3
    import time
    bucket = _bucket()
    athena = boto3.client("athena", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    for prefix in PREFIXES:
        n = len([o for o in s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/").get("Contents", [])
                 if o["Key"].endswith(".ndjson")])
        table = f"lake_{prefix}"
        rows = "?"
        try:
            r = athena.start_query_execution(QueryString=f"SELECT count(*) FROM {DATABASE}.{table}",
                                             WorkGroup=WORKGROUP, QueryExecutionContext={"Database": DATABASE})
            qid = r["QueryExecutionId"]
            for _ in range(30):
                st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
                if st in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    break
                time.sleep(1)
            if st == "SUCCEEDED":
                rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"][1]["Data"][0].get("VarCharValue", "?")
        except Exception:
            pass
        print(f"  {prefix}: {n} ndjson object(s) · {DATABASE}.{table} = {rows} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate + publish derived analytics lake roll-ups")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(); return
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] generate derived roll-ups (rdr, csi, marketintel, leads)")
    data = generate()
    summary = publish(data, args.apply)
    register_and_repair(summary, args.apply)
    print(f"[{mode}] done. prefixes: {', '.join(summary)}")
    if args.apply:
        print("  next: python3 -m app.infra.deploy_quick_datasets --apply  (adds Quick datasets for the new tables)")


if __name__ == "__main__":
    main()
