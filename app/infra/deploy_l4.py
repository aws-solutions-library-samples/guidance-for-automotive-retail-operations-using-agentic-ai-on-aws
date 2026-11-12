#!/usr/bin/env python3
"""L4 deploy via boto3 (no SAM/AWS CLI needed).

Creates the access-layer stack for the journeys service (names from
app/infra/naming.py; `<env>` is RETAIL_OPS_ENV, default dev):
  * IAM roles    per-function least-privilege roles (app/infra/iam_roles.py)
  * Lambda       retail-ops-<env>-journeys-authorizer  (python3.12)
  * Lambda       retail-ops-<env>-journeys-service     (python3.12)
  * REST API     retail-ops-<env>-journeys-api  with a TOKEN authorizer and routes
                 GET  /journeys/board
                 GET  /journeys/{id}/assist
                 POST /journeys/{id}/utterance
                 deployed to stage `dev`.

Idempotent: updates code/role/api in place if they already exist.
Dry-run by default; pass --apply to create/update. --delete tears the stack down.
ENABLE_QUICK on the service Lambda is "true" when the account has an Amazon Quick
subscription (DescribeAccountSubscription); ENABLE_QUICK=true|false in the deploy
environment (deploy/config.env) overrides the detection.
RETAIL_OPS_QUICK_EMBED_DOMAINS on the service Lambda is the console URL from
console-config.json (override it in the deploy environment); the embed route passes
it as AllowedDomains, so the console embeds Quick dashboards without the Quick
admin's domain allowlist.
RETAIL_OPS_CONSOLE_ORIGIN on the service Lambda is the same console URL (override
it in the deploy environment): the origin the API answers CORS for, never `*`.
`--disable-agents A2,A5|all` / `--enable-agents A2|all` (dry run unless --apply)
change only RETAIL_OPS_AGENT_RUNTIME_DISABLE on the service Lambda: the merge-safe
rollback switch that sends those agents' calls back in-process (ADR 0009).
`--dev-token off|on` (dry run unless --apply) changes only
RETAIL_OPS_ALLOW_DEV_TOKEN on the authorizer (and the floor-stream Lambda when it
exists): `off` refuses the plain `user:<id>` / `cust:<id>` development tokens, so
only Cognito sign-in works. Later l4 deploys keep the setting.

The Lambda handlers are the SAME modules the dev server uses
(app.services.api.authorizer / app.services.api.handler), so deployed behavior
matches local.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

try:
    from app.agentcore import registry
    from app.infra import iam_roles, naming
    from app.services.api import cors
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.agentcore import registry
    from app.infra import iam_roles, naming
    from app.services.api import cors

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
ENV_PREFIX = naming.PREFIX                       # retail-ops-<env>-
ROLE_NAME = naming.name("journeys-role")  # legacy shared role (kept only for teardown)
AUTH_FN = naming.name("journeys-authorizer")
SVC_FN = naming.name("journeys-service")
API_NAME = naming.name("journeys-api")
# The API stage keeps the name `dev` in every environment: it lives inside the
# env-named REST API, so it cannot collide, and every script builds the API base
# URL as https://<api-id>.execute-api.<region>.amazonaws.com/dev.
STAGE = "dev"
JWT_LAYER = naming.name("jwt")
RUNTIME = "python3.12"
DEFAULT_MEMORY_MB = 512
# Lambda allocates CPU in proportion to memory; 1769 MB is one full vCPU. The
# service Lambda's read models (Store Cockpit across every store, OEM views) are
# bound by deserialising scanned DynamoDB items, which took ~7 s at 512 MB.
SVC_MEMORY_MB = 1769
BEDROCK_MODEL = "us.anthropic.claude-opus-4-5-20251101-v1:0"


def _zip_code() -> bytes:
    """Zip the app/ package (source only; Lambda provides boto3)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for py in (ROOT / "app").rglob("*.py"):
            # skip local-only entrypoints, deployers, and caches
            if "__pycache__" in py.parts or py.name in (
                "devserver.py", "deploy_l4.py", "deploy_l6.py", "deploy_events.py",
                "deploy_approvals.py", "ddb_load.py", "cognito_setup.py", "iam_roles.py",
                "deploy_gateway.py", "deploy_gateway_quick.py", "deploy_lake.py",
                "deploy_quick.py", "deploy_agentcore.py", "deploy_console.py",
                "publish_jwt_layer.py", "deploy_streaming.py",
            ):
                continue
            z.write(py, str(py.relative_to(ROOT)))
    return buf.getvalue()


# --- roles (per-function, least-privilege; see app/infra/iam_roles.py) -------

def ensure_roles(iam, account: str, apply: bool) -> tuple[str, str]:
    """Ensure the authorizer + service per-function roles. Returns (auth, svc)."""
    auth_arn = iam_roles.ensure_role(iam, account, iam_roles.ROLE_AUTHORIZER, apply)
    svc_arn = iam_roles.ensure_role(iam, account, iam_roles.ROLE_SERVICE, apply)
    if apply:
        print("  [role] waiting for propagation…"); time.sleep(10)
    return auth_arn, svc_arn


# --- lambdas ----------------------------------------------------------------

# Env vars this deployer does NOT own but which sibling deployers set on the
# service Lambda (deploy_approvals sets the SM ARN; the event bus toggles live
# mode). A full L4 redeploy must PRESERVE these rather than clobber them.
# ENABLE_QUICK is set by quick_env() below; it is preserved only when that
# cannot decide (the Quick subscription could not be read).
_PRESERVE_ENV_KEYS = ("RETAIL_OPS_APPROVALS_SM_ARN", "RETAIL_OPS_EVENT_BUS_LIVE", "RETAIL_OPS_EVENT_BUS",
                      "RETAIL_OPS_QUICK_EMBED_USER_ARN", "ENABLE_QUICK", "RETAIL_OPS_QUICK_PER_USER_EMBED",
                      "RETAIL_OPS_QUICK_EMBED_DOMAINS", "RETAIL_OPS_CONSOLE_ORIGIN", "RETAIL_OPS_SES_FROM",
                      "RETAIL_OPS_GATEWAY_URL", "RETAIL_OPS_GATEWAY_ARN", "RETAIL_OPS_GATEWAY_TARGET_LAMBDA",
                      # the agent runtime map survives a redeploy from a machine without
                      # agentcore-config.json, and the operator's rollback switch survives
                      # every redeploy (ADR 0009)
                      "RETAIL_OPS_AGENT_RUNTIMES", "RETAIL_OPS_AGENT_RUNTIME_DISABLE",
                      # the development-token switch (--dev-token) survives every redeploy
                      "RETAIL_OPS_ALLOW_DEV_TOKEN")

AGENTCORE_CONFIG = naming.config_path("agentcore")
DISABLE_KEY = "RETAIL_OPS_AGENT_RUNTIME_DISABLE"
_RUNTIME_ARN = re.compile(r"^arn:aws:bedrock-agentcore:[a-z0-9-]+:\d{12}:runtime/[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10}$")


