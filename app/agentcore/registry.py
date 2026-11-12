"""Agent registry — the single source of truth for the per-agent AgentCore runtimes (ADR 0009).

Each custom agent runs in its own Amazon Bedrock AgentCore runtime. This module
says which agents exist, what their runtimes are called, which tools and jobs
each one serves, which agents it may call, and how claims, tool arguments and
job payloads are shaped when they cross a runtime boundary. The runtime
dispatcher (`app/agentcore/agent.py`), the service-side client
(`app/agentcore/client.py`), the deployer, teardown, `deploy/verify.sh` and the
tests all read it.

Stdlib only, and nothing from `app.services` is imported at module level:
targets and role sets are dotted `"module:attr"` strings resolved at call time,
so the deployer can read the registry without the service layer.

  AGENTS / agent(code) / tool(code, name)    the agent table and lookups
  job_owner(job) / job_names()               the eight agent-owned jobs
  runtime_names() / deploy_order()           runtime names; callees before callers
  parse_codes(raw)                           "A3,A2" from a deployer CLI -> codes (raises ValueError)
  validate_args(spec, args)                  tool arguments (raises ArgError)
  validate_job_payload(payload, passthrough) job payloads (raises ArgError)
  serialize_claims(c) / normalize_claims(raw) claims on the wire (raises ClaimsError)
  resolve(ref) / resolve_roles(ref)          "module:attr" -> object / frozenset
  current_agent_id() / acting_as(code)       the agent this process serves

`python3 -m app.agentcore.registry --names` prints the runtime names, one per line.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)

STEM_US = "retail_ops"            # technical stem, underscores (spec §13, 2026-10-19)
# The environment name, default `dev` (ADR 0007): the deployers read and validate
# RETAIL_OPS_ENV in app/infra/naming.py and set it on every runtime, so a runtime
# reports the name it was deployed under. Read here, not imported, because the
# runtime bundles carry no app/infra module.
ENV = os.environ.get("RETAIL_OPS_ENV") or "dev"
LEGACY_RUNTIME_NAME = "retail_ops_agent"   # the single shared runtime this registry replaces

ID_PATTERN = r"^[A-Za-z0-9_.:#-]{1,64}$"
MAX_BODY_BYTES = 256 * 1024       # request body limit: checked by the client before sending and by the dispatcher

# Service modules the runtime dispatcher loads with importlib (it imports none
# statically, so a bundle never drags in the API handler). The bundle builder
# adds these, the tool and job targets and the reasoning entry as closure roots.
DISPATCHER_MODULES = (
    "app.services.common.authz",
    "app.services.common.ddb",
    "app.services.common.trace",
    "app.services.platform.mode",
)


# --- specs --------------------------------------------------------------------

@dataclass(frozen=True)
class ArgSpec:
    """One declared argument. `kind` is str, int or bool; int rejects bool."""
    kind: type
    required: bool = True
    pattern: str | None = ID_PATTERN   # str only; None = free text (then max_length applies)
    max_length: int | None = None
    minimum: int | None = None         # int only
    maximum: int | None = None         # int only
    default: object = None             # used when not required and absent
    nullable: bool = False             # an explicit null is accepted and replaced by `default`


@dataclass(frozen=True)
class ToolSpec:
    """One tool an agent's runtime serves (`kind: tool`)."""
    name: str
    target: str                   # "app.agentcore.tools.a1:rank_vehicles"
    args: dict[str, ArgSpec]      # every accepted argument; anything else is rejected
    scope: str                    # "journey" | "oem" | "none"
    roles: str | None             # "module:attr" of the route's own role set; None = scope check only
    read_only: bool
    idempotent: bool              # a repeat with the same args has no extra effect
    timeout_s: int = 20


@dataclass(frozen=True)
class JobSpec:
    """One scheduled / on-demand job an agent's runtime serves (`kind: job`)."""
    name: str                     # key in job_handler.JOBS
    target: str                   # "app.services.agents.equity_monitor:run_equity_monitor"
    passthrough: tuple[str, ...] = ()   # extra payload keys (shop-load: dealer_id)


