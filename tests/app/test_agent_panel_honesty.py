"""The agent-panel honesty audit (user decision 2026-10-22): every automation the
console's agent bar lists does what it says, on the trigger it shows, and leaves
a run "last ran" can read.

  * store-digest: the nightly model-written cockpit insights, and the cockpits
    showing them (or the figures-only text when none is current);
  * predictive-scan: the nightly predictive part watch, store by store;
  * job_handler: schedules run every market, applied in-process runs trace;
  * automation_traces + handler: on-demand automations trace as agent runs;
  * GET /traces/latest: "last ran" outside a journey;
  * deploy_l6: every job the bar calls scheduled ships enabled, in every market.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

import pytest

from app.infra import deploy_l6, iam_roles
from app.services.api import automation_traces, handler, job_handler
from app.services.api.routes import shell
from app.services.dealer import cockpit, store_digest
from app.services.service import predictive
from tests.app.conftest import ROOT, api_event, call

MGR = dict(user="U-US-00001", role="dealer_manager", dealer_ids="D-101")
SVC_MGR = dict(user="U-US-00003", role="dealer_service_manager", dealer_ids="D-101")


@pytest.fixture(autouse=True)
def _fresh_caches():
    cockpit._CACHE.clear()
    shell._LATEST_CACHE.update(at=0.0, rows=[])
    yield
    cockpit._CACHE.clear()
    shell._LATEST_CACHE.update(at=0.0, rows=[])


def _bedrock(calls: list):
    def ground(question, facts, passages, *, persona, fallback, **_kw):
        calls.append({"question": question, "facts": facts, "persona": persona})
        # states no number of its own: a served insight may only quote figures that are in its facts
        return {"text": "Written insight from the figures.", "engine": "bedrock", "model": "test-model",
                "guardrail": {"applied": True, "intervened": False}}
    return ground


def _insights(fake_ddb) -> list[dict]:
    return [r for r in fake_ddb.rows("DEALER_Recommendations") if r.get("action") == store_digest.ACTION]


# --- store-digest -------------------------------------------------------------------

def test_store_digest_dry_run_builds_facts_without_a_model_call_or_a_write(fake_ddb, monkeypatch):
    def _no_model(*_a, **_k):
        raise AssertionError("a dry run must not call the model")
    monkeypatch.setattr(store_digest.generate, "ground", _no_model)
    out = store_digest.run_store_digest("us", apply=False, limit=2)
    assert out["scopes"] == 2 and out["insights"] == 2 * (len(cockpit.TABS) + 1)
    assert set(out["by_status"]) <= {"would_write", "no_facts"} and out["by_status"].get("would_write")
    assert not _insights(fake_ddb)


def test_store_digest_writes_bedrock_insights_the_cockpits_show(fake_ddb, monkeypatch):
    calls: list = []
    monkeypatch.setattr(store_digest.generate, "ground", _bedrock(calls))
    out = store_digest.run_store_digest("us", apply=True, limit=2)          # scopes: ALL, D-101
    assert out["by_status"].get("written") and calls
    # grounded on the cockpit's own figures, in the writer agent's voice
    assert all(c["facts"] for c in calls)
    assert any("Campaign Agent" in c["persona"] for c in calls) and any("Service Agent" in c["persona"] for c in calls)
    rows = {r["recommendation_id"]: r for r in _insights(fake_ddb)}
    funnel = rows["REC-INSIGHT-us-D-101-funnel"]
    assert funnel["agent_id"] == "A5" and funnel["dealer_id"] == "D-101" and funnel["engine"] == "bedrock"
    assert funnel["sources"] and funnel["facts_hash"]
    assert rows["REC-INSIGHT-us-D-101-service_cockpit"]["agent_id"] == "A7"
    assert rows["REC-INSIGHT-us-ALL-funnel"]["dealer_id"] is None

    code, tab = call(api_event("GET", "/dealer/cockpit/funnel", **MGR))
    assert code == 200
    assert tab["insight"]["text"].startswith("Written insight") and tab["insight"]["written_by"] == "A5"
    assert tab["insight"]["figures_only"] and tab["insight"]["written_at"]

    code, svc = call(api_event("GET", "/service/cockpit", **SVC_MGR))
    assert code == 200 and svc["insight"]["written_by"] == "A7"
    # a moved what-if slider keeps the figures-only insight, which follows the sliders
    code, moved = call(api_event("GET", "/service/cockpit", **SVC_MGR, query={"labor_rate_delta_pct": "5"}))
    assert code == 200 and "written_by" not in moved["insight"]


def test_store_digest_reconfirms_unchanged_figures_without_a_model_call(fake_ddb, monkeypatch):
    calls: list = []
    monkeypatch.setattr(store_digest.generate, "ground", _bedrock(calls))
    store_digest.run_store_digest("us", apply=True, limit=1)
    first = len(calls)
    t1 = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
    out = store_digest.run_store_digest("us", apply=True, limit=1, now=t1)
    # every Store Cockpit tab is unchanged; the service cockpit's hours-to-ETA moves with the clock
    assert out["by_status"].get("unchanged", 0) >= len(cockpit.TABS) - 1
    assert len(calls) - first <= out["insights"] - out["by_status"]["unchanged"]


def test_store_digest_model_fallback_never_writes_text_and_is_not_retried_on_the_same_figures(fake_ddb, monkeypatch):
    calls: list = []

    def ground(*_a, **k):
        calls.append(1)
        return {"text": k["fallback"], "engine": "deterministic (ClientError)", "model": None}

    monkeypatch.setattr(store_digest.generate, "ground", ground)
    out = store_digest.run_store_digest("us", apply=True, limit=1)
    assert out["by_status"].get("fallback") and not [r for r in _insights(fake_ddb) if r.get("text")]
    code, tab = call(api_event("GET", "/dealer/cockpit/funnel", **MGR))
    assert code == 200 and "written_by" not in tab["insight"]                 # figures-only text
    first = len(calls)
    t1 = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
    again = store_digest.run_store_digest("us", apply=True, limit=1, now=t1)
    assert again["by_status"].get("held", 0) >= len(cockpit.TABS) - 1     # same figures: no call
    assert len(calls) - first <= again["insights"] - again["by_status"]["held"]


def test_a_grounding_check_fallback_is_retried_once_a_topic_block_is_not(fake_ddb, monkeypatch):
    seen: dict[str, int] = {}

    def ground(question, facts, passages, *, persona, fallback, **_kw):
        key = facts[0]
        seen[key] = seen.get(key, 0) + 1
        if "Service Agent" in persona:                                    # policy block: no retry
            return {"text": "blocked", "engine": "bedrock (guardrail intervened)", "model": "m"}
        if seen[key] == 1:                                                # first sample drifts
            return {"text": fallback, "engine": store_digest.GROUNDING_FALLBACK, "model": None}
        return {"text": "Second sample.", "engine": "bedrock", "model": "m"}

    monkeypatch.setattr(store_digest.generate, "ground", ground)
    out = store_digest.run_store_digest("us", apply=True, limit=1)
    assert out["retried"] >= 1 and out["by_status"]["written"] >= 1 and out["by_status"]["fallback"] == 1
    assert {r["text"] for r in _insights(fake_ddb) if r.get("text")} == {"Second sample."}
    held = [r for r in _insights(fake_ddb) if not r.get("text")]                # the blocked one
    assert len(held) == 1 and held[0]["attempt_engine"] == "bedrock (guardrail intervened)"


def test_a_stale_insight_falls_back_to_the_figures(fake_ddb):
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=store_digest.STALE_HOURS + 1)).isoformat()
    fake_ddb.seed("DEALER_Recommendations", [{
        "recommendation_id": "REC-INSIGHT-us-D-101-funnel", "action": "store_insight", "agent_id": "A5",
        "text": "old", "generated_at": old, "checked_at": old}])
    assert store_digest.stored_insight("us", "D-101", "funnel") is None
    fresh = _dt.datetime.now(_dt.timezone.utc).isoformat()
    fake_ddb.seed("DEALER_Recommendations", [{
        "recommendation_id": "REC-INSIGHT-us-D-101-funnel", "action": "store_insight", "agent_id": "A5",
        "text": "new", "generated_at": fresh, "checked_at": fresh}])
    assert store_digest.stored_insight("us", "D-101", "funnel")["text"] == "new"
    assert store_digest.stored_insight("us", None, "funnel") is None


def test_the_model_label_and_markdown_are_dropped():
    assert store_digest.clean_text("**[AI INSIGHT]** 75% of your 28 **active** journeys stall.") \
        == "75% of your 28 active journeys stall."
    assert store_digest.clean_text("AI Insight: Close rate is 61%.") == "Close rate is 61%."
    assert store_digest.clean_text("Close rate is 61%.") == "Close rate is 61%."


def test_the_market_wide_cockpit_stays_in_its_market(fake_ddb):
    def tabs(m):
        return cockpit.snapshot({"is_oem": True, "market_id": m, "dealer_ids": set()}, None, m, None,
                                fresh=True, overlay=False)["tabs"]
    us, de = tabs("us"), tabs("de")
    assert (us["sales"]["currency"], us["fi"]["currency"]) == ("USD", "USD")
    assert (de["sales"]["currency"], de["fi"]["currency"]) == ("EUR", "EUR")
    # gross is this market's deals only, not both markets' together
    assert us["sales"]["kpis"][1]["value"] != de["sales"]["kpis"][1]["value"]


def test_every_insight_is_told_its_markets_currency(fake_ddb, monkeypatch):
    seen: list[list[str]] = []

    def ground(question, facts, passages, **_k):
        seen.append(facts)
        return {"text": "Written.", "engine": "bedrock", "model": "m"}

    monkeypatch.setattr(store_digest.generate, "ground", ground)
    store_digest.run_store_digest("de", apply=True, limit=2)
    assert seen and all(f[0] == "Currency for every money figure: EUR" for f in seen)
    trade = store_digest.tab_facts("trade", {"kpis": [{"label": "Equity mined", "value": 31423, "format": "money"}]},
                                   "EUR")
    assert "Equity mined: 31,423 EUR" in trade


def test_scope_key():
    assert store_digest.scope_key({"is_oem": False, "dealer_ids": {"D-101"}}) == "D-101"
    assert store_digest.scope_key({"is_oem": True}) == "ALL"
    assert store_digest.scope_key({"is_oem": True}, "D-102") == "D-102"
    assert store_digest.scope_key({"is_oem": False, "dealer_ids": {"D-101", "D-102"}}) is None


# --- predictive-scan ----------------------------------------------------------------

def test_predictive_scan_runs_store_by_store_and_is_idempotent(fake_ddb):
    before = len(fake_ddb.rows("DEALER_Leads"))
    dry = predictive.run_predictive_scan("us", apply=False, limit=3)
    assert dry["stores"] == 3 and dry["applied"] is False and len(fake_ddb.rows("DEALER_Leads")) == before
    out = predictive.run_predictive_scan("us", apply=True, limit=3)
    assert out["leads_raised"] == dry["would_raise"]
    assert all(s["leads_raised"] <= 10 for s in out["by_store"])
    assert len(fake_ddb.rows("DEALER_Leads")) == before + out["leads_raised"]
    assert predictive.run_predictive_scan("us", apply=True, limit=3)["leads_raised"] == 0


# --- job_handler --------------------------------------------------------------------

def _job_traces(fake_ddb, job):
    return [t for t in fake_ddb.rows("PLAT_AgentTraces") if t.get("tool") == f"job.{job}"]


def test_a_schedule_runs_its_job_in_every_market_and_traces_each_run(fake_ddb):
    out = job_handler.lambda_handler({"job": "stall-sweep", "markets": ["us", "de"], "limit": 1})
    assert out["ok"] is True and set(out["by_market"]) == {"us", "de"}
    assert all(r["summary"]["market"] == m for m, r in out["by_market"].items())
    traces = _job_traces(fake_ddb, "stall-sweep")
    assert sorted(t["market_id"] for t in traces) == ["de", "us"] and {t["agent_id"] for t in traces} == {"A2"}
    assert job_handler.lambda_handler({"job": "stall-sweep", "markets": ["US!"]})["ok"] is False
    assert job_handler.lambda_handler({"job": "stall-sweep", "markets": []})["ok"] is False


def test_only_applied_runs_count_as_a_run(fake_ddb):
    job_handler.run_job("district-cycle", {"market": "us", "limit": 1}, apply=False)
    assert not _job_traces(fake_ddb, "district-cycle")
    job_handler.run_job("district-cycle", {"market": "us", "limit": 1}, apply=True)
    (t,) = _job_traces(fake_ddb, "district-cycle")
    assert t["agent_id"] == "A6" and t["outcome"] == "ok"


def test_store_digest_job_runs_in_process_as_the_campaign_agent(fake_ddb, monkeypatch):
    monkeypatch.setattr(store_digest.generate, "ground", _bedrock([]))
    out = job_handler.run_job("store-digest", {"market": "us", "limit": 1}, apply=True)
    assert out.status == "local" and out.body["by_status"].get("written")
    assert _job_traces(fake_ddb, "store-digest")[0]["agent_id"] == "A5"


# --- on-demand automations trace as agent runs --------------------------------------

def test_automation_routes_match():
    run = automation_traces.match("POST", "/journeys/J-US-J1/outreach")
    assert (run.agent_id, run.tool, run.journey_id) == ("A2", "a2.send_outreach", "J-US-J1")
    run = automation_traces.match("POST", "/service/SJ-1/handoff-to-sales")
    assert run.service_journey_id == "SJ-1" and run.journey_id is None
    assert automation_traces.match("GET", "/journeys/J-US-J1/outreach") is None
    assert automation_traces.match("POST", "/journeys/J-US-J1/nudge") is None


def test_a_successful_automation_request_is_the_agents_run_a_refused_one_is_not(fake_ddb):
    ok = api_event("POST", "/assistant/shift-list/publish", user="U-US-00005", role="dealer_bdc", dealer_ids="D-101")
    assert handler.lambda_handler(ok)["statusCode"] == 200
    run = [t for t in fake_ddb.rows("PLAT_AgentTraces") if t.get("tool") == "a2.publish_shift_list"]
    assert len(run) == 1 and run[0]["agent_id"] == "A2" and run[0]["dealer_id"] == "D-101"
    refused = api_event("POST", "/assistant/shift-list/publish", user="U-US-00011", role="dealer_technician",
                        dealer_ids="D-101")
    assert handler.lambda_handler(refused)["statusCode"] == 403
    assert len([t for t in fake_ddb.rows("PLAT_AgentTraces") if t.get("tool") == "a2.publish_shift_list"]) == 1
    assert any(t.get("agent_id") == "L4" and t.get("tool") == "POST /assistant/shift-list/publish"
               for t in fake_ddb.rows("PLAT_AgentTraces"))


# --- GET /traces/latest -------------------------------------------------------------

def test_latest_runs_by_name_in_your_market_and_stores(fake_ddb):
    def t(tid, tool, ts, agent="A2", market="us", dealer=None):
        return {"trace_id": tid, "tool": tool, "ts": ts, "agent_id": agent, "market_id": market,
                "dealer_id": dealer, "outcome": "ok"}
    fake_ddb.seed("PLAT_AgentTraces", [
        t("T1", "job.stall-sweep", "2026-10-20T05:30:00Z"),
        t("T2", "runtime.job.stall-sweep", "2026-10-21T05:30:00Z"),
        t("T3", "job.stall-sweep", "2026-10-22T05:30:00Z", market="de"),
        t("T4", "a2.publish_shift_list", "2026-10-21T09:00:00Z", dealer="D-101"),
        t("T5", "a2.publish_shift_list", "2026-10-21T10:00:00Z", dealer="D-102"),
        t("T6", "job.stall-sweep", "2026-10-23T00:00:00Z", agent="L4"),
    ])
    code, out = call(api_event("GET", "/traces/latest", **MGR,
                               query={"runs": "job.stall-sweep,a2.publish_shift_list,job.nope"}))
    assert code == 200
    assert out["runs"]["job.stall-sweep"]["trace_id"] == "T2"          # newest us agent run; runtime prefix matches
    assert out["runs"]["a2.publish_shift_list"]["trace_id"] == "T4"    # your store, not D-102's
    assert "job.nope" not in out["runs"]
    shell._LATEST_CACHE.update(at=0.0, rows=[])
    code, oem = call(api_event("GET", "/traces/latest", user="U-US-00044", role="oem_dm", is_oem=True,
                               query={"runs": "a2.publish_shift_list"}))
    assert code == 200 and oem["runs"]["a2.publish_shift_list"]["trace_id"] == "T5"
    assert call(api_event("GET", "/traces/latest", **MGR, query={"runs": "DROP TABLE"}))[0] == 400
    assert call(api_event("GET", "/traces/latest", **MGR))[0] == 400
    assert call(api_event("GET", "/traces/latest", role="customer", customer_id="C-US-J1",
                          query={"runs": "job.stall-sweep"}))[0] == 403


# --- deploy_l6: the schedules behind "Scheduled" ------------------------------------

def test_every_job_on_the_bar_ships_enabled_in_every_market():
    jobs = {j[0]: j for j in deploy_l6.JOBS}
    assert {"predictive-scan", "store-digest"} <= set(jobs)
    assert [j for j, row in jobs.items() if not row[5]] == ["autonomy-promotion"]
    for job, (_j, _fn, sname, expr, _d, _on) in jobs.items():
        scheds = deploy_l6.schedules_for(job, sname, expr)
        assert sorted(m for _n, _e, ms in scheds for m in ms) == sorted(deploy_l6.MARKETS), job
        assert scheds[0][0] == sname
    assert deploy_l6.schedules_for("shop-load", "retail-ops-dev-shop-load", "cron(0 10 * * ? *)") == [
        ("retail-ops-dev-shop-load", "cron(0 10 * * ? *)", ["us"]),
        ("retail-ops-dev-shop-load-de", "cron(0 3 * * ? *)", ["de"])]
    assert {"retail-ops-dev-predictive-scan", "retail-ops-dev-store-digest"} <= set(iam_roles.WORKER_FUNCTIONS)


def test_every_run_the_console_names_is_written_by_the_server():
    src = (ROOT / "app/console/src/lib/agents.ts").read_text()
    runs = {k for block in re.findall(r"runs:\s*\[([^\]]*)\]", src) for k in re.findall(r'"([^"]+)"', block)}
    enabled = {j[0] for j in deploy_l6.JOBS if j[5]}
    for k in runs:
        if k.startswith("job."):
            assert k[4:] in enabled, k
        else:
            assert k in automation_traces.RUN_NAMES or k == "refresh_audiences", k
    assert json.loads(json.dumps(sorted(runs)))                               # non-empty