class ConfigError(ValueError):
    """agentcore-config.json is malformed; the message names the field."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        super().__init__(f"agentcore-config.json: {field} {reason}")


def _checked_arn(field: str, arn: object) -> str:
    if not isinstance(arn, str) or not _RUNTIME_ARN.fullmatch(arn):
        raise ConfigError(field, "is not an AgentCore runtime ARN")
    return arn


def runtime_env_from_config(path: Path) -> dict[str, str]:
    """The runtime map for the Lambdas from agentcore-config.json (written by
    deploy_agentcore --apply): {"RETAIL_OPS_AGENT_RUNTIMES": "<compact json>"}.
    {} when the file is missing (the current map is then preserved) or names no
    agent. A v1 file (the single runtime) wires A3 only. Raises ConfigError for
    bad JSON, an unknown schema_version, an unknown agent code or a bad ARN."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    try:
        cfg = json.loads(text)
    except ValueError:
        raise ConfigError("(file)", "is not valid JSON") from None
    if not isinstance(cfg, dict):
        raise ConfigError("(file)", "is not a JSON object")
    version = cfg.get("schema_version")
    if version is None and "agent_runtime_arn" in cfg:          # v1: the single shared runtime
        arns = {"A3": _checked_arn("agent_runtime_arn", cfg["agent_runtime_arn"])}
    elif version == 2:
        agents = cfg.get("agents") or {}
        if not isinstance(agents, dict):
            raise ConfigError("agents", "is not an object")
        arns = {}
        for code, entry in agents.items():
            if code not in registry.AGENTS:
                raise ConfigError(f"agents.{code}", f"is not a registry agent ({', '.join(registry.AGENTS)})")
            arn = entry.get("runtime_arn") if isinstance(entry, dict) else None
            arns[code] = _checked_arn(f"agents.{code}.runtime_arn", arn)
    else:
        raise ConfigError("schema_version", f"{version!r} is not supported (expected 2)")
    if not arns:
        return {}
    ordered = {c: arns[c] for c in registry.AGENTS if c in arns}
    return {"RETAIL_OPS_AGENT_RUNTIMES": json.dumps(ordered, separators=(",", ":"))}


QUICK_FLAG = "ENABLE_QUICK"


def quick_flag(qs, account: str, override: str | None = None) -> tuple[str | None, str]:
    """ENABLE_QUICK for the service Lambda, and why.

    The deploy environment's ENABLE_QUICK=true|false wins. Otherwise "true" when
    the account has an active Amazon Quick subscription and "false" when it has
    none. None (keep the current value) when the subscription cannot be read.
    The flag turns on read calls only (ListFlows, ListDashboards, the embed URL
    for the shared embed user); per-user embedding stays behind its own
    RETAIL_OPS_QUICK_PER_USER_EMBED switch, which this deployer never sets."""
    if override is not None and override.strip():
        value = override.strip().lower()
        if value not in ("true", "false"):
            raise SystemExit(f"  [quick] {QUICK_FLAG}={override!r} in the deploy environment: use true or false")
        return value, "deploy environment override"
    try:
        info = qs.describe_account_subscription(AwsAccountId=account).get("AccountInfo") or {}
    except qs.exceptions.ResourceNotFoundException:
        return "false", "no Amazon Quick subscription in this account"
    except Exception as e:  # noqa: BLE001 - undecided: keep whatever the Lambda has
        return None, f"could not read the Amazon Quick subscription ({type(e).__name__})"
    status = info.get("AccountSubscriptionStatus")
    if status == "ACCOUNT_CREATED":
        return "true", f"Amazon Quick {info.get('Edition') or 'subscription'} {status}"
    return "false", f"Amazon Quick subscription status {status or 'unknown'}"


def quick_env(qs, account: str, override: str | None = None) -> dict[str, str]:
    """{"ENABLE_QUICK": value} for the service Lambda, or {} to keep the current value."""
    value, why = quick_flag(qs, account, override)
    if value is None:
        print(f"  [quick] {QUICK_FLAG} unchanged on {SVC_FN}: {why}; set {QUICK_FLAG}=true|false to decide")
        return {}
    print(f"  [quick] {QUICK_FLAG}={value} on {SVC_FN} ({why})")
    return {QUICK_FLAG: value}


EMBED_DOMAINS = "RETAIL_OPS_QUICK_EMBED_DOMAINS"
CONSOLE_CONFIG = naming.config_path("console")


def embed_domains_env(override: str | None = None, console_config: Path = CONSOLE_CONFIG) -> dict[str, str]:
    """{"RETAIL_OPS_QUICK_EMBED_DOMAINS": "<origin>"} for the service Lambda: the
    origins a Quick embed URL may load in (GenerateEmbedUrlForRegisteredUser
    AllowedDomains, so the Quick admin's domain allowlist is not needed).

    RETAIL_OPS_QUICK_EMBED_DOMAINS in the deploy environment wins (comma-separated
    https origins). Otherwise the console's URL from console-config.json (written by
    the console stage). {} keeps the current value: on a fresh account the console
    stage runs after l4, so re-run l4 once after the first full deploy."""
    if override is not None and override.strip():
        domains = [d.strip().rstrip("/") for d in override.split(",") if d.strip()]
        bad = [d for d in domains if not d.startswith("https://")]
        if bad:
            raise SystemExit(f"  [quick] {EMBED_DOMAINS}: use https origins ({', '.join(bad)})")
        value, why = ",".join(domains[:3]), "deploy environment override"
    else:
        try:
            url = (json.loads(console_config.read_text()).get("url") or "").rstrip("/")
        except (FileNotFoundError, ValueError):
            url = ""
        if not url.startswith("https://"):
            print(f"  [quick] {EMBED_DOMAINS} unchanged on {SVC_FN}: no console URL yet "
                  "(re-run l4 after the console stage)")
            return {}
        value, why = url, "console-config.json"
    print(f"  [quick] {EMBED_DOMAINS}={value} on {SVC_FN} ({why})")
    return {EMBED_DOMAINS: value}


CONSOLE_ORIGIN = "RETAIL_OPS_CONSOLE_ORIGIN"


def console_origin_env(override: str | None = None, console_config: Path = CONSOLE_CONFIG) -> dict[str, str]:
    """{"RETAIL_OPS_CONSOLE_ORIGIN": "<origin>"} for the service Lambda: the origin(s)
    the API answers CORS for (app.services.api.handler; never `*`, the API carries
    a bearer token). RETAIL_OPS_CONSOLE_ORIGIN in the deploy environment wins
    (comma-separated https origins). Otherwise the console's URL from
    console-config.json. {} keeps the current value: on a fresh account the console
    stage runs after l4, so re-run l4 once after the first full deploy; until then
    only the local dev console (localhost:3100) is allowed."""
    if override is not None and override.strip():
        origins = [o.strip().rstrip("/") for o in override.split(",") if o.strip()]
        bad = [o for o in origins if not o.startswith("https://")]
        if bad:
            raise SystemExit(f"  [cors] {CONSOLE_ORIGIN}: use https origins ({', '.join(bad)})")
        value, why = ",".join(origins), "deploy environment override"
    else:
        try:
            url = (json.loads(console_config.read_text()).get("url") or "").rstrip("/")
        except (FileNotFoundError, ValueError):
            url = ""
        if not url.startswith("https://"):
            print(f"  [cors] {CONSOLE_ORIGIN} unchanged on {SVC_FN}: no console URL yet "
                  "(re-run l4 after the console stage)")
            return {}
        value, why = url, "console-config.json"
    print(f"  [cors] {CONSOLE_ORIGIN}={value} on {SVC_FN} ({why})")
    return {CONSOLE_ORIGIN: value}


