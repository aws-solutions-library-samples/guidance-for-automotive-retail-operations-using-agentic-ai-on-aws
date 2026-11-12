#!/usr/bin/env python3
"""Re-prefix the temporary SSO_ tables by data owner, and neutralize infra tokens.

Reads the verified owner map (embedded below) and rewrites:
  SSO_<Name>  -> <OWNER>_<Name>   per the map (OEM/DEALER/CUST/PLAT)
  sso-<name>  -> app-<name>       (infra: buckets, buses, schedulers, doc-ids)
  sso_<field> -> app_<field>      (e.g. sso_id -> app_id)
  sso/<path>  -> app/<path>
  bare "sso"  -> "app"

Skips .git, .kiro, aidlc, session-backup, node_modules, __pycache__,
this script, rename_product.py, retire_irpo_namespace.py, and TOOLS.md
(generated — regenerate via extract_tools.py).

Usage: python3 scripts/apply_owner_prefixes.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTS = {".md", ".py", ".json", ".sh", ".txt", ".ts", ".tsx", ".yaml", ".yml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".kiro", "aidlc", "session-backup"}
SKIP_FILES = {
    "scripts/apply_owner_prefixes.py",
    "scripts/retire_irpo_namespace.py",
    "scripts/rename_product.py",
    "automotive-agent-skills/TOOLS.md",
}

OWNER_MAP = {
    "OEM": ["Accounts","AccountPlans","Alerts","CIPrograms","Campaigns","CatalogModels","Catalog","Comms","Compliance","ContactReports","GuardrailEvents","Incentives","Markets","StateRules"],
    "DEALER": ["Appointments","Approvals","Coverage","Customers","DTCReference","Deals","DeclinedWork","Deliveries","FIProducts","FinanceOffers","Handoffs","Inspections","InspectionTemplates","Inventory","JourneyEvents","Journeys","LaborOps","LeadSLA","Leads","Notifications","NurturePlans","ObjectionStrategies","Outcomes","Parts","PartsOrders","PlayRuns","Plays","Recommendations","RepairOrders","SalesPackages","ServiceEstimates","ServiceJourneyEvents","ServiceJourneys","Signals","Tasks","Touchpoints","TradeDispositions","Valuations","Vehicles"],
    "CUST": ["Consent","Messages","Documents"],
    "PLAT": ["AgentTraces","Config","ConfigAutonomy","ConfigGuardrails","ConfigNotifications","ConfigNurture","ConfigSuggestions","Configurations","EntityMap","KnowledgeCollections","OrgHierarchy","Users"],
}

# name -> owner, sorted longest-name-first so SSO_Config doesn't eat SSO_ConfigAutonomy.
NAME_TO_OWNER = {}
for owner, names in OWNER_MAP.items():
    for n in names:
        NAME_TO_OWNER[n] = owner
NAMES_LONGEST_FIRST = sorted(NAME_TO_OWNER, key=len, reverse=True)

TABLE_SUBS = [
    (re.compile(r"SSO_" + re.escape(n) + r"\b"), f"{NAME_TO_OWNER[n]}_{n}")
    for n in NAMES_LONGEST_FIRST
]
INFRA_SUBS = [
    (re.compile(r"sso-"), "app-"),
    (re.compile(r"sso_"), "app_"),
    (re.compile(r"sso/"), "app/"),
    (re.compile(r"\bsso\b"), "app"),
]


def iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in EXTS:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES or (set(rel.split("/")) & SKIP_DIRS):
            continue
        yield p


def transform(text: str) -> tuple[str, int]:
    n = 0
    for pat, repl in TABLE_SUBS:
        text, k = pat.subn(repl, text); n += k
    # Guard: any leftover SSO_<Word> means an unmapped table — fail loudly.
    # Exclude the RETAIL_OPS_Executive_Overview.pptx deck filename, whose substring
    # "SSO_Executive" is not a table (file names may keep the Retail Ops shorthand).
    scan = text.replace("RETAIL_OPS_Executive_Overview", "")
    leftover = re.findall(r"SSO_[A-Za-z0-9]+", scan)
    if leftover:
        raise SystemExit(f"ERROR unmapped SSO_ tables: {sorted(set(leftover))}")
    for pat, repl in INFRA_SUBS:
        text, k = pat.subn(repl, text); n += k
    return text, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    total = 0
    for p in iter_files():
        old = p.read_text(encoding="utf-8", errors="surrogateescape")
        new, n = transform(old)
        if n:
            total += n
            print(f"{n:4d}  {p.relative_to(ROOT)}")
            if not args.dry_run:
                p.write_text(new, encoding="utf-8", errors="surrogateescape")
    print(f"\n{total} replacements{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
