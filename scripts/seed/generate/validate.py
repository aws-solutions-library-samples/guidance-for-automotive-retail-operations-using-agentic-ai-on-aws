"""validate.py — the blocking acceptance gate over an exported market set (U1 subset).

Runs after export. PASS/FAIL over the local artifacts is the blocking acceptance
gate (FR5.1). The U1 skeleton checks the following invariants; U8 grows this to
the full FR5.2 set (source_ref completeness, volume/mix, storyline presence,
Process-2/agentic):

  BR1.1  manifest present + every artifact exists with a matching sha256
  BR1.2  every table key carries the correct owner prefix per OWNER_MAP
  BR1.3  no retired identifier (IRPO_ / ISSO_ / SSO_) anywhere in the artifacts
  BR1.4  no real-PII: every email domain is on the synthetic sending-domain
         allowlist DERIVED from the generator's own market pools + OEM pattern
  BR1.5  no credential-shaped string, except allowlisted synthetic demo values
  BR1.6  each exported table for the market is non-empty
  BR1.7  referential integrity: User.scope_node_id -> OrgNode, OrgNode tree

The full FR5.2 invariant set + FR6 coverage report (U8):

  BR2.1  source_ref completeness on every monetary/quantitative/cited table
  BR2.2  cross-unit referential integrity (Deals->Journeys, service events/ROs
         -> ServiceJourneys)
  BR2.3  guardrail-event rule_ids are all in GR-001..GR-008
  BR2.4  recommendation funnel monotonicity (shown >= accepted >= converted >= 0)
  BR2.5  no PLAT_AgentTraces timestamp beyond SEED_NOW (R-08)
  BR2.6  storyline presence: every golden purchase (J1-J9) + service (S1-S8)
         storyline is materialized, and the District storyline exists
  FR6    a per-market coverage-report.json is written next to the manifest

Exposes `validate_market(market_dir) -> Report` and a `__main__` that exits 0 on
PASS, non-zero on FAIL (BR4.1).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Support running as a module (python -m scripts.seed.generate.validate) or as a
# bare script (python scripts/seed/generate/validate.py).
if __package__ in (None, ""):  # pragma: no cover - script-mode import shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scripts.seed.generate.config import OUT_ROOT
    from scripts.seed.generate.owner_map import owner_matches, split_prefixed
else:
    from .config import OUT_ROOT
    from .owner_map import owner_matches, split_prefixed

# ── BR1.3 retired-identifier tokens ──────────────────────────────────────────
RETIRED_TOKENS = ("IRPO_", "ISSO_", "SSO_")

# ── FR5.2 / FR6 constants (U8) ───────────────────────────────────────────────
# Tables whose every row MUST carry a source_ref (monetary / quantitative / cited
# content). Event/log tables are deliberately excluded — they are not cited.
SOURCE_REF_REQUIRED = (
    "OEM_Campaigns", "OEM_Incentives", "OEM_AccountPlans", "OEM_CIPrograms",
    "DEALER_Deals", "DEALER_FinanceOffers", "DEALER_ServiceEstimates",
    "DEALER_Valuations", "DEALER_Recommendations",
    "PLAT_KnowledgeCollections",
)
# The eight golden purchase journeys and eight service stories that MUST be present
# (storyline presence, FR6). J-ids come from storylines.JOURNEYS at runtime.
SERVICE_GOLDEN_IDS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
GUARDRAIL_IDS = tuple(f"GR-{n:03d}" for n in range(1, 9))  # GR-001..GR-008

# ── BR1.4 synthetic sending-domain allowlist ─────────────────────────────────
# DERIVED from the generator's own pools (market.py email_domain_pool for BOTH
# markets) + the core.py OEM-user pattern, so the rule and the generator cannot
# drift. Loaded from market.py at runtime; a static fallback keeps validate.py
# usable if market.py cannot be imported.
def _allowed_email_domains() -> set[str]:
    domains: set[str] = set()
    try:
        if __package__ in (None, ""):  # script mode
            from scripts.seed.generate import market as _market
        else:
            from . import market as _market  # lazy: keep validate importable standalone

        for market_id in ("us", "de"):
            m = _market.build(market_id)
            domains.update(d.lower() for d in m.email_domain_pool)
            # OEM-user pattern in core.py: "<brand_short>-oem.example.com"
            domains.add(f"{m.brand_short.lower()}-oem.example.com")
    except Exception:
        # Static fallback = the union as emitted today (keeps validate self-contained).
        domains.update(
            {
                "example.com",
                "example.net",
                "mail.example.org",
                "example.de",
                "beispiel.example",
                "meridian-oem.example.com",
                "nordwerk-oem.example.com",
            }
        )
    return domains


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# ── BR1.5 credential-shaped patterns ─────────────────────────────────────────
# Deliberately narrow for the skeleton (U8 hardens). The known synthetic demo
# password is allowlisted so the skeleton passes.
SYNTHETIC_CREDENTIAL_ALLOWLIST = {"IrpoDemo2026!"}
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_AWS_SECRET_RE = re.compile(r"\baws_secret_access_key\b", re.IGNORECASE)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


@dataclass
class Report:
    market: str
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    checks_run: int = 0
    coverage: dict = field(default_factory=dict)  # FR6 coverage summary (U8)

    def fail(self, invariant: str, detail: str) -> None:
        self.passed = False
        self.failures.append(f"{invariant}: {detail}")

    def ran(self) -> None:
        self.checks_run += 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_strings(obj: object):
    """Yield every string value in a nested JSON structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def validate_market(market_dir: Path) -> Report:
    market = market_dir.name
    rep = Report(market=market)

    # ── BR1.1 manifest present + artifacts exist with matching sha256 ────────
    rep.ran()
    manifest_path = market_dir / "manifest.json"
    if not manifest_path.is_file():
        rep.fail("BR1.1", f"manifest.json missing at {manifest_path}")
        return rep  # nothing else is checkable without a manifest
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        rep.fail("BR1.1", f"manifest.json is not valid JSON: {e}")
        return rep
    artifacts = manifest.get("artifacts", [])
    if not artifacts:
        rep.fail("BR1.1", "manifest lists no artifacts")

    loaded: dict[str, list] = {}  # name -> rows, for later invariants
    for art in artifacts:
        apath = market_dir / art["path"]
        if not apath.is_file():
            rep.fail("BR1.1", f"artifact missing: {art['path']}")
            continue
        raw = apath.read_bytes()
        digest = _sha256(raw)
        if digest != art.get("sha256"):
            rep.fail(
                "BR1.1",
                f"sha256 mismatch for {art['path']}: manifest {art.get('sha256')} != file {digest}",
            )
        if art.get("kind") == "table":
            try:
                loaded[art["name"]] = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as e:
                rep.fail("BR1.1", f"artifact {art['path']} is not valid JSON: {e}")

    # ── BR1.2 owner-prefix correctness ───────────────────────────────────────
    rep.ran()
    for art in artifacts:
        if art.get("kind") != "table":
            continue
        if not owner_matches(art["name"]):
            rep.fail("BR1.2", f"table {art['name']!r} prefix does not match OWNER_MAP")

    # ── BR1.6 non-empty tables ────────────────────────────────────────────────
    rep.ran()
    for art in artifacts:
        if art.get("kind") == "table" and art.get("rows", 0) == 0:
            rep.fail("BR1.6", f"table {art['name']!r} is empty")

    # Load every artifact's text once for content scans (BR1.3/1.4/1.5).
    all_rows = [row for rows in loaded.values() for row in (rows if isinstance(rows, list) else [])]

    # ── BR1.3 no retired identifiers ──────────────────────────────────────────
    rep.ran()
    for art in artifacts:
        apath = market_dir / art["path"]
        if not apath.is_file():
            continue
        text = apath.read_text(encoding="utf-8", errors="replace")
        for token in RETIRED_TOKENS:
            if token in text:
                rep.fail("BR1.3", f"retired identifier {token!r} found in {art['path']}")
                break

    # ── BR1.4 no real-PII (email domain allowlist) ────────────────────────────
    rep.ran()
    allowed = _allowed_email_domains()
    for row in all_rows:
        for s in _iter_strings(row):
            for m in _EMAIL_RE.finditer(s):
                domain = m.group(1).lower()
                if domain not in allowed:
                    rep.fail("BR1.4", f"email domain {domain!r} not on the synthetic allowlist")

    # ── BR1.5 no credential-shaped strings (allowlist synthetic demo) ─────────
    rep.ran()
    for row in all_rows:
        for s in _iter_strings(row):
            if s in SYNTHETIC_CREDENTIAL_ALLOWLIST:
                continue
            if _AWS_ACCESS_KEY_RE.search(s) or _AWS_SECRET_RE.search(s) or _PRIVATE_KEY_RE.search(s):
                rep.fail("BR1.5", f"credential-shaped string found: {s[:24]!r}...")

    # ── BR1.7 referential integrity (User -> OrgNode, OrgNode tree) ───────────
    rep.ran()
    org_key = next((n for n in loaded if split_prefixed(n)[1] == "OrgHierarchy"), None)
    users_key = next((n for n in loaded if split_prefixed(n)[1] == "Users"), None)
    if org_key:
        org_ids = {n.get("node_id") for n in loaded[org_key]}
        for node in loaded[org_key]:
            parent = node.get("parent_id")
            if parent is not None and parent not in org_ids:
                rep.fail("BR1.7", f"OrgNode {node.get('node_id')!r} parent {parent!r} not found")
        if users_key:
            for u in loaded[users_key]:
                scope = u.get("scope_node_id")
                if scope is not None and scope not in org_ids:
                    rep.fail(
                        "BR1.7",
                        f"User {u.get('user_id')!r} scope_node_id {scope!r} has no OrgNode",
                    )

    # Bare-name -> rows lookup (owner prefix stripped) for the FR5.2/FR6 checks.
    by_bare = {split_prefixed(n)[1]: rows for n, rows in loaded.items()}

    # ── BR2.1 source_ref completeness (FR5.2) ────────────────────────────────
    # Every row of a monetary/quantitative/cited table carries a non-empty
    # source_ref so downstream (incl. Quick) citations resolve.
    rep.ran()
    for full_name, rows in loaded.items():
        if full_name not in SOURCE_REF_REQUIRED:
            continue
        for row in rows:
            if not row.get("source_ref"):
                rep.fail("BR2.1", f"{full_name} row missing source_ref: {sorted(row)[:4]}")
                break

    # ── BR2.2 referential integrity across units (FR5.2) ─────────────────────
    rep.ran()
    journey_ids = {j["journey_id"] for j in by_bare.get("Journeys", [])}
    sj_ids = {s["service_journey_id"] for s in by_bare.get("ServiceJourneys", [])}
    for d in by_bare.get("Deals", []):
        if journey_ids and d.get("journey_id") not in journey_ids:
            rep.fail("BR2.2", f"Deal {d.get('deal_id')!r} journey_id not found")
            break
    for e in by_bare.get("ServiceJourneyEvents", []):
        if sj_ids and e.get("service_journey_id") not in sj_ids:
            rep.fail("BR2.2", f"ServiceJourneyEvent service_journey_id {e.get('service_journey_id')!r} not found")
            break
    for ro in by_bare.get("RepairOrders", []):
        if sj_ids and ro.get("service_journey_id") not in sj_ids:
            rep.fail("BR2.2", f"RepairOrder {ro.get('ro_id')!r} service_journey_id not found")
            break

    # ── BR2.3 guardrail-event rule_ids in GR-001..008 (FR5.2) ────────────────
    rep.ran()
    for gre in by_bare.get("GuardrailEvents", []):
        if gre.get("rule_id") not in GUARDRAIL_IDS:
            rep.fail("BR2.3", f"GuardrailEvent rule_id {gre.get('rule_id')!r} not in GR-001..008")
            break

    # ── BR2.4 recommendation funnel monotonicity (FR5.2, agentic) ────────────
    rep.ran()
    for r in by_bare.get("Recommendations", []):
        shown, acc, conv = r.get("shown", 0), r.get("accepted", 0), r.get("converted", 0)
        if not (shown >= acc >= conv >= 0):
            rep.fail("BR2.4", f"Recommendation {r.get('recommendation_id')!r} funnel not monotone: {shown}/{acc}/{conv}")
            break

    # ── BR2.5 no AgentTrace beyond SEED_NOW (R-08 as an invariant) ───────────
    rep.ran()
    seed_now = manifest.get("seed_now")
    if seed_now:
        for tr in by_bare.get("AgentTraces", []):
            if tr.get("ts") and tr["ts"] > seed_now:
                rep.fail("BR2.5", f"AgentTrace {tr.get('trace_id')!r} ts {tr['ts']} exceeds SEED_NOW {seed_now}")
                break

    # ── BR2.7 plausibility: a person reading one row must not spot a stub ────────────
    # Each check here is a gap a rehearsal reported (round 7): VINs that read "VIN-US-…", a decline
    # dated tomorrow, a $334 cabin filter, a back-order naming no part, six reserved cars for nobody,
    # a car promised for 4:30 AM, an appointment with no customer. The generator must keep them out.
    rep.ran()
    if seed_now and full_tables(by_bare):
        _plausibility(rep, by_bare, seed_now, market)
    # ── BR2.6 storyline presence (FR6) ───────────────────────────────────────
    # Only enforced on a FULL export. A partial slice (e.g. the U1 foundation-only
    # skeleton, which emits no Journeys/ServiceJourneys/OEM tables) is not expected
    # to carry the golden storylines, so the invariant is scoped to runs that
    # actually produced the purchase-journey table.
    rep.ran()
    try:
        if __package__ in (None, ""):  # script mode
            from scripts.seed.generate.storylines import JOURNEYS as _JOURNEYS
        else:
            from .storylines import JOURNEYS as _JOURNEYS
        purchase_ids = {j["id"] for j in _JOURNEYS}
    except Exception:
        purchase_ids = set()
    present_purchase: set = set()
    present_service: set = set()
    full_run = "Journeys" in by_bare  # the commerce/oem entity tables are present
    if full_run:
        present_purchase = {j.get("golden_story") for j in by_bare.get("Journeys", [])}
        missing_p = sorted(purchase_ids - present_purchase)
        if missing_p:
            rep.fail("BR2.6", f"missing golden purchase storylines: {missing_p}")
        if "ServiceJourneys" in by_bare:
            present_service = {s.get("golden_story") for s in by_bare.get("ServiceJourneys", [])}
            missing_s = sorted(set(SERVICE_GOLDEN_IDS) - present_service)
            if missing_s:
                rep.fail("BR2.6", f"missing golden service storylines: {missing_s}")
        # District storyline: at least one OEM account + alert for the district.
        if "Accounts" in by_bare and not (by_bare.get("Accounts") and by_bare.get("Alerts")):
            rep.fail("BR2.6", "district storyline incomplete (no OEM accounts or alerts)")

    # ── FR6 coverage report (written next to the manifest; never fails) ──────
    coverage = {
        "market": market,
        "table_count": sum(1 for a in artifacts if a.get("kind") == "table"),
        "row_count": sum(len(r) for r in loaded.values()),
        "storylines": {
            "purchase_present": len(purchase_ids & present_purchase),
            "purchase_total": len(purchase_ids),
            "service_present": len(set(SERVICE_GOLDEN_IDS) & present_service),
            "service_total": len(SERVICE_GOLDEN_IDS),
            "district_present": bool(by_bare.get("Accounts") and by_bare.get("Alerts")),
        },
        "invariant_groups_run": rep.checks_run,
        "passed": rep.passed,
        "failure_count": len(rep.failures),
    }
    try:
        (market_dir / "coverage-report.json").write_text(
            json.dumps(coverage, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # a read-only artifact dir must not turn a PASS into a crash
    rep.coverage = coverage

    return rep


def full_tables(by_bare: dict) -> bool:
    return "Journeys" in by_bare and "ServiceJourneys" in by_bare


_VIN_ALNUM = set("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")
_DECLINED_MAX = {"Cabin filter": 130, "Alignment": 260, "Coolant flush": 280, "Rear brakes": 700}
_STORE_TZ = {"us": "America/New_York", "de": "Europe/Berlin"}


def _plausibility(rep: Report, by_bare: dict, seed_now: str, market: str) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(_STORE_TZ.get(market, "UTC"))
    bad_vins = [v.get("vin") for v in by_bare.get("Vehicles", []) + by_bare.get("Inventory", [])
                if not (isinstance(v.get("vin"), str) and len(v["vin"]) == 17 and set(v["vin"]) <= _VIN_ALNUM)]
    if bad_vins:
        rep.fail("BR2.7", f"{len(bad_vins)} VIN(s) do not read as VINs (17 chars, no I/O/Q), e.g. {bad_vins[:3]}")
    future = [d.get("declined_id") for d in by_bare.get("DeclinedWork", []) if (d.get("declined_at") or "") > seed_now]
    if future:
        rep.fail("BR2.7", f"{len(future)} declined item(s) dated after the seed clock, e.g. {future[:3]}")
    pricey = [(d.get("item"), d.get("amount")) for d in by_bare.get("DeclinedWork", [])
              if d.get("item") in _DECLINED_MAX and (d.get("amount") or 0) > _DECLINED_MAX[d["item"]]]
    if pricey:
        rep.fail("BR2.7", f"{len(pricey)} declined item(s) priced beyond plausibility, e.g. {pricey[:3]}")
    nameless = [o.get("parts_order_id") for o in by_bare.get("PartsOrders", []) if not o.get("part_number")]
    if nameless:
        rep.fail("BR2.7", f"{len(nameless)} parts order(s) name no part, e.g. {nameless[:3]}")
    journeys = {j["journey_id"] for j in by_bare.get("Journeys", [])}
    orphan_holds = [u.get("vin") for u in by_bare.get("Inventory", [])
                    if u.get("status") == "reserved" and u.get("reserved_for_journey_id") not in journeys]
    if orphan_holds:
        rep.fail("BR2.7", f"{len(orphan_holds)} reserved unit(s) held for no journey, e.g. {orphan_holds[:3]}")
    off_hours = []
    for sj in by_bare.get("ServiceJourneys", []):
        if sj.get("stage") in ("COMPLETED", "FOLLOW_UP") or not sj.get("promised_at"):
            continue
        hour = datetime.fromisoformat(sj["promised_at"]).astimezone(tz).hour
        if not 7 <= hour <= 18:
            off_hours.append((sj.get("service_journey_id"), hour))
    if off_hours:
        rep.fail("BR2.7", f"{len(off_hours)} open visit(s) promised outside store hours, e.g. {off_hours[:3]}")
    customers = {c["customer_id"] for c in by_bare.get("Customers", [])}
    nobody = [a.get("appointment_id") for a in by_bare.get("Appointments", [])
              if a.get("kind") != "open_slot" and a.get("customer_id") not in customers]
    if nobody:
        rep.fail("BR2.7", f"{len(nobody)} appointment(s) with no customer, e.g. {nobody[:3]}")
    # a trim reads with its model ("Crosstrail SE"), never a bare grade ("SE")
    bare_grades = [t.get("trim_id") for t in by_bare.get("Catalog", []) if t.get("trim_id") and t.get("kind") != "package"
                   and len(str(t.get("trim_name") or "").split()) < 2]
    if bare_grades:
        rep.fail("BR2.7", f"{len(bare_grades)} trim(s) named by grade alone, e.g. {bare_grades[:3]}")


def validate_out_root(out_root: Path = OUT_ROOT, markets: list[str] | None = None) -> list[Report]:
    reports: list[Report] = []
    candidates = markets or [p.name for p in sorted(out_root.iterdir()) if p.is_dir()] if out_root.exists() else []
    for market in candidates:
        reports.append(validate_market(out_root / market))
    return reports


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate an exported synthetic-data market set.")
    ap.add_argument("--market", default=None, help="us | de (default: every market dir present)")
    ap.add_argument("--out-root", default=str(OUT_ROOT))
    args = ap.parse_args(argv)
    out_root = Path(args.out_root)
    markets = [args.market] if args.market else None
    reports = validate_out_root(out_root, markets)
    if not reports:
        print(f"validate: no market artifacts found under {out_root}")
        return 1
    ok = True
    for rep in reports:
        if rep.passed:
            print(f"validate[{rep.market}]: PASS ({rep.checks_run} invariant groups)")
        else:
            ok = False
            print(f"validate[{rep.market}]: FAIL ({len(rep.failures)} issue(s))")
            for f in rep.failures:
                print(f"  - {f}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