# Replies API Gateway builds itself never reach the service Lambda, so they skip
# app/services/api/cors.py: the TOKEN authorizer's 401 / 403, a missing-route or
# missing-token 403, throttling (429) and gateway 5xx. Without CORS headers the
# browser hides them as "Failed to fetch" even for a correctly signed-in failure.
# DEFAULT_4XX / DEFAULT_5XX cover every such response type.
CORS_GATEWAY_RESPONSES = ("DEFAULT_4XX", "DEFAULT_5XX")


def gateway_cors_origin(svc_env: dict[str, str], current: str | None = None) -> str:
    """The one Access-Control-Allow-Origin a gateway response can carry (a static
    value; it cannot echo the request). Mirrors cors.cors_origin() for a request
    it cannot match: the first allowed origin — the console origin this deploy sets,
    else the one already on the service Lambda, else the local dev console."""
    for raw in (svc_env.get(CONSOLE_ORIGIN), current):
        origins = [o.strip().rstrip("/") for o in (raw or "").split(",") if o.strip()]
        if origins:
            return origins[0]
    return cors.LOCAL_ORIGINS[0]


def gateway_cors_parameters(origin: str) -> dict[str, str]:
    """responseParameters for a gateway response: the same headers cors.headers()
    puts on every Lambda reply (static values are single-quoted)."""
    if "'" in origin or not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise SystemExit(f"  [cors] refusing gateway-response origin {origin!r}")
    values = {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
              "Access-Control-Allow-Headers": cors.ALLOW_HEADERS,
              "Access-Control-Allow-Methods": cors.ALLOW_METHODS,
              "Access-Control-Expose-Headers": cors.EXPOSE_HEADERS}
    return {f"gatewayresponse.header.{k}": f"'{v}'" for k, v in values.items()}


def ensure_gateway_cors(agw, api_id: str, origin: str) -> None:
    """Put the CORS headers on API Gateway's own 4XX / 5XX replies (idempotent:
    put_gateway_response replaces). Takes effect with the next stage deployment."""
    params = gateway_cors_parameters(origin)
    for rtype in CORS_GATEWAY_RESPONSES:
        agw.put_gateway_response(restApiId=api_id, responseType=rtype, responseParameters=params)
    print(f"  [cors] gateway responses {', '.join(CORS_GATEWAY_RESPONSES)} allow {origin}")


SES_FROM = "RETAIL_OPS_SES_FROM"


def ses_from_env(value: str | None = None) -> dict[str, str]:
    """{"RETAIL_OPS_SES_FROM": "<verified SES identity>"} from the deploy
    environment, or {} when it names none (outreach then records the missing
    sender on each email and falls back to a dealer task; there is no default
    sender). Shared by the l4, l6, streaming and agentcore deployers."""
    src = (os.environ.get(SES_FROM) if value is None else value) or ""
    src = src.strip()
    return {SES_FROM: src} if src else {}


def merge_preserved(current: dict, new: dict, keys: tuple[str, ...]) -> dict:
    """`new`, plus each of `keys` that the current environment has and `new` lacks."""
    merged = dict(new)
    for k in keys:
        if k in current and k not in merged:
            merged[k] = current[k]
    return merged


def parse_agent_list(raw: str) -> list[str] | str:
    """`--disable-agents` / `--enable-agents` value: "all" or a comma list of codes."""
    return "all" if raw.strip().lower() == "all" else registry.parse_codes(raw)


def toggle_disable(current: str | None, disable: list[str] | str | None = None,
                   enable: list[str] | str | None = None) -> str | None:
    """The new RETAIL_OPS_AGENT_RUNTIME_DISABLE value: "1" (every agent
    in-process), a CSV of codes, or None (remove the key)."""
    raw = (current or "").strip()
    off = set(registry.AGENTS) if raw == "1" else {c.strip().upper() for c in raw.split(",") if c.strip()}
    if disable == "all":
        off = set(registry.AGENTS)
    elif disable:
        off |= set(disable)
    if enable == "all":
        off = set()
    elif enable:
        off -= set(enable)
    if not off:
        return None
    if off >= set(registry.AGENTS):
        return "1"
    return ",".join(c for c in [*registry.AGENTS, *sorted(off - set(registry.AGENTS))] if c in off)


def _a2_note(codes: list[str] | str) -> str | None:
    """The rollback note for Sales Package specialists (the A2 runtime keeps its own map)."""
    if codes == "all":
        return None
    named = [registry.AGENTS[c].display for c in codes if c in registry.AGENTS["A2"].calls]
    if not named or "A2" in codes:
        return None
    return (f"  note: the Journey Agent runtime still calls the {', '.join(named)} for the Sales Package; "
            "disable A2 to run the whole package in-process")


def set_disable(lam, fn_name: str, *, disable: list[str] | str | None = None,
                enable: list[str] | str | None = None, apply: bool = False) -> tuple[str | None, str | None]:
    """Change only RETAIL_OPS_AGENT_RUNTIME_DISABLE on one Lambda: read the current
    environment, update that key, write the whole merged environment back in one
    update_function_configuration (dry run unless apply). Returns (before, after)."""
    cur = (lam.get_function_configuration(FunctionName=fn_name).get("Environment") or {}).get("Variables") or {}
    before = cur.get(DISABLE_KEY)
    after = toggle_disable(before, disable, enable)
    state = "unchanged" if after == before else ("set" if apply else "would set")
    print(f"  [disable] {fn_name}: {DISABLE_KEY} {before or '(unset)'} -> {after or '(unset)'} ({state})")
    if apply and after != before:
        variables = {k: v for k, v in cur.items() if k != DISABLE_KEY}
        if after is not None:
            variables[DISABLE_KEY] = after
        lam.update_function_configuration(FunctionName=fn_name, Environment={"Variables": variables})
        _wait_updated(lam, fn_name)
    return before, after


DEV_TOKEN_KEY = "RETAIL_OPS_ALLOW_DEV_TOKEN"  # noqa: S105 - an environment variable name, not a credential
STREAM_FN = naming.name("floor-stream")     # verifies tokens in-process (deploy_streaming)


def set_dev_token(lam, functions: list[str], enabled: bool, *, apply: bool = False) -> int:
    """Change only RETAIL_OPS_ALLOW_DEV_TOKEN ("1" accepts the plain development
    tokens, "0" refuses them) on each named Lambda that exists, writing the whole
    merged environment back (dry run unless apply). Returns 0, or 1 when the
    authorizer is missing."""
    value = "1" if enabled else "0"
    for fn in functions:
        try:
            cur = (lam.get_function_configuration(FunctionName=fn).get("Environment") or {}).get("Variables") or {}
        except lam.exceptions.ResourceNotFoundException:
            if fn == AUTH_FN:
                print(f"  [dev-token] {fn} not found: deploy the l4 stage first")
                return 1
            continue
        before = cur.get(DEV_TOKEN_KEY)
        state = "unchanged" if before == value else ("set" if apply else "would set")
        print(f"  [dev-token] {fn}: {DEV_TOKEN_KEY} {before or '(unset: on)'} -> {value} ({state})")
        if apply and before != value:
            lam.update_function_configuration(FunctionName=fn, Environment={"Variables": {**cur, DEV_TOKEN_KEY: value}})
            _wait_updated(lam, fn)
    if not apply:
        print("  (dry run: re-run with --apply to change the Lambda configuration)")
    return 0


