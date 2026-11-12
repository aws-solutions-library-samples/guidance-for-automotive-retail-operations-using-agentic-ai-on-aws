# ruff: noqa: S101
"""Agent registry (ADR 0009): names, ownership, claims and argument contracts."""
from __future__ import annotations

import inspect
import json
import logging
import re
import sys

import pytest

from app.agentcore import registry
from app.agentcore.registry import ArgError, ArgSpec, ClaimsError
from app.services.api import handler, job_handler
from app.services.common import authz
from app.services.marketing import analytics
from tests.app.conftest import ROOT, api_event

_NAME = re.compile(r"^retail_ops_dev_a[1-7]_[a-z_]+$")
_AWS_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
_AGENT_OWNED_JOBS = {"rescore-profiles", "nurture-tick", "stall-sweep", "equity-monitor", "nightly-attribution",
                     "shop-load", "service-followup-tick", "service-lane-monitor", "predictive-scan"}


# --- names and ownership ----------------------------------------------------------

def test_six_agents_with_valid_unique_runtime_names():
    assert list(registry.AGENTS) == ["A1", "A2", "A3", "A4", "A5", "A7"]
    names = registry.runtime_names()
    assert names == [a.runtime_name for a in registry.AGENTS.values()]
    assert len(set(names)) == 6
    for name in names:
        assert _NAME.fullmatch(name), name
        assert _AWS_NAME.fullmatch(name) and len(name) <= 48
    assert registry.agent("a3").runtime_name == "retail_ops_dev_a3_sales"
    assert registry.agent("A6") is None and registry.agent(None) is None


def test_names_cli_prints_the_six_runtime_names(capsys):
    assert registry.main(["--names"]) == 0
    assert capsys.readouterr().out.split() == registry.runtime_names()
    with pytest.raises(SystemExit):
        registry.main([])


def test_display_names_match_the_console_agent_labels():
    src = (ROOT / "app/console/src/lib/agents.ts").read_text()
    block = re.search(r"AGENT_LABEL[^=]*=\s*\{(.*?)\}", src, re.DOTALL)
    assert block, "AGENT_LABEL not found in agents.ts"
    labels = dict(re.findall(r'(A\d):\s*"([^"]+)"', block.group(1)))
    assert labels["A6"] == "Field Sales Agent"          # A6 stays in Quick: a label, no runtime
    for code, spec in registry.AGENTS.items():
        assert spec.display == labels[code]


def test_engines_calls_and_knowledge_base():
    a3 = registry.agent("A3")
    assert a3.engine == "reasoning" and a3.deps == "strands" and a3.reasoning == "app.services.agents.a3_agent:run"
    assert a3.needs_kb and not any(a.needs_kb for c, a in registry.AGENTS.items() if c != "A3")
    assert all(a.engine == "tools" and a.reasoning is None for c, a in registry.AGENTS.items() if c != "A3")
    assert registry.agent("A2").calls == ("A1", "A3", "A4")
    assert all(not a.calls for c, a in registry.AGENTS.items() if c != "A2")


def test_deploy_order_puts_callees_before_callers():
    order = registry.deploy_order()
    assert sorted(order) == sorted(registry.AGENTS) and order[-1] == "A2"
    for code in order:
        for callee in registry.AGENTS[code].calls:
            assert order.index(callee) < order.index(code)


def test_every_target_imports_and_accepts_its_declared_args():
    for spec in registry.AGENTS.values():
        for tool in spec.tools:
            fn = registry.resolve(tool.target)
            inspect.signature(fn).bind_partial({}, None, journey=None, **{k: None for k in tool.args})
            assert registry.tool(spec.code, tool.name) is tool
        for job in spec.jobs:
            fn = registry.resolve(job.target)
            inspect.signature(fn).bind_partial(market="us", apply=False, limit=None,
                                               **{k: None for k in job.passthrough})
        if spec.reasoning:
            inspect.signature(registry.resolve(spec.reasoning)).bind_partial(
                "q", None, {}, env_prefix=None, correlation_id=None)
    with pytest.raises(ValueError):
        registry.resolve("no-colon")