@dataclass(frozen=True)
class AgentSpec:
    """One custom agent and its runtime."""
    code: str
    slug: str
    display: str                  # user-facing name, equal to the console AGENT_LABEL
    engine: str                   # "tools" | "reasoning"
    deps: str                     # dependency profile: "tools" | "strands"
    reasoning: str | None         # "module:attr" of the reasoning entry (kind: agent), or None
    tools: tuple[ToolSpec, ...]
    jobs: tuple[JobSpec, ...]
    calls: tuple[str, ...]        # agents this runtime may invoke (A2A-shaped InvokeAgentRuntime)
    needs_kb: bool = False        # wire RETAIL_OPS_KNOWLEDGE_BASE_ID into this runtime's env

    def runtime_name_for(self, stem_us: str = STEM_US, env: str = ENV) -> str:
        return f"{stem_us}_{env}_{self.code.lower()}_{self.slug}"

    @property
    def runtime_name(self) -> str:
        return self.runtime_name_for()


class ArgError(ValueError):
    """A tool argument or job payload key failed its ArgSpec. The message names
    the field. Callers catch it before ValueError (job routes map ValueError to 404)."""

    def __init__(self, field: str, reason: str = "is invalid") -> None:
        self.field = field
        super().__init__(f"{field} {reason}")


class ClaimsError(ValueError):
    """A claims field is missing or malformed. The message names the field."""

    def __init__(self, field: str, reason: str = "is invalid") -> None:
        self.field = field
        super().__init__(f"claims.{field} {reason}")


# --- the agent table (design §5.1 / §5.2) ---------------------------------------

_JOURNEY_ARGS = {"journey_id": ArgSpec(str)}
_MARKET = ArgSpec(str, required=False, pattern=r"^[a-z]{2}$")

AGENTS: dict[str, AgentSpec] = {a.code: a for a in (
    AgentSpec(
        code="A1", slug="discovery", display="Discovery Agent", engine="tools", deps="tools", reasoning=None,
        tools=(ToolSpec("rank_vehicles", "app.agentcore.tools.a1:rank_vehicles", _JOURNEY_ARGS,
                        scope="journey", roles=None, read_only=True, idempotent=True, timeout_s=8),),
        jobs=(JobSpec("rescore-profiles", "app.services.agents.rescore:run_rescore_profiles"),),
        calls=()),
    AgentSpec(
        code="A2", slug="journey", display="Journey Agent", engine="tools", deps="tools", reasoning=None,
        tools=(ToolSpec("build_sales_package", "app.agentcore.tools.a2:build_sales_package", _JOURNEY_ARGS,
                        scope="journey", roles=None, read_only=False, idempotent=True, timeout_s=20),),
        jobs=(JobSpec("nurture-tick", "app.services.journeys.nurture_tick:run_nurture_tick"),
              JobSpec("stall-sweep", "app.services.journeys.stall_sweep:run_stall_sweep")),
        calls=("A1", "A3", "A4")),
    AgentSpec(
        code="A3", slug="sales", display="Sales Agent", engine="reasoning", deps="strands",
        reasoning="app.services.agents.a3_agent:run",
        tools=(ToolSpec("build_assist", "app.agentcore.tools.a3:build_assist", _JOURNEY_ARGS,
                        scope="journey", roles=None, read_only=True, idempotent=True, timeout_s=8),),
        jobs=(), calls=(), needs_kb=True),
    AgentSpec(
        code="A4", slug="trade_in", display="Trade-In Agent", engine="tools", deps="tools", reasoning=None,
        tools=(ToolSpec("build_equity", "app.agentcore.tools.a4:build_equity", _JOURNEY_ARGS,
                        scope="journey", roles=None, read_only=True, idempotent=True, timeout_s=8),),
        jobs=(JobSpec("equity-monitor", "app.services.agents.equity_monitor:run_equity_monitor"),),
        calls=()),
    AgentSpec(
        code="A5", slug="campaign", display="Campaign Agent", engine="tools", deps="tools", reasoning=None,
        tools=(ToolSpec("recommend_budget_shift", "app.agentcore.tools.a5:recommend_budget_shift",
                        {"market": _MARKET,
                         "apply": ArgSpec(bool, required=False, pattern=None, default=False)},
                        scope="oem", roles="app.services.common.authz:OEM_READ_ROLES",
                        read_only=False, idempotent=True, timeout_s=20),
               ToolSpec("submit_budget_shift", "app.agentcore.tools.a5:submit_budget_shift",
                        {"market": _MARKET,
                         "note": ArgSpec(str, required=False, pattern=None, max_length=500)},
                        scope="oem", roles="app.services.marketing.analytics:SUBMIT_ROLES",
                        read_only=False, idempotent=True, timeout_s=20)),
        jobs=(JobSpec("nightly-attribution", "app.services.oem.campaigns:run_nightly_attribution"),),
        calls=()),
    AgentSpec(
        code="A7", slug="service", display="Service Agent", engine="tools", deps="tools", reasoning=None,
        tools=(),
        jobs=(JobSpec("shop-load", "app.services.service.shop_load:run_shop_load", passthrough=("dealer_id",)),
              JobSpec("service-followup-tick", "app.services.service.followup_tick:run_service_followup_tick"),
              JobSpec("service-lane-monitor", "app.services.service.lane_monitor:run_service_lane_monitor"),
              JobSpec("predictive-scan", "app.services.service.predictive:run_predictive_scan")),
        calls=()),
)}


