"""Print, as JSON, every AWS resource name the deployers derive for the current env.

Run in a fresh process (`python3 -m tests.app.naming_collect`) so RETAIL_OPS_ENV
is read at import time exactly as a deployer reads it; tests/app/test_naming.py
compares the dev output with tests/golden/naming_dev.json (the names the
deployers used before the environment name became configurable) and checks the
AWS length limits for a long env. Imports only; no AWS call is made.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

ACCOUNT = "123456789012"
POOL_ID = "us-east-1_AbCdEfGh1"


class _FakeLambda:
    """Records the layer name deploy_l4 looks up (no AWS call)."""

    def __init__(self) -> None:
        self.layer = None

    def list_layer_versions(self, **kw):
        self.layer = kw.get("LayerName")
        return {"LayerVersions": []}


def _m(name: str):
    return importlib.import_module(f"app.infra.{name}")


def _attrs(mod, names: list[str]) -> dict:
    out = {}
    for n in names:
        v = getattr(mod, n)
        out[f"{mod.__name__.rsplit('.', 1)[-1]}.{n}"] = v.name if isinstance(v, Path) else v
    return out


def collect() -> dict:
    out: dict = {}
    ir = _m("iam_roles")
    out |= _attrs(ir, ["ENV_PREFIX", "ROLE_AUTHORIZER", "ROLE_SERVICE", "ROLE_WORKER", "ROLE_SUBSCRIBER",
                       "ROLE_APPROVALS", "ROLE_STREAM", "LEGACY_SHARED_ROLE", "WORKER_FUNCTIONS"])
    for role, (policy, builder) in ir.ROLE_CATALOG.items():
        out[f"iam_roles.policy[{role}]"] = [policy, builder(ACCOUNT)]

    l4 = _m("deploy_l4")
    out |= _attrs(l4, ["ENV_PREFIX", "ROLE_NAME", "AUTH_FN", "SVC_FN", "API_NAME", "STAGE",
                       "AGENTCORE_CONFIG", "CONSOLE_CONFIG"])
    fake = _FakeLambda()
    l4._jwt_layer_arn(fake, ACCOUNT)
    out["deploy_l4.jwt_layer"] = fake.layer

    l6 = _m("deploy_l6")
    out |= _attrs(l6, ["SCHED_ROLE", "JOBS"])
    out["deploy_l6.schedules"] = [s for job, _fn, sname, expr, _d, _e in l6.JOBS for s in l6.schedules_for(job, sname, expr)]
    out["deploy_l6.env_prefix"] = l6._base_env()["RETAIL_OPS_ENV_PREFIX"]

    out |= _attrs(_m("deploy_events"), ["BUS_NAME", "SUB_FN", "DLQ_NAME", "RULE_NAME"])
    out |= _attrs(_m("deploy_approvals"), ["SUBMIT_FN", "SFN_ROLE", "STATE_MACHINE", "JOURNEYS_FN"])
    lake = _m("deploy_lake")
    out |= _attrs(lake, ["CONFIG_PATH", "BUS", "STREAM", "RULE", "FIREHOSE_ROLE", "EVENTS_ROLE", "DATABASE",
                         "WORKGROUP", "ENV_PREFIX"])
    out["deploy_lake.bucket"] = lake._bucket(ACCOUNT)
    out |= _attrs(_m("deploy_lake_analytics"), ["DATABASE", "WORKGROUP", "LAKE_CONFIG"])
    out |= _attrs(_m("deploy_lake_rollups"), ["DATABASE", "WORKGROUP", "ENV_PREFIX", "LAKE_CONFIG"])
    out |= _attrs(_m("deploy_quick_index"), ["LAKE_CONFIG"])
    out |= _attrs(_m("deploy_guardrails"), ["NAME", "DIGEST_NAME", "CONFIG_PATH", "DIGEST_CONFIG_PATH"])
    kb = _m("deploy_knowledge_base")
    out |= _attrs(kb, ["KB_NAME", "ROLE", "INDEX", "CONFIG_PATH"])
    out["deploy_knowledge_base.lake_bucket"] = kb._lake_bucket(ACCOUNT)
    out["deploy_knowledge_base.vector_bucket"] = kb._vec_bucket(ACCOUNT)
    al = _m("deploy_alarms")
    out |= _attrs(al, ["FUNCTIONS", "UNKNOWN_NAMESPACE"])
    out["deploy_alarms.alarms"] = [al._alarm_name(fn, s) for fn in al.FUNCTIONS for _, s, _ in al.METRICS]
    out["deploy_alarms.runtime_unknown"] = [[s["alarm"], s["filter_name"], s["log_group"]]
                                            for s in al.runtime_unknown_specs()]
    out |= _attrs(_m("deploy_waf"), ["ACL_NAME", "IPSET_NAME", "CONFIG_PATH", "CONSOLE_CONFIG"])
    con = _m("deploy_console")
    out |= _attrs(con, ["OAC_NAME", "DIST_COMMENT", "CALLER_REF", "REWRITE_FN", "STREAM_OAC_NAME", "CONFIG_PATH"])
    out["deploy_console.bucket"] = con._bucket(ACCOUNT)
    out |= _attrs(_m("deploy_gateway"), ["GW_LAMBDA", "GW_LAMBDA_ROLE", "GW_ROLE", "GW_NAME", "CONFIG_PATH"])
    out |= _attrs(_m("deploy_gateway_quick"), ["GW_NAME", "QUICK_MCP_SECRET", "CONFIG_PATH"])
    out |= _attrs(_m("deploy_memory"), ["NAME", "API_NAME", "TABLE", "CONFIG_PATH"])
    out |= _attrs(_m("deploy_streaming"), ["ENV_PREFIX", "STREAM_FN", "CONFIG_PATH"])
    out |= _attrs(_m("deploy_quick_groups"), ["ENV_PREFIX"])
    qd = _m("deploy_quick_datasets")
    out |= _attrs(qd, ["DATABASE", "WORKGROUP", "DATA_SOURCE_ID", "QUICK_ROLE_POLICY", "LAKE_CONFIG", "QUICK_CONFIG"])
    qd.LAKE_CONFIG = Path("/nonexistent/lake-config.json")      # the derived name, not this machine's state
    bucket = qd._lake_bucket(ACCOUNT)
    out["deploy_quick_datasets.lake_bucket"] = bucket
    out["deploy_quick_datasets.role_policy"] = qd.quick_role_policy(ACCOUNT, bucket)
    out |= _attrs(_m("deploy_quick"), ["CONFIG_PATH"])
    out |= _attrs(_m("deploy_quick_content"), ["CONFIG_PATH", "STEM"])
    cg = _m("cognito_setup")
    out |= _attrs(cg, ["POOL_NAME", "CLIENT_NAME", "HOSTED_UI_CLIENT_NAME", "CONFIG_PATH"])
    out |= _attrs(_m("publish_jwt_layer"), ["LAYER_NAME"])

    ac = _m("deploy_agentcore")
    out |= _attrs(ac, ["ENV", "EXEC_ROLE", "POLICY_NAME", "ENV_PREFIX", "EVENT_BUS", "RUNTIME_PATTERN",
                       "CONFIG_PATH", "GUARDRAIL_CONFIG", "KB_CONFIG", "LEGACY_NAME", "LEGACY_CODE_KEY"])
    out["deploy_agentcore.bucket"] = ac._bucket(ACCOUNT)
    out["deploy_agentcore.exec_policy"] = ac._exec_policy(ACCOUNT)
    from app.agentcore import registry
    out["registry.runtime_names"] = registry.runtime_names()
    out["deploy_agentcore.code_keys"] = [ac.code_key(n) for n in registry.runtime_names()]
    td = _m("teardown")
    out["teardown.runtime_names_for"] = td.runtime_names_for("retail-ops", registry.ENV)

    from app.services.common import ddb
    from app.services.events import bus
    from app.services.gateway import handler as gw_handler
    out["services.ddb.DEFAULT_ENV_PREFIX"] = ddb.DEFAULT_ENV_PREFIX
    out["services.bus.BUS_NAME"] = bus.BUS_NAME
    out["services.gateway.ENV_PREFIX"] = gw_handler.ENV_PREFIX
    return out


def extras() -> dict:
    """Names only the current code can report (the Cognito domain prefix rule)."""
    cg = _m("cognito_setup")
    return {"cognito_setup.domain_prefix": cg.default_domain_prefix(POOL_ID)}


if __name__ == "__main__":
    import sys
    data = collect()
    if "--extras" in sys.argv:
        data |= extras()
    print(json.dumps(data, indent=1, sort_keys=True))