def _jwt_layer_arn(lam, account: str) -> str | None:
    """Latest published version of the retail-ops-<env>-jwt layer (PyJWT+cryptography),
    or None if it hasn't been published yet."""
    try:
        vers = lam.list_layer_versions(LayerName=JWT_LAYER, MaxItems=1).get("LayerVersions", [])
        return vers[0]["LayerVersionArn"] if vers else None
    except Exception:
        return None


def ensure_function(lam, name: str, handler: str, role_arn: str, code: bytes,
                    env: dict, apply: bool, layers: list[str] | None = None,
                    memory_mb: int = DEFAULT_MEMORY_MB) -> str:
    layers = layers or []
    try:
        lam.get_function(FunctionName=name); exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False
    if not apply:
        print(f"  [lambda] {'would update' if exists else 'would create'} {name} ({handler})"
              + (f" +layer" if layers else ""))
        return f"(arn pending {name})"
    if exists:
        # Preserve env vars owned by sibling deployers (e.g. the approvals SM ARN)
        # so redeploying L4 doesn't silently disable those integrations.
        cur = (lam.get_function(FunctionName=name)["Configuration"].get("Environment") or {}).get("Variables", {})
        merged = merge_preserved(cur, env, _PRESERVE_ENV_KEYS)
        for k in sorted(set(merged) - set(env)):
            print(f"  [lambda] preserving {k} on {name}")
        lam.update_function_code(FunctionName=name, ZipFile=code)
        _wait_updated(lam, name)
        lam.update_function_configuration(FunctionName=name, Handler=handler, Role=role_arn,
                                          Runtime=RUNTIME, Timeout=29, MemorySize=memory_mb,
                                          Environment={"Variables": merged}, Layers=layers)
        _wait_updated(lam, name)
        print(f"  [lambda] updated {name}" + (" (+jwt layer)" if layers else ""))
    else:
        for attempt in range(6):
            try:
                lam.create_function(FunctionName=name, Runtime=RUNTIME, Role=role_arn,
                                    Handler=handler, Code={"ZipFile": code}, Timeout=29,
                                    MemorySize=memory_mb, Environment={"Variables": env}, Layers=layers)
                break
            except lam.exceptions.ClientError as e:
                if "cannot be assumed" in str(e) or "role" in str(e).lower():
                    time.sleep(5); continue
                raise
        _wait_updated(lam, name)
        print(f"  [lambda] created {name}" + (" (+jwt layer)" if layers else ""))
    from app.infra import freshness
    freshness.stamp_lambda(lam, name, code)   # verify.sh freshness: which source this Lambda runs
    return lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]


def _wait_updated(lam, name: str) -> None:
    for _ in range(30):
        st = lam.get_function(FunctionName=name)["Configuration"].get("LastUpdateStatus")
        if st in (None, "Successful"):
            return
        if st == "Failed":
            raise RuntimeError(f"{name} update failed")
        time.sleep(2)


# --- rest api ---------------------------------------------------------------

def _clear_api(agw, api_id: str) -> None:
    """Strip an existing REST API back to a bare root so it can be rebuilt under
    the same id. Deletes every child of `/` (which cascades to nested resources
    and their methods) plus the authorizers, whose names would otherwise clash."""
    children = [r for r in agw.get_resources(restApiId=api_id, limit=500)["items"]
                if r["path"] != "/" and r.get("parentId")]
    # shallowest first: deleting a parent removes its subtree, so skip the rest
    for r in sorted(children, key=lambda x: x["path"].count("/")):
        try:
            agw.delete_resource(restApiId=api_id, resourceId=r["id"])
        except agw.exceptions.NotFoundException:
            pass  # already removed with its parent's subtree
    for a in agw.get_authorizers(restApiId=api_id).get("items", []):
        agw.delete_authorizer(restApiId=api_id, authorizerId=a["id"])
    # also drop methods hung directly off the root (OPTIONS from a prior deploy)
    root = next(r for r in agw.get_resources(restApiId=api_id)["items"] if r["path"] == "/")
    for method in (root.get("resourceMethods") or {}):
        agw.delete_method(restApiId=api_id, resourceId=root["id"], httpMethod=method)