def agent(code: str | None) -> AgentSpec | None:
    """The agent for a code (case-insensitive), or None."""
    return AGENTS.get((code or "").upper())


def tool(code: str | None, name: str | None) -> ToolSpec | None:
    """The named tool of an agent, or None."""
    spec = agent(code)
    return next((t for t in spec.tools if t.name == name), None) if spec else None


def job_owner(job: str | None) -> str | None:
    """The code of the agent that owns a job, or None (district-cycle, store-digest,
    autonomy-promotion: they run in their job Lambdas)."""
    return next((a.code for a in AGENTS.values() for j in a.jobs if j.name == job), None)


def job_spec(job: str | None) -> JobSpec | None:
    """The JobSpec of an agent-owned job, or None."""
    return next((j for a in AGENTS.values() for j in a.jobs if j.name == job), None)


def job_names() -> tuple[str, ...]:
    """Every agent-owned job, in registry order."""
    return tuple(j.name for a in AGENTS.values() for j in a.jobs)


def runtime_names(stem_us: str = STEM_US, env: str = ENV) -> list[str]:
    """The runtime names, in registry order (A1, A2, A3, A4, A5, A7)."""
    return [a.runtime_name_for(stem_us, env) for a in AGENTS.values()]


def deploy_order() -> list[str]:
    """Agent codes with every callee before its caller (A2 last)."""
    done: list[str] = []
    pending = list(AGENTS)
    while pending:
        ready = [c for c in pending if all(t in done for t in AGENTS[c].calls)]
        if not ready:
            raise ValueError(f"agent calls form a cycle: {pending}")
        done.extend(ready)
        pending = [c for c in pending if c not in ready]
    return done


def parse_codes(raw: str) -> list[str]:
    """A comma list of agent codes (`"a3, A2"` -> ["A3", "A2"]) for the deployer
    CLIs. Raises ValueError naming the known codes for anything else."""
    codes = [c.strip().upper() for c in (raw or "").split(",") if c.strip()]
    unknown = [c for c in codes if c not in AGENTS]
    if not codes or unknown:
        what = f"unknown agent code(s) {', '.join(unknown)}" if unknown else "no agent code given"
        raise ValueError(f"{what}; known codes: {', '.join(AGENTS)}")
    return list(dict.fromkeys(codes))


# --- runtime identity (planner decision P2) -------------------------------------

_ACTING_AS: ContextVar[str | None] = ContextVar("retail_ops_acting_as", default=None)


def current_agent_id() -> str | None:
    """The agent this process serves: an `acting_as` override for the current
    context, else RETAIL_OPS_AGENT_ID (set on each runtime), else None (a Lambda,
    the devserver or a test)."""
    code = _ACTING_AS.get() or os.environ.get("RETAIL_OPS_AGENT_ID") or ""
    return code.strip().upper() or None


