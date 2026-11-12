"""Gateway OEM read tools return the fields the OEM rows actually carry.

OEM_Alerts rows hold `title`/`kind`, not `message`/`summary`; OEM_CIPrograms rows
hold `started_at`/`goal`/`status`, not `window`/`period`/`summary`. The tools map
the real columns onto their output keys (older names stay as fallbacks), keep
`source_ref` on every record and add the row's own citation as `data_source_ref`.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.infra import deploy_gateway
from app.services.gateway import handler, oem_read

OEM = {"user_id": "quick-service", "role": "oem_service", "is_oem": True, "dealer_ids": None}
DEALER = {"user_id": "U-US-00001", "role": "dealer_manager", "is_oem": False, "dealer_ids": "D-101"}
_SRC = oem_read._SRC


def _market_rows(fake, table, market):
    return [r for r in fake.rows(table) if r.get("market_id") == market]


def _alerts(claims, market="us"):
    return oem_read.call_tool("oem_get_alerts", claims, {"market_id": market})


def _programs(claims, market="us"):
    return oem_read.call_tool("oem_get_incentive_programs", claims, {"market_id": market})


# --- oem_get_alerts --------------------------------------------------------------

def test_alerts_fill_message_and_context_from_seeded_rows(fake_ddb):
    out = _alerts(OEM)
    assert out["count"] == len(_market_rows(fake_ddb, "OEM_Alerts", "us")) > 0
    for a in out["alerts"]:
        assert a["message"] and a["message"] == a["title"]
        assert a["severity"] in ("high", "medium", "low")
        assert a["category"] and a["status"] in ("open", "resolved")
        assert a["dealer_id"] and a["district_id"] and a["opened_at"]
        assert a["source_ref"] == _SRC and a["data_source_ref"].startswith("spec:")
        assert "required_action" not in a and "due_at" not in a     # the seeded rows carry neither
    resolved = [a for a in out["alerts"] if a["status"] == "resolved"]
    assert resolved and all(a["resolved_at"] for a in resolved)


def test_alerts_map_one_row_exactly(fake_ddb):
    fake_ddb.seed("OEM_Alerts", [{
        "alert_id": "AL-T1", "market_id": "us", "dealer_id": "D-101", "district_id": "DIST-07",
        "kind": "lead_sla_breach", "severity": "high", "status": "open", "title": "Lead SLA breaches trending up",
        "opened_at": "2026-09-02T09:00:00Z", "resolved_at": None, "source_ref": "test:alert"}])
    a = next(a for a in _alerts(OEM)["alerts"] if a["alert_id"] == "AL-T1")
    assert a == {"alert_id": "AL-T1", "severity": "high", "message": "Lead SLA breaches trending up",
                 "dealer_id": "D-101", "title": "Lead SLA breaches trending up", "category": "lead_sla_breach",
                 "status": "open", "district_id": "DIST-07", "opened_at": "2026-09-02T09:00:00Z",
                 "resolved_at": None, "data_source_ref": "test:alert", "source_ref": _SRC}


def test_alerts_keep_old_field_names_as_fallbacks(fake_ddb):
    fake_ddb.seed("OEM_Alerts", [
        {"alert_id": "AL-T2", "market_id": "us", "dealer_id": "D-101", "severity": "medium",
         "message": "Old-style message", "category": "csi", "created_at": "2026-09-01T00:00:00Z",
         "required_action": "Call the GM", "due_date": "2026-09-08"},
        {"alert_id": "AL-T3", "market_id": "us", "dealer_id": "D-101", "severity": "low", "summary": "Summary only"}])
    rows = {a["alert_id"]: a for a in _alerts(OEM)["alerts"]}
    old = rows["AL-T2"]
    assert old["message"] == old["title"] == "Old-style message"
    assert old["category"] == "csi" and old["opened_at"] == "2026-09-01T00:00:00Z"
    assert old["required_action"] == "Call the GM" and old["due_at"] == "2026-09-08"
    assert "data_source_ref" not in old and old["source_ref"] == _SRC      # no row citation to keep
    assert rows["AL-T3"]["message"] == rows["AL-T3"]["title"] == "Summary only"


def test_alerts_scope_unchanged_for_a_dealer_caller(fake_ddb):
    out = _alerts(DEALER)
    assert out["count"] > 0 and {a["dealer_id"] for a in out["alerts"]} == {"D-101"}
    assert all(a["message"] for a in out["alerts"])


# --- oem_get_incentive_programs -------------------------------------------------

def test_programs_fill_window_summary_and_dates_from_seeded_rows(fake_ddb):
    for market in ("us", "de"):
        out = _programs(OEM, market)
        assert out["count"] == len(_market_rows(fake_ddb, "OEM_CIPrograms", market)) > 0
        for p in out["programs"]:
            assert p["name"] and p["goal"] and p["status"] == "active" and p["district_id"]
            assert p["started_at"] and p["ended_at"] is None
            assert p["window"] == f"from {p['started_at'][:10]} (no end date)"
            assert p["summary"] == p["goal"]
            assert p["source_ref"] == _SRC and p["data_source_ref"].startswith("spec:")
            assert "owner_user_id" not in p and "condition" not in p


def test_programs_keep_old_field_names_and_build_a_closed_window(fake_ddb):
    fake_ddb.seed("OEM_CIPrograms", [
        {"program_id": "CIP-T1", "market_id": "us", "name": "Old", "window": "Q3 2026", "summary": "Old summary",
         "goal": "g"},
        {"program_id": "CIP-T2", "market_id": "us", "name": "Period", "period": "2026-H2", "description": "Desc"},
        {"program_id": "CIP-T3", "market_id": "us", "name": "Closed", "started_at": "2026-07-01T09:00:00Z",
         "ended_at": "2026-09-30T09:00:00Z", "goal": "Close the gap", "status": "ended"}])
    rows = {p["program_id"]: p for p in _programs(OEM)["programs"]}
    assert rows["CIP-T1"]["window"] == "Q3 2026" and rows["CIP-T1"]["summary"] == "Old summary"
    assert rows["CIP-T2"]["window"] == "2026-H2" and rows["CIP-T2"]["summary"] == "Desc"
    closed = rows["CIP-T3"]
    assert closed["window"] == "2026-07-01 to 2026-09-30" and closed["summary"] == "Close the gap"
    assert closed["ended_at"] == "2026-09-30T09:00:00Z" and closed["status"] == "ended"


def test_programs_name_another_dealer_only_to_an_oem_caller(fake_ddb):
    oem = {p["program_id"]: p for p in _programs(OEM)["programs"]}
    dealer = {p["program_id"]: p for p in _programs(DEALER)["programs"]}
    assert set(dealer) == set(oem)                                       # scope (market-wide) unchanged
    targeted = {pid for pid, p in oem.items() if p["dealer_id"]}
    assert targeted and {oem[pid]["dealer_id"] for pid in targeted} >= {"D-101"}
    for pid in targeted:
        expected = oem[pid]["dealer_id"] if oem[pid]["dealer_id"] == "D-101" else None
        assert dealer[pid]["dealer_id"] == expected
    assert all(dealer[pid]["window"] and dealer[pid]["summary"] for pid in dealer)


# --- oem_get_account / oem_list_accounts -------------------------------------------

def test_account_keeps_row_citation_and_strips_internal_fields(fake_ddb):
    fake_ddb.seed("OEM_Accounts", [{
        "account_id": "ACC-T1", "market_id": "us", "dealer_id": "D-101", "name": "Test Motors",
        "district_id": "DIST-07", "tier": "A", "targets": {"retail_units": 10}, "competitors_in_pma": ["x"],
        "source_ref": "test:account"}])
    acct = oem_read.call_tool("oem_get_account", OEM, {"account_id": "ACC-T1"})["account"]
    assert acct == {"account_id": "ACC-T1", "market_id": "us", "dealer_id": "D-101", "name": "Test Motors",
                    "district_id": "DIST-07", "tier": "A", "data_source_ref": "test:account", "source_ref": _SRC}


def test_account_and_list_return_the_seeded_fields(fake_ddb):
    acct = oem_read.call_tool("oem_get_account", OEM, {"account_id": "ACC-US-00001"})["account"]
    for k in ("account_id", "account_type", "dealer_id", "district_id", "region_id", "name", "tier",
              "days_supply", "sales_health_index", "service_health_index", "market_id"):
        assert acct[k] not in (None, ""), k
    assert acct["source_ref"] == _SRC and acct["data_source_ref"].startswith("spec:")
    out = oem_read.call_tool("oem_list_accounts", OEM, {"market_id": "de"})
    assert out["count"] == len(_market_rows(fake_ddb, "OEM_Accounts", "de")) > 0
    assert all(a["source_ref"] == _SRC and a["data_source_ref"] for a in out["accounts"])
    scoped = oem_read.call_tool("oem_list_accounts", DEALER, {"market_id": "us"})
    assert {a["dealer_id"] for a in scoped["accounts"]} == {"D-101"}
    assert oem_read.call_tool("oem_get_account", {"dealer_ids": "D-999"},
                              {"account_id": "ACC-US-00001"})["error"] == "account outside caller scope"


# --- the CUSTOM_JWT (Quick) path through the Lambda handler -------------------------

def test_jwt_lambda_path_returns_filled_fields(fake_ddb):
    def ctx(tool):
        return SimpleNamespace(client_context=SimpleNamespace(
            custom={"bedrockAgentCoreToolName": f"oem-read___{tool}", "scope": "retail-ops-oem/read"}))
    alerts = handler.lambda_handler({"market_id": "us", "claims": {"dealer_ids": "D-999"}}, ctx("oem_get_alerts"))
    assert alerts["count"] > 1 and all(a["message"] and a["category"] for a in alerts["alerts"])
    programs = handler.lambda_handler({"market_id": "us"}, ctx("oem_get_incentive_programs"))
    assert programs["count"] > 0
    assert all(p["window"] and p["started_at"] and p["goal"] and p["status"] for p in programs["programs"])


# --- deploy_gateway --code-only ------------------------------------------------------

class _FakeLambda:
    def __init__(self):
        self.calls = []

    def get_function(self, FunctionName):
        self.calls.append(("get_function", FunctionName))
        return {"Configuration": {"Handler": deploy_gateway.HANDLER, "CodeSha256": "old",
                                  "LastUpdateStatus": "Successful"}}

    def update_function_code(self, FunctionName, ZipFile):
        self.calls.append(("update_function_code", FunctionName))
        assert ZipFile[:2] == b"PK"
        return {"CodeSha256": "new"}

    def __getattr__(self, name):                                   # anything else would change more
        raise AssertionError(f"code-only update must not call lambda.{name}")


def test_code_only_update_touches_only_the_function_code():
    lam = _FakeLambda()
    assert deploy_gateway.update_code(False, lam) == "old"
    assert {c[0] for c in lam.calls} == {"get_function"}            # dry run: read only
    lam = _FakeLambda()
    assert deploy_gateway.update_code(True, lam) == "new"
    writes = [c for c in lam.calls if c[0] != "get_function"]
    # the code, and the freshness stamp (a tag — metadata, not configuration); never the configuration
    assert [c for c in writes if c[0] != "tag_resource"] == [("update_function_code", deploy_gateway.GW_LAMBDA)]