def ensure_api(agw, lam, account: str, auth_arn: str, svc_arn: str, apply: bool,
               cors_origin: str | None = None) -> str:
    apis = [a for a in agw.get_rest_apis().get("items", []) if a["name"] == API_NAME]
    if not apply:
        print(f"  [api] {'exists' if apis else 'would create'} {API_NAME}")
        if cors_origin:
            print(f"  [cors] would set gateway responses {', '.join(CORS_GATEWAY_RESPONSES)} to allow {cors_origin}")
        return "(api pending)"
    if apis:
        # Reuse the REST API id so the invoke URL is stable across deploys: the
        # console bakes that base at build time and the demo links are written
        # down, so churning the id would invalidate both on every --apply. Only
        # the definition under it (resources + authorizer) is rebuilt.
        api_id = apis[0]["id"]
        print(f"  [api] reusing {API_NAME} ({api_id}) — rebuilding its definition in place")
        _clear_api(agw, api_id)
    else:
        api_id = agw.create_rest_api(name=API_NAME, description=f"Retail Ops {naming.ENV} journeys access layer",
                                     endpointConfiguration={"types": ["REGIONAL"]})["id"]
    root = next(r["id"] for r in agw.get_resources(restApiId=api_id)["items"] if r["path"] == "/")

    # authorizer (TOKEN, Authorization header)
    auth_uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{auth_arn}/invocations"
    authorizer_id = agw.create_authorizer(
        restApiId=api_id, name="OrgScopeAuthorizer", type="TOKEN",
        authorizerUri=auth_uri, identitySource="method.request.header.Authorization",
        authorizerResultTtlInSeconds=300)["id"]

    svc_uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{svc_arn}/invocations"

    created: list[str] = []

    def resource(parent, part):
        rid = agw.create_resource(restApiId=api_id, parentId=parent, pathPart=part)["id"]
        created.append(rid)
        return rid

    def proxy_method(res_id, http_method, authorized=True):
        kwargs = dict(restApiId=api_id, resourceId=res_id, httpMethod=http_method,
                      authorizationType="CUSTOM" if authorized else "NONE")
        if authorized:
            kwargs["authorizerId"] = authorizer_id
        agw.put_method(**kwargs)
        agw.put_integration(restApiId=api_id, resourceId=res_id, httpMethod=http_method,
                            type="AWS_PROXY", integrationHttpMethod="POST", uri=svc_uri)

    # /journeys
    journeys = resource(root, "journeys")
    # /journeys/board  (GET)
    board = resource(journeys, "board")
    proxy_method(board, "GET")
    # /journeys/{id}
    jid = resource(journeys, "{id}")
    # /journeys/{id}/assist (GET)
    assist = resource(jid, "assist")
    proxy_method(assist, "GET")
    # /journeys/{id}/utterance (POST)
    utt = resource(jid, "utterance")
    proxy_method(utt, "POST")
    # /journeys/{id}/equity (GET — A4 trade equity)
    eq = resource(jid, "equity")
    proxy_method(eq, "GET")
    # /journeys/{id}/revalue (POST — A4 trade refresh on mileage/condition update)
    revalue = resource(jid, "revalue")
    proxy_method(revalue, "POST")
    # /journeys/{id}/discovery (GET — A1 vehicle discovery)
    disc = resource(jid, "discovery")
    proxy_method(disc, "GET")
    # /journeys/{id}/lifestyle (GET infer / POST with intake answers — A1)
    lifestyle_res = resource(jid, "lifestyle")
    proxy_method(lifestyle_res, "GET")
    proxy_method(lifestyle_res, "POST")
    # /journeys/{id}/recommend-config (GET — A1 needs-justified trim+packages)
    recfg = resource(jid, "recommend-config")
    proxy_method(recfg, "GET")
    # /journeys/{id}/suggestions (GET — A3 contextual suggestions)
    sugg = resource(jid, "suggestions")
    proxy_method(sugg, "GET")
    # /journeys/{id}/price-build (GET — A1 market-correct price/incentive/monthly)
    pricebuild = resource(jid, "price-build")
    proxy_method(pricebuild, "GET")
    # /journeys/{id}/inventory (GET — A1 VIN-level availability + near-match + ETA)
    inv_res = resource(jid, "inventory")
    proxy_method(inv_res, "GET")
    # /journeys/{id}/deal (GET — A3 F&I deal desk + credit-compliance gate)
    deal_res = resource(jid, "deal")
    proxy_method(deal_res, "GET")
    # /journeys/{id}/competitive-intel (GET — A3 in-catalog cross-shop)
    compintel = resource(jid, "competitive-intel")
    proxy_method(compintel, "GET")
    # /journeys/{id}/incentives (GET — A3 active incentives for model/market)
    incentives_res = resource(jid, "incentives")
    proxy_method(incentives_res, "GET")
    # /journeys/{id}/cross-sell (GET — A3 F&I attach recommendations)
    crosssell = resource(jid, "cross-sell")
    proxy_method(crosssell, "GET")
    # /journeys/{id}/prequalify (POST — F&I soft prequal + records compliance)
    prequal = resource(jid, "prequalify")
    proxy_method(prequal, "POST")
    # /journeys/{id}/state (GET — A2 supervisor view: stepper + handoff + NBA)
    state = resource(jid, "state")
    proxy_method(state, "GET")
    # /journeys/{id}/advance (POST — A2 guarded stage transition)
    advance = resource(jid, "advance")
    proxy_method(advance, "POST")
    # /journeys/{id}/nurture (GET — A2 nurture read model)
    nurture_res = resource(jid, "nurture")
    proxy_method(nurture_res, "GET")
    # /journeys/{id}/nurture/plan (POST — A2 build/refresh NurturePlan)
    nurture_plan = resource(nurture_res, "plan")
    proxy_method(nurture_plan, "POST")
    # /journeys/{id}/chat (POST — A2 grounded customer Q&A; customer or staff)
    chat = resource(jid, "chat")
    proxy_method(chat, "POST")
    # /journeys/{id}/nudge (GET — A2 smallest-next-commitment)
    nudge_res = resource(jid, "nudge")
    proxy_method(nudge_res, "GET")
    # /journeys/{id}/nba (POST — A2 compute+log next-best-action)
    nba_res = resource(jid, "nba")
    proxy_method(nba_res, "POST")
    # /journeys/{id}/book-test-drive (POST — A2 appointment + visit_booked)
    booktd = resource(jid, "book-test-drive")
    proxy_method(booktd, "POST")
    # /journeys/{id}/intent-shift (GET — A2 detect_intent_shift)
    intent_res = resource(jid, "intent-shift")
    proxy_method(intent_res, "GET")
    # /journeys/{id}/package (GET poll / POST build — A2 Sales Package aggregation)
    pkg_res = resource(jid, "package")
    proxy_method(pkg_res, "GET")
    proxy_method(pkg_res, "POST")
    # /journeys/{id}/task (POST — A2 create_task / dealer alert)
    task_res = resource(jid, "task")
    proxy_method(task_res, "POST")
    # /journeys/{id}/run-play (POST — A2 Plays engine, AR-10)
    runplay_res = resource(jid, "run-play")
    proxy_method(runplay_res, "POST")

    # /garage (GET — Customer Web self-service) + /garage/{id} (GET — staff mirror)
    garage = resource(root, "garage")
    proxy_method(garage, "GET")
    garage_id = resource(garage, "{id}")
    proxy_method(garage_id, "GET")

    # /dealer/cockpit (GET — Store Cockpit, dealer principal dashboard)
    dealer_res = resource(root, "dealer")
    cockpit_res = resource(dealer_res, "cockpit")
    proxy_method(cockpit_res, "GET")
    # /dealer/delivery (GET board) + /dealer/delivery/{id}/deliver (POST mark_delivered)
    delivery_res = resource(dealer_res, "delivery")
    proxy_method(delivery_res, "GET")
    delivery_id_res = resource(delivery_res, "{id}")
    deliver_action = resource(delivery_id_res, "deliver")
    proxy_method(deliver_action, "POST")
    # /dealer/leads (GET Lead Desk) + /dealer/leads/{id}/assign (POST assign_lead)
    leads_res = resource(dealer_res, "leads")
    proxy_method(leads_res, "GET")
    leads_id_res = resource(leads_res, "{id}")
    lead_assign = resource(leads_id_res, "assign")
    proxy_method(lead_assign, "POST")

    # /admin/autonomy (GET list + POST change — Admin Console)
    admin = resource(root, "admin")
    admin_auto = resource(admin, "autonomy")
    proxy_method(admin_auto, "GET")
    proxy_method(admin_auto, "POST")

    # /oem/today (GET — A6 OEM Workspace) + /oem/campaigns (GET — A5 Marketing Studio)
    oem = resource(root, "oem")
    oem_today_res = resource(oem, "today")
    proxy_method(oem_today_res, "GET")
    oem_campaigns_res = resource(oem, "campaigns")
    proxy_method(oem_campaigns_res, "GET")
    # /oem/campaigns/budget-shift (POST — A5 recommend_budget_shift)
    oem_budgetshift = resource(oem_campaigns_res, "budget-shift")
    proxy_method(oem_budgetshift, "POST")
    # /oem/alerts (GET — A6 AI alerts feed, district-scoped)
    oem_alerts_res = resource(oem, "alerts")
    proxy_method(oem_alerts_res, "GET")
    # /oem/accounts/{id}/draft-plan (POST — A6 draft_account_plan)
    oem_accounts = resource(oem, "accounts")
    oem_account_id = resource(oem_accounts, "{id}")
    oem_draftplan = resource(oem_account_id, "draft-plan")
    proxy_method(oem_draftplan, "POST")
    # /oem/guardrails (GET — A6 rule table + blocks-this-week) and
    # /oem/guardrails/check (POST — GR-001..GR-008 check; Admin demo, acceptance 15b)
    oem_guardrails_res = resource(oem, "guardrails")
    proxy_method(oem_guardrails_res, "GET")
    oem_guardrails_check = resource(oem_guardrails_res, "check")
    proxy_method(oem_guardrails_check, "POST")
    # Tier A: A6 planner suite
    # /oem/accounts/{id}/composite|ev-readiness|compliance (GET)
    oem_acct_composite = resource(oem_account_id, "composite"); proxy_method(oem_acct_composite, "GET")
    oem_acct_evready = resource(oem_account_id, "ev-readiness"); proxy_method(oem_acct_evready, "GET")
    oem_acct_compliance = resource(oem_account_id, "compliance"); proxy_method(oem_acct_compliance, "GET")
    # /oem/scenario (POST), /oem/weekend (GET)
    oem_scenario = resource(oem, "scenario"); proxy_method(oem_scenario, "POST")
    oem_weekend = resource(oem, "weekend"); proxy_method(oem_weekend, "GET")

    # /service  (A7 service journey)
    service = resource(root, "service")
    # /service/board (GET)
    svc_board_res = resource(service, "board")
    proxy_method(svc_board_res, "GET")
    # /service/book (POST — A7 book_service)
    svc_book_res = resource(service, "book")
    proxy_method(svc_book_res, "POST")
    # /service/predict/{vin} (GET — A7 predict_next_service)
    svc_predict = resource(service, "predict")
    svc_predict_vin = resource(svc_predict, "{vin}")
    proxy_method(svc_predict_vin, "GET")
    # /service/{id} (GET — RO detail)
    svc_id = resource(service, "{id}")
    proxy_method(svc_id, "GET")
    # /service/{id}/state (GET — A7 supervisor view)
    svc_state = resource(svc_id, "state")
    proxy_method(svc_state, "GET")
    # /service/{id}/advance (POST — A7 guarded stage transition)
    svc_adv = resource(svc_id, "advance")
    proxy_method(svc_adv, "POST")
    # /service/{id}/approve (POST — A7 record estimate decision; staff or customer)
    svc_appr = resource(svc_id, "approve")
    proxy_method(svc_appr, "POST")
    # /service/{id}/handoff-to-sales (POST — A7->A2 repurchase/upgrade handoff)
    svc_ho = resource(svc_id, "handoff-to-sales")
    proxy_method(svc_ho, "POST")
    # /service/{id}/inspection (POST — A7 record MPI findings)
    svc_insp = resource(svc_id, "inspection")
    proxy_method(svc_insp, "POST")
    # /service/{id}/findings (GET — A7 prioritized findings)
    svc_find = resource(svc_id, "findings")
    proxy_method(svc_find, "GET")
    # /service/{id}/estimate (POST — A7 build_estimate)
    svc_est = resource(svc_id, "estimate")
    proxy_method(svc_est, "POST")
    # /service/{id}/parts (GET — A7 track_parts)
    svc_parts = resource(svc_id, "parts")
    proxy_method(svc_parts, "GET")
    # /service/{id}/line-status (POST — A7 update_line_status)
    svc_line = resource(svc_id, "line-status")
    proxy_method(svc_line, "POST")
    # /service/{id}/notify-ready (POST — A7 notify_ready -> READY)
    svc_ready = resource(svc_id, "notify-ready")
    proxy_method(svc_ready, "POST")
    # /service/{id}/decline-followup (POST — A7 declined work -> A2 service nurture)
    svc_decl = resource(svc_id, "decline-followup")
    proxy_method(svc_decl, "POST")

    # /approvals/plan/{id}         (GET  — approval status)
    # /approvals/plan/{id}/submit  (POST — §8 R2 submit for approval)
    # /approvals/plan/{id}/decide  (POST — §8 R2 approve/reject)
    approvals = resource(root, "approvals")
    appr_plan = resource(approvals, "plan")
    appr_plan_id = resource(appr_plan, "{id}")
    proxy_method(appr_plan_id, "GET")
    appr_submit = resource(appr_plan_id, "submit")
    proxy_method(appr_submit, "POST")
    appr_decide = resource(appr_plan_id, "decide")
    proxy_method(appr_decide, "POST")

    # /events (POST — §8 domain event publish; staff-only)
    events = resource(root, "events")
    proxy_method(events, "POST")

    # /notifications (GET inbox) + /notifications/{id}/read (POST) — Notification Hub
    notifs = resource(root, "notifications")
    proxy_method(notifs, "GET")
    notif_id = resource(notifs, "{id}")
    notif_read = resource(notif_id, "read")
    proxy_method(notif_read, "POST")

    # /jobs/{job} (POST — on-demand agentic job trigger; OEM/admin-gated)
    jobs = resource(root, "jobs")
    jobs_id = resource(jobs, "{job}")
    proxy_method(jobs_id, "POST")

    # /prospects (GET — A4 upgrade prospects) + /prospects/acquire/{vin} (POST)
    prospects_res = resource(root, "prospects")
    proxy_method(prospects_res, "GET")
    prospects_acq = resource(prospects_res, "acquire")
    prospects_acq_vin = resource(prospects_acq, "{vin}")
    proxy_method(prospects_acq_vin, "POST")

    # /knowledge/retrieve (POST — Tier C KnowledgeBase grounding)
    knowledge_res = resource(root, "knowledge")
    knowledge_retrieve = resource(knowledge_res, "retrieve")
    proxy_method(knowledge_retrieve, "POST")

    # Cross-persona read via the AgentCore Gateway (spec §6). /gateway/oem/{tool}
    # (GET) — the sanctioned path for a non-OEM persona to read OEM-owned data.
    gateway_res = resource(root, "gateway")
    gateway_oem = resource(gateway_res, "oem")
    gateway_oem_tool = resource(gateway_oem, "{tool}")
    proxy_method(gateway_oem_tool, "GET")

    # Amazon Quick (quicksight) — plug-and-play, disabled unless ENABLE_QUICK.
    # /oem/quick/flows (GET), /oem/quick/dashboards (GET),
    # /oem/quick/dashboards/{id}/embed-url (GET registered-user embed)
    oem_quick = resource(oem, "quick")
    oem_quick_flows = resource(oem_quick, "flows")
    proxy_method(oem_quick_flows, "GET")
    oem_quick_dash = resource(oem_quick, "dashboards")
    proxy_method(oem_quick_dash, "GET")
    oem_quick_dash_id = resource(oem_quick_dash, "{id}")
    oem_quick_embed = resource(oem_quick_dash_id, "embed-url")
    proxy_method(oem_quick_embed, "GET")

    # --- platform routes (app/services/api/platform_routes.py) ---------------
    # /tasks (GET list, POST create) · /tasks/{id} (GET, PATCH)
    tasks_res = resource(root, "tasks")
    proxy_method(tasks_res, "GET"); proxy_method(tasks_res, "POST")
    task_id = resource(tasks_res, "{id}")
    proxy_method(task_id, "GET"); proxy_method(task_id, "PATCH")
    # /handoffs (POST) · /handoffs/{id}/accept (POST, PATCH)
    handoffs_res = resource(root, "handoffs")
    proxy_method(handoffs_res, "POST")
    handoff_accept = resource(resource(handoffs_res, "{id}"), "accept")
    proxy_method(handoff_accept, "POST"); proxy_method(handoff_accept, "PATCH")
    # /journeys/{id}/activity (GET)
    proxy_method(resource(jid, "activity"), "GET")
    # /nav/resolve (GET) · /nav/ctx (POST)
    nav = resource(root, "nav")
    proxy_method(resource(nav, "resolve"), "GET")
    proxy_method(resource(nav, "ctx"), "POST")
    # /me/home (GET)
    proxy_method(resource(resource(root, "me"), "home"), "GET")
    # /recommendations (POST) · /{id}/accept|dismiss (POST, PATCH) · /stats (GET)
    recs_res = resource(root, "recommendations")
    proxy_method(recs_res, "POST")
    proxy_method(resource(recs_res, "stats"), "GET")
    rec_id = resource(recs_res, "{id}")
    for verb_part in ("accept", "dismiss"):
        r = resource(rec_id, verb_part)
        proxy_method(r, "POST"); proxy_method(r, "PATCH")
    # /agents/actions (GET)
    proxy_method(resource(resource(root, "agents"), "actions"), "GET")
    # /admin/autonomy/rollback (POST)
    proxy_method(resource(admin_auto, "rollback"), "POST")
    # /config/market (GET)
    proxy_method(resource(resource(root, "config"), "market"), "GET")

    # Catch-all: every other path (the per-domain route modules in
    # app/services/api/routes/) reaches the service Lambda, which 404s unknown
    # routes itself — new routes need no deployer change.
    proxy_res = resource(root, "{proxy+}")
    proxy_method(proxy_res, "ANY")
    # CORS preflight: browsers send OPTIONS without Authorization, so every
    # resource gets an unauthenticated OPTIONS that the Lambda answers with 204
    # + Access-Control-Allow-* headers.
    for rid in [root] + created:
        proxy_method(rid, "OPTIONS", authorized=False)
    # ...and API Gateway's own error replies (authorizer 401/403, 429, 5xx) carry
    # them too, before the deployment below publishes the definition.
    ensure_gateway_cors(agw, api_id, cors_origin or cors.LOCAL_ORIGINS[0])

    agw.create_deployment(restApiId=api_id, stageName=STAGE)

    # allow API Gateway to invoke both lambdas. Statement ids are keyed to the
    # API id so a recreated API (new id) always gets a fresh, non-colliding grant.
    grants = [
        (SVC_FN, f"apigw-invoke-svc-{api_id}", f"arn:aws:execute-api:{REGION}:{account}:{api_id}/*"),
        (AUTH_FN, f"apigw-invoke-auth-{api_id}",
         f"arn:aws:execute-api:{REGION}:{account}:{api_id}/authorizers/{authorizer_id}"),
    ]
    # The authorizer is recreated on every --apply (new authorizer id), so a
    # fresh invoke grant is added each time. Left unmanaged, these accumulate and
    # eventually blow the 20 KB Lambda resource-policy limit — at which point
    # add_permission raises PolicyLengthExceededException, the authorizer grant
    # silently fails to land, and every request dies with
    # AuthorizerConfigurationException ({"message":null}). Prune every stale
    # apigw-invoke-* statement first so the policy never fills; then add the
    # current grants.
    for fn in (SVC_FN, AUTH_FN):
        try:
            policy = json.loads(lam.get_policy(FunctionName=fn)["Policy"])
        except lam.exceptions.ResourceNotFoundException:
            continue
        for stmt in policy.get("Statement", []):
            sid = stmt.get("Sid", "")
            if sid.startswith("apigw-invoke-"):
                try:
                    lam.remove_permission(FunctionName=fn, StatementId=sid)
                except lam.exceptions.ResourceNotFoundException:
                    pass
    for fn, sid, src in grants:
        lam.add_permission(FunctionName=fn, StatementId=sid, Action="lambda:InvokeFunction",
                           Principal="apigateway.amazonaws.com", SourceArn=src)
    print(f"  [api] deployed {API_NAME} ({api_id}), stage {STAGE}")
    return f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{STAGE}"