def test_exactly_the_nine_agent_owned_jobs():
    assert set(registry.job_names()) == _AGENT_OWNED_JOBS and len(registry.job_names()) == 9
    assert registry.job_owner("district-cycle") is None and registry.job_owner("autonomy-promotion") is None
    assert registry.job_owner("store-digest") is None and registry.job_owner("predictive-scan") == "A7"
    assert registry.job_owner("equity-monitor") == "A4" and registry.job_owner("shop-load") == "A7"
    assert registry.job_spec("shop-load").passthrough == ("dealer_id",)
    assert _AGENT_OWNED_JOBS <= set(job_handler.JOBS)


@pytest.mark.parametrize("job", sorted(_AGENT_OWNED_JOBS))
def test_job_table_reaches_the_registry_target(job, monkeypatch):
    """job_handler.JOBS[job] calls the same function the registry names (spy on
    every binding of it, so a re-export such as district_cycle's counts)."""
    spec = registry.job_spec(job)
    original = registry.resolve(spec.target)
    attr = spec.target.split(":")[1]
    calls: list[dict] = []

    def _spy(**kw):
        calls.append(kw)
        return {"spied": True}

    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").startswith("app.services") and getattr(mod, attr, None) is original:
            monkeypatch.setattr(mod, attr, _spy)
    out = job_handler.JOBS[job]({"market": "us"}, False)
    assert out == {"spied": True}
    assert calls and calls[0]["market"] == "us" and calls[0]["apply"] is False


def test_no_entry_for_the_field_sales_agent():
    assert "A6" not in registry.AGENTS
    assert all(registry.job_owner(j) != "A6" for j in registry.job_names())


# --- role references -----------------------------------------------------------------

def test_role_references_resolve_to_the_routes_own_sets():
    from app.services.api.routes import marketing_oem
    refs = {t.name: t.roles for a in registry.AGENTS.values() for t in a.tools if t.roles}
    assert set(refs) == {"recommend_budget_shift", "submit_budget_shift"}
    for ref in refs.values():
        assert isinstance(registry.resolve_roles(ref), frozenset)
    assert registry.resolve_roles(refs["recommend_budget_shift"]) is authz.OEM_READ_ROLES
    assert registry.resolve_roles(refs["submit_budget_shift"]) is analytics.SUBMIT_ROLES
    assert marketing_oem.SHIFT_SUBMIT is analytics.SUBMIT_ROLES
    with pytest.raises(TypeError):
        registry.resolve_roles("app.agentcore.registry:STEM_US")


# --- claims ------------------------------------------------------------------------

def _authorizer(ev: dict, **extra) -> dict:
    ev["requestContext"]["authorizer"].update(extra)
    return ev


def _round_trip(claims: dict) -> dict:
    return registry.normalize_claims(json.loads(json.dumps(registry.serialize_claims(claims))))


def _claim_fixtures() -> dict[str, dict]:
    advisor = _authorizer(api_event("GET", "/x", user="U-US-00002", role="dealer_advisor",
                                    dealer_ids="D-101,D-102"),
                          groups="dealer_advisor,dealer_manager", extra_screens="dealer.cockpit")
    district_dm = _authorizer(api_event("GET", "/x", user="U-US-00044", role="oem_dm", is_oem=True),
                              scope_node_id="DIST-07")
    customer = api_event("GET", "/x", user="C-US-J1", role="customer", customer_id="C-US-J1")
    as_dealer = api_event("GET", "/x", user="U-US-00051", role="admin_tenant", is_oem=True)
    as_dealer["headers"] = {"X-Act-As": "U-US-00002"}
    as_customer = api_event("GET", "/x", user="U-US-00051", role="admin_tenant", is_oem=True)
    as_customer["headers"] = {"X-Act-As": "cust:C-US-J1"}
    events = {"advisor": advisor, "district_dm": district_dm, "customer": customer,
              "admin_as_dealer": as_dealer, "admin_as_customer": as_customer}
    return {name: handler._claims(ev) for name, ev in events.items()}