@contextmanager
def acting_as(code: str) -> Iterator[None]:
    """Serve as `code` in the current context only (used by loopback tests, so
    concurrent threads can act as different agents)."""
    token = _ACTING_AS.set(code.upper())
    try:
        yield
    finally:
        _ACTING_AS.reset(token)


# --- dotted references ------------------------------------------------------------

def resolve(ref: str) -> Any:
    """`"package.module:attr"` -> the attribute (imported at call time)."""
    module, sep, attr = ref.partition(":")
    if not sep or not module or not attr:
        raise ValueError(f"not a 'module:attr' reference: {ref!r}")
    return getattr(importlib.import_module(module), attr)


def resolve_roles(ref: str) -> frozenset[str]:
    """The role set a ToolSpec.roles reference names: the same object the route uses."""
    roles = resolve(ref)
    if isinstance(roles, frozenset):
        return roles
    if isinstance(roles, (set, list, tuple)):
        return frozenset(roles)
    raise TypeError(f"{ref} is not a role set")


# --- arguments and job payloads ----------------------------------------------------

def _check_arg(name: str, spec: ArgSpec, present: bool, value: Any) -> Any:
    if not present:
        if spec.required:
            raise ArgError(name, "is required")
        return spec.default
    if value is None:
        if spec.nullable:
            return spec.default
        raise ArgError(name, "must not be null")
    if spec.kind is bool:
        if type(value) is not bool:
            raise ArgError(name, "must be a boolean")
        return value
    if spec.kind is int:
        if type(value) is not int:          # bool is a subclass of int; reject it
            raise ArgError(name, "must be an integer")
        if spec.minimum is not None and value < spec.minimum:
            raise ArgError(name, f"must be >= {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ArgError(name, f"must be <= {spec.maximum}")
        return value
    if spec.kind is str:
        if type(value) is not str:
            raise ArgError(name, "must be a string")
        if spec.pattern is not None:
            if not re.fullmatch(spec.pattern, value):
                raise ArgError(name, "has an invalid format")
        elif spec.max_length is not None and len(value) > spec.max_length:
            raise ArgError(name, f"must be at most {spec.max_length} characters")
        return value
    raise ArgError(name, f"has an unsupported declared type {spec.kind.__name__}")


def _validate(declared: dict[str, ArgSpec], given: Any, what: str) -> dict[str, Any]:
    if given is None:
        given = {}
    if not isinstance(given, dict):
        raise ArgError(what, "must be an object")
    for name in given:
        if name not in declared:
            raise ArgError(str(name)[:64], "is not a declared argument")
    return {name: _check_arg(name, spec, name in given, given.get(name)) for name, spec in declared.items()}


def validate_args(tool_spec: ToolSpec, args: Any) -> dict[str, Any]:
    """Every declared argument of a tool, checked, with defaults applied.
    Raises ArgError naming the field for an undeclared name or a bad value."""
    return _validate(tool_spec.args, args, "args")


# Job payload rules, one implementation for both sides: job_handler validates
# before choosing remote or in-process, the runtime dispatcher validates again.
JOB_ARGS = {
    "market": ArgSpec(str, required=False, pattern=r"^[a-z]{2}$", default="us", nullable=True),
    "limit": ArgSpec(int, required=False, minimum=0, default=None, nullable=True),
}
PASSTHROUGH_ARG = ArgSpec(str, required=False, pattern=ID_PATTERN, default=None, nullable=True)


def validate_job_payload(payload: Any, passthrough: tuple[str, ...] = ()) -> dict[str, Any]:
    """Exactly `market`, `limit` and the declared passthrough keys, with defaults
    applied (`market` "us", `limit` None, passthrough None). Raises ArgError
    naming the field for an undeclared key or a value that fails its ArgSpec."""
    declared = {**JOB_ARGS, **{k: PASSTHROUGH_ARG for k in passthrough}}
    return _validate(declared, payload, "payload")


# --- claims on the wire (design §5 claims schema) -----------------------------------

_USER_ID = re.compile(r"^[A-Za-z0-9_.:@#-]{1,128}$")
_ROLE = re.compile(r"^[a-z_]{1,64}$")
_GROUP = re.compile(r"^[a-z0-9_.:-]{1,64}$")
_MARKET_RE = re.compile(r"^[a-z]{2}$")
_ID = re.compile(ID_PATTERN)
_MAX_GROUPS = 50
_MAX_DEALERS = 500
CLAIM_KEYS = ("user_id", "role", "groups", "extra_screens", "market_id", "scope_node_id", "dealer_ids",
              "is_oem", "customer_id", "impersonated_by", "impersonating_role")


def serialize_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """Claims as JSON-safe values: sets become sorted lists, nothing else changes."""
    return {k: (sorted(v) if isinstance(v, (set, frozenset)) else v) for k, v in claims.items()}


def _optional_str(raw: dict[str, Any], key: str, pattern: re.Pattern[str]) -> Any:
    value = raw[key]
    if value is None or value == "":
        return value                       # None and "" pass through unchanged
    if type(value) is not str or not pattern.fullmatch(value):
        raise ClaimsError(key)
    return value


def _str_list(raw: dict[str, Any], key: str) -> Any:
    value = raw[key]
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_GROUPS:
        raise ClaimsError(key, f"must be a list of at most {_MAX_GROUPS} strings")
    if not all(type(v) is str and _GROUP.fullmatch(v) for v in value):
        raise ClaimsError(key)
    return list(value)


def _dealer_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        items = [d.strip() for d in value.split(",") if d.strip()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        raise ClaimsError("dealer_ids", "must be a list or a comma-separated string")
    if len(items) > _MAX_DEALERS:
        raise ClaimsError("dealer_ids", f"must hold at most {_MAX_DEALERS} ids")
    if not all(type(d) is str and _ID.fullmatch(d) for d in items):
        raise ClaimsError("dealer_ids")
    return set(items)


def _is_oem(value: Any) -> Any:
    if value is None or value == "" or type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ClaimsError("is_oem", "must be a boolean")


def normalize_claims(raw: Any) -> dict[str, Any]:
    """The inverse of serialize_claims, plus type and format checks. No defaults
    are applied: a missing optional key stays missing. `user_id` and `role` are
    required. Unknown keys are dropped (logged at INFO with the key name only).
    Raises ClaimsError naming the field."""
    if not isinstance(raw, dict):
        raise ClaimsError("user_id", "is required (claims must be an object)")
    out: dict[str, Any] = {}
    for key in raw:
        if key not in CLAIM_KEYS:
            _log.info("claims: dropped unknown key %s", str(key)[:64])
    user_id, role = raw.get("user_id"), raw.get("role")
    if type(user_id) is not str or not _USER_ID.fullmatch(user_id):
        raise ClaimsError("user_id", "is required" if not user_id else "is invalid")
    if type(role) is not str or not _ROLE.fullmatch(role):
        raise ClaimsError("role", "is required" if not role else "is invalid")
    out["user_id"], out["role"] = user_id, role
    for key in ("groups", "extra_screens"):
        if key in raw:
            out[key] = _str_list(raw, key)
    if "market_id" in raw:
        out["market_id"] = _optional_str(raw, "market_id", _MARKET_RE)
    if "scope_node_id" in raw:
        out["scope_node_id"] = _optional_str(raw, "scope_node_id", _ID)
    if "dealer_ids" in raw:
        out["dealer_ids"] = _dealer_ids(raw["dealer_ids"])
    if "is_oem" in raw:
        out["is_oem"] = _is_oem(raw["is_oem"])
    if "customer_id" in raw:
        out["customer_id"] = _optional_str(raw, "customer_id", _ID)
    if "impersonated_by" in raw:
        out["impersonated_by"] = _optional_str(raw, "impersonated_by", _USER_ID)
    if "impersonating_role" in raw:
        out["impersonating_role"] = _optional_str(raw, "impersonating_role", _ROLE)
    return out


# --- CLI ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m app.agentcore.registry",
                                     description="Agent registry for the per-agent AgentCore runtimes.")
    parser.add_argument("--names", action="store_true", help="print the runtime names, one per line")
    args = parser.parse_args(argv)
    if not args.names:
        parser.error("nothing to do; pass --names")
    for name in runtime_names():
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