def delete_stack(iam, lam, agw, account: str) -> None:
    for a in [x for x in agw.get_rest_apis().get("items", []) if x["name"] == API_NAME]:
        agw.delete_rest_api(restApiId=a["id"]); print(f"  deleted api {a['id']}")
    for fn in (AUTH_FN, SVC_FN):
        try:
            lam.delete_function(FunctionName=fn); print(f"  deleted lambda {fn}")
        except lam.exceptions.ResourceNotFoundException:
            pass
    # Per-function roles (+ the legacy shared role) are torn down by iam_roles;
    # delete them here only when tearing down the whole L4 stack in isolation.
    iam_roles.delete_all_roles(iam, account)


def runtime_env_or_exit(path: Path | None = None, *, quiet: bool = False) -> dict[str, str]:
    """runtime_env_from_config, printing one wired line per agent unless quiet;
    a malformed config is fatal (a half-wired service is worse than no deploy)."""
    try:
        env = runtime_env_from_config(path or AGENTCORE_CONFIG)
    except ConfigError as e:
        raise SystemExit(f"  [agentcore] {e}; fix or remove app/infra/{AGENTCORE_CONFIG.name}") from None
    for code, arn in json.loads(env.get("RETAIL_OPS_AGENT_RUNTIMES") or "{}").items():
        if not quiet:
            print(f"  [agentcore] wired {code} ({registry.AGENTS[code].display}) -> runtime ...{arn[-10:]}")
    return env