def test_claims_round_trip_unchanged_for_every_caller_kind(fake_ddb):
    fixtures = _claim_fixtures()
    assert fixtures["advisor"]["extra_screens"] == ["dealer.cockpit"]
    assert fixtures["advisor"]["groups"] == ["dealer_advisor", "dealer_manager"]
    assert fixtures["district_dm"]["scope_node_id"] == "DIST-07" and fixtures["district_dm"]["is_oem"] is True
    assert fixtures["admin_as_dealer"]["impersonated_by"] == "U-US-00051"
    assert fixtures["admin_as_dealer"]["dealer_ids"]           # resolved from the impersonated user
    assert fixtures["admin_as_customer"]["impersonating_role"] == "customer"
    for name, claims in fixtures.items():
        back = _round_trip(claims)
        assert back == claims, name
        assert isinstance(back["dealer_ids"], set)
        assert set(back) == set(claims), name


def test_claims_accept_csv_dealers_and_string_booleans():
    out = registry.normalize_claims({"user_id": "U-1", "role": "dealer_advisor", "dealer_ids": "D-101, D-102,",
                                     "is_oem": "TRUE", "market_id": "", "scope_node_id": None,
                                     "customer_id": ""})
    assert out["dealer_ids"] == {"D-101", "D-102"} and out["is_oem"] is True
    assert out["market_id"] == "" and out["scope_node_id"] is None and out["customer_id"] == ""
    assert registry.normalize_claims({"user_id": "U-1", "role": "x", "is_oem": "false"})["is_oem"] is False
    assert registry.normalize_claims({"user_id": "U-1", "role": "x", "dealer_ids": ("D-1",)})["dealer_ids"] == {"D-1"}
    # no defaults: absent optional keys stay absent
    assert registry.normalize_claims({"user_id": "U-1", "role": "x"}) == {"user_id": "U-1", "role": "x"}


def test_unknown_claim_keys_are_dropped_and_logged_by_name_only(caplog):
    with caplog.at_level(logging.INFO, logger="app.agentcore.registry"):
        out = registry.normalize_claims({"user_id": "U-1", "role": "x", "email": "someone-secret"})
    assert "email" not in out
    assert "email" in caplog.text and "someone-secret" not in caplog.text


@pytest.mark.parametrize("raw, field", [
    ({"role": "dealer_advisor"}, "user_id"),
    ({"user_id": "", "role": "dealer_advisor"}, "user_id"),
    ({"user_id": "bad id", "role": "dealer_advisor"}, "user_id"),
    ({"user_id": 7, "role": "dealer_advisor"}, "user_id"),
    ({"user_id": "U-1"}, "role"),
    ({"user_id": "U-1", "role": "Dealer Advisor"}, "role"),
    ({"user_id": "U-1", "role": "x", "market_id": "US"}, "market_id"),
    ({"user_id": "U-1", "role": "x", "is_oem": "yes"}, "is_oem"),
    ({"user_id": "U-1", "role": "x", "dealer_ids": ["D 1"]}, "dealer_ids"),
    ({"user_id": "U-1", "role": "x", "dealer_ids": 5}, "dealer_ids"),
    ({"user_id": "U-1", "role": "x", "groups": "dealer_advisor"}, "groups"),
    ({"user_id": "U-1", "role": "x", "extra_screens": ["Dealer.Cockpit"]}, "extra_screens"),
    ({"user_id": "U-1", "role": "x", "scope_node_id": "a b"}, "scope_node_id"),
    (None, "user_id"),
])
def test_malformed_claims_raise_claims_error_naming_the_field(raw, field):
    with pytest.raises(ClaimsError) as e:
        registry.normalize_claims(raw)
    assert e.value.field == field and f"claims.{field}" in str(e.value)


def test_claims_limits_on_list_sizes():
    with pytest.raises(ClaimsError):
        registry.normalize_claims({"user_id": "U-1", "role": "x", "groups": ["g"] * 51})
    with pytest.raises(ClaimsError):
        registry.normalize_claims({"user_id": "U-1", "role": "x", "dealer_ids": [f"D-{i}" for i in range(501)]})


# --- arguments ---------------------------------------------------------------------

def _check(spec: ArgSpec, value):
    return registry.validate_args(registry.ToolSpec("t", "m:f", {"v": spec}, "none", None, True, True),
                                  {"v": value})["v"]


def test_argspec_types_patterns_and_lengths():
    with pytest.raises(ArgError):
        _check(ArgSpec(int, minimum=0), True)              # bool is not an int
    assert _check(ArgSpec(int, minimum=0), 3) == 3
    with pytest.raises(ArgError):
        _check(ArgSpec(bool, pattern=None), 1)             # 1 is not a bool
    assert _check(ArgSpec(bool, pattern=None), False) is False
    note = registry.tool("A5", "submit_budget_shift").args["note"]
    assert _check(note, "Shift budget to the spring EV event, please.").startswith("Shift")
    with pytest.raises(ArgError) as e:
        _check(note, "x" * 501)
    assert e.value.field == "v"
    market = registry.tool("A5", "submit_budget_shift").args["market"]
    with pytest.raises(ArgError):
        _check(market, "US")
    with pytest.raises(ArgError):
        _check(market, "us\n")                              # the whole value must match
    assert _check(market, "de") == "de"


def test_validate_args_rejects_undeclared_and_missing_and_applies_defaults():
    shift = registry.tool("A5", "recommend_budget_shift")
    assert registry.validate_args(shift, {}) == {"market": None, "apply": False}
    assert registry.validate_args(shift, None) == {"market": None, "apply": False}
    with pytest.raises(ArgError) as e:
        registry.validate_args(shift, {"apply": True, "dry": True})
    assert e.value.field == "dry"
    rank = registry.tool("A1", "rank_vehicles")
    with pytest.raises(ArgError) as e:
        registry.validate_args(rank, {})
    assert e.value.field == "journey_id"
    with pytest.raises(ArgError):
        registry.validate_args(rank, {"journey_id": "J US J1"})
    with pytest.raises(ArgError):
        registry.validate_args(rank, ["J-US-J1"])
    assert registry.validate_args(rank, {"journey_id": "J-US-J1"}) == {"journey_id": "J-US-J1"}


# --- job payloads ----------------------------------------------------------------------

@pytest.mark.parametrize("payload, expected", [
    ({}, {"market": "us", "limit": None}),
    (None, {"market": "us", "limit": None}),
    ({"market": None}, {"market": "us", "limit": None}),
    ({"limit": 0}, {"market": "us", "limit": 0}),
    ({"limit": None, "market": "de"}, {"market": "de", "limit": None}),
])
def test_job_payload_accepts_todays_values(payload, expected):
    assert registry.validate_job_payload(payload) == expected


@pytest.mark.parametrize("payload, field", [
    ({"limit": "5"}, "limit"),
    ({"limit": True}, "limit"),
    ({"limit": -1}, "limit"),
    ({"market": "US"}, "market"),
    ({"job": "equity-monitor"}, "job"),
    ({"dealer_id": "D-101"}, "dealer_id"),
    ("market=us", "payload"),
])
def test_job_payload_rejects_bad_values_naming_the_field(payload, field):
    with pytest.raises(ArgError) as e:
        registry.validate_job_payload(payload)
    assert e.value.field == field


def test_job_payload_passthrough_is_declared_per_job():
    out = registry.validate_job_payload({"dealer_id": "D-101"}, passthrough=("dealer_id",))
    assert out == {"market": "us", "limit": None, "dealer_id": "D-101"}
    assert registry.validate_job_payload({}, passthrough=("dealer_id",))["dealer_id"] is None
    with pytest.raises(ArgError):
        registry.validate_job_payload({"dealer_id": "D 101"}, passthrough=("dealer_id",))


# --- runtime identity -------------------------------------------------------------------

def test_current_agent_id_reads_the_override_then_the_environment(monkeypatch):
    assert registry.current_agent_id() is None
    monkeypatch.setenv("RETAIL_OPS_AGENT_ID", "a4")
    assert registry.current_agent_id() == "A4"
    with registry.acting_as("A1"):
        assert registry.current_agent_id() == "A1"
    assert registry.current_agent_id() == "A4"
    monkeypatch.delenv("RETAIL_OPS_AGENT_ID")
    with pytest.raises(RuntimeError), registry.acting_as("A2"):
        raise RuntimeError("boom")
    assert registry.current_agent_id() is None