def toggle_agents(lam, functions: list[str], *, disable: list[str] | str | None,
                  enable: list[str] | str | None, apply: bool, sales_package_note: bool = True) -> int:
    """--disable-agents / --enable-agents on the named Lambdas (merge-safe)."""
    note = _a2_note(disable) if disable and sales_package_note else None
    for fn in functions:
        set_disable(lam, fn, disable=disable, enable=enable, apply=apply)
    if note:
        print(note)
    if not apply:
        print("  (dry run: re-run with --apply to change the Lambda configuration)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the L4 access-layer stack via boto3.")
    ap.add_argument("--apply", action="store_true", help="create/update (default: dry-run)")
    ap.add_argument("--delete", action="store_true", help="tear the stack down")
    switch = ap.add_mutually_exclusive_group()
    switch.add_argument("--disable-agents", metavar="CODES|all",
                        help="run these agents in-process on the service Lambda (rollback switch; merge-safe)")
    switch.add_argument("--enable-agents", metavar="CODES|all",
                        help="route these agents to their runtimes again")
    switch.add_argument("--dev-token", choices=("on", "off"),
                        help="accept (on) or refuse (off) the plain development tokens on the authorizer")
    args = ap.parse_args(argv)
    try:
        disable = parse_agent_list(args.disable_agents) if args.disable_agents else None
        enable = parse_agent_list(args.enable_agents) if args.enable_agents else None
    except ValueError as e:
        ap.error(str(e))

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    iam, lam, agw = s.client("iam"), s.client("lambda"), s.client("apigateway")
    print(f"account={account} region={REGION} runtime={RUNTIME}")

    if disable or enable:
        return toggle_agents(lam, [SVC_FN], disable=disable, enable=enable, apply=args.apply)
    if args.dev_token:
        enabled = args.dev_token == "on"  # noqa: S105 - the on/off switch value, not a credential
        return set_dev_token(lam, [AUTH_FN, STREAM_FN], enabled, apply=args.apply)
    if args.delete:
        delete_stack(iam, lam, agw, account); return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying {API_NAME}")
    # Decided first so a bad ENABLE_QUICK override stops the deploy before any change.
    svc_quick_env = quick_env(s.client("quicksight"), account, os.environ.get(QUICK_FLAG))
    svc_quick_env.update(embed_domains_env(os.environ.get(EMBED_DOMAINS)))
    # The CORS origin rides on the same service-Lambda env (same console URL source).
    svc_quick_env.update(console_origin_env(os.environ.get(CONSOLE_ORIGIN)))
    auth_role_arn, svc_role_arn = ensure_roles(iam, account, args.apply)
    code = _zip_code()
    print(f"  [pkg] app zip = {len(code)} bytes")
    # Deployed service publishes domain events to the real bus (the EventBridge
    # rule fans out to the subscriber Lambda); local dev keeps in-process dispatch.
    common_env = {"RETAIL_OPS_ENV": naming.ENV, "RETAIL_OPS_ENV_PREFIX": ENV_PREFIX,
                  "RETAIL_OPS_BEDROCK_MODEL": BEDROCK_MODEL,
                  "RETAIL_OPS_EVENT_BUS_LIVE": "1", "RETAIL_OPS_EVENT_BUS": naming.EVENT_BUS,
                  # Select the AWS engine implementations (Code Interpreter swap
                  # point, Personalize ranker). Every aws engine falls back to its
                  # audited local twin when the AWS resource isn't configured, so
                  # this is safe before Personalize/AgentCore are provisioned.
                  "INTEGRATION_MODE": "aws"}
    # The SES sender (a verified identity) has no built-in default: outreach
    # emails fall back to dealer tasks until the deploy environment names one.
    common_env.update(ses_from_env())
    # If Amazon Quick (quicksight) resources were discovered, carry the embed
    # user ARN onto the service Lambda so registered-user dashboard embedding
    # works (quick-config.json isn't in the Lambda zip). The ENABLE_QUICK flag
    # (quick_env above) still gates whether it's used.
    # Bedrock Guardrail (deploy_guardrails.py): every Converse call attaches it.
    _gc = naming.config_path("guardrail")
    if _gc.exists():
        import json as _json
        gc = _json.loads(_gc.read_text())
        if gc.get("guardrail_id"):
            common_env["RETAIL_OPS_GUARDRAIL_ID"] = gc["guardrail_id"]
            common_env["RETAIL_OPS_GUARDRAIL_VERSION"] = str(gc.get("version") or "DRAFT")
            print(f"  [guardrail] wired {gc.get('name')} v{gc.get('version')} onto the Lambdas")
    # AgentCore runtimes (deploy_agentcore.py, one per agent, ADR 0009): the service
    # delegates agent work (the A3 turn, the Sales Package, the A5 budget shift,
    # on-demand jobs) to them; the Lambda carries no agent framework.
    common_env.update(runtime_env_or_exit())
    # Bedrock Knowledge Base (deploy_knowledge_base.py): grounded retrieval for A3/A6/A7.
    _kc = naming.config_path("knowledge-base")
    if _kc.exists():
        import json as _json
        kc = _json.loads(_kc.read_text())
        if kc.get("knowledge_base_id"):
            common_env["RETAIL_OPS_KNOWLEDGE_BASE_ID"] = kc["knowledge_base_id"]
            print(f"  [knowledge] wired Bedrock KB {kc['knowledge_base_id']} onto the Lambdas")
    # AgentCore Memory (deploy_memory.py): durable sourced customer context for A3/A2.
    _mc = naming.config_path("memory")
    if _mc.exists():
        import json as _json
        mc = _json.loads(_mc.read_text())
        if mc.get("memory_id"):
            common_env["RETAIL_OPS_MEMORY_ID"] = mc["memory_id"]
            print(f"  [memory] wired AgentCore Memory {mc['memory_id']} onto the Lambdas")
    _qc = naming.config_path("quick")
    if _qc.exists():
        import json as _json
        qc = _json.loads(_qc.read_text())
        if qc.get("embed_user_arn"):
            common_env["RETAIL_OPS_QUICK_EMBED_USER_ARN"] = qc["embed_user_arn"]
        print(f"  [quick] wired Amazon Quick embed user {qc.get('embed_user_name')} onto the service Lambda")
    # If Cognito has been provisioned, wire its issuer/client into the authorizer
    # so it verifies real JWTs. The dev token stays enabled (RETAIL_OPS_ALLOW_DEV_TOKEN
    # defaults on) unless switched off with --dev-token off, which redeploys keep.
    auth_env = dict(common_env)
    cog_cfg = naming.config_path("cognito")
    if cog_cfg.exists():
        import json as _json
        c = _json.loads(cog_cfg.read_text())
        auth_env["RETAIL_OPS_COGNITO_ISSUER"] = c["issuer"]
        # Accept every app client of the pool: the native sign-in client and, when
        # SSO is wired (cognito_setup --federate), the Hosted-UI client that mints
        # the federated tokens. Audience is an allowlist, so an SSO login verifies.
        auds = [c["client_id"]]
        hosted = (c.get("federate") or {}).get("hosted_ui_client_id")
        # Generated config can predate an IdP/client created in the account, or
        # be rewritten by a native-only setup run. Discover the one known
        # user-facing Hosted UI client by name so SSO tokens are never rejected
        # merely because local deployment state is stale. Do not allowlist the
        # machine-to-machine Quick client or arbitrary pool clients.
        if not hosted:
            try:
                cog = s.client("cognito-idp")
                clients = cog.list_user_pool_clients(
                    UserPoolId=c["user_pool_id"], MaxResults=60).get("UserPoolClients", [])
                hosted = next((x["ClientId"] for x in clients
                               if x.get("ClientName") == naming.name("staff-hostedui")), None)
                if hosted:
                    print(f"  [cognito] discovered Hosted-UI client {hosted} from the user pool")
            except Exception as e:
                print(f"  [cognito] Hosted-UI client discovery failed ({type(e).__name__}); "
                      "native audience remains configured")
        if hosted and hosted not in auds:
            auds.append(hosted)
        auth_env["RETAIL_OPS_COGNITO_CLIENT_ID"] = ",".join(auds)
        print(f"  [cognito] authorizer wired to pool {c['user_pool_id']} "
              f"({len(auds)} app client{'s' if len(auds) > 1 else ''} accepted)")
    jwt_layer = _jwt_layer_arn(lam, account)
    if jwt_layer:
        print(f"  [layer] authorizer uses {jwt_layer.split(':')[-2]}:{jwt_layer.split(':')[-1]} (PyJWT+cryptography)")
    auth_arn = ensure_function(lam, AUTH_FN, "app.services.api.authorizer.lambda_handler",
                               auth_role_arn, code, auth_env, args.apply,
                               layers=[jwt_layer] if jwt_layer else None)
    # ENABLE_QUICK goes on the service Lambda only (the authorizer never calls Quick).
    svc_arn = ensure_function(lam, SVC_FN, "app.services.api.handler.lambda_handler",
                              svc_role_arn, code, {**common_env, **svc_quick_env}, args.apply,
                              memory_mb=SVC_MEMORY_MB)
    try:
        current_origin = (lam.get_function_configuration(FunctionName=SVC_FN).get("Environment") or {}) \
            .get("Variables", {}).get(CONSOLE_ORIGIN)
    except lam.exceptions.ResourceNotFoundException:
        current_origin = None
    url = ensure_api(agw, lam, account, auth_arn, svc_arn, args.apply,
                     cors_origin=gateway_cors_origin(svc_quick_env, current_origin))
    print(f"[{mode}] done. API base URL: {url}")
    if not args.apply:
        print("  (re-run with --apply to create)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
