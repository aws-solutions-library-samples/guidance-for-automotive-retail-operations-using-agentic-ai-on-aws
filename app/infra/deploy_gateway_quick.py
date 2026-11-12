#!/usr/bin/env python3
"""CUSTOM_JWT AgentCore Gateway for AWS Quick (Retail Ops Quick Build checklist §3).

The AWS_IAM gateway (`retail-ops-<env>-crosspersona`, deploy_gateway.py) serves the
console/runtime cross-persona path. AWS Quick's MCP connector, by contrast,
authenticates with an **OAuth client-credentials token** from Cognito, so it
needs a gateway whose inbound authorizer is **CUSTOM_JWT** validating that
Cognito token. This deployer stands up that second gateway:

  * Gateway  `retail-ops-<env>-crosspersona-quick`  (protocol MCP, authorizer CUSTOM_JWT)
      - discoveryUrl = the Cognito pool's OIDC discovery document
      - allowedClients = the `retail-ops-quick-mcp` client; allowedScopes = retail-ops-oem/read
  * Lambda target `oem-read` → the SAME `retail-ops-<env>-gateway-oem` Lambda, with a
      JWT tool schema (no `claims` property — identity comes from the token; the
      handler derives an OEM service scope and ignores model-supplied claims).

Reuses the gateway IAM role and the read Lambda created by deploy_gateway.py.
Writes app/infra/gateway-quick-config.json.

  --apply / --status / --delete.  Idempotent.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import deploy_gateway, naming  # reuse schema + role + lambda helpers

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
CONFIG_PATH = naming.config_path("gateway-quick")

GW_NAME = naming.name("crosspersona-quick")
GW_TARGET = "oem-read"
ALLOWED_SCOPE = "retail-ops-oem/read"
QUICK_MCP_SECRET = naming.name("quick-mcp-oauth")  # noqa: S105 — Secrets Manager secret name, not a credential


def _cognito_cfg() -> dict:
    cfg = naming.config_path("cognito")
    return json.loads(cfg.read_text()) if cfg.exists() else {}


def _jwt_settings() -> tuple[str, str, str]:
    """(discovery_url, allowed_client, secret_name) for the CUSTOM_JWT authorizer.

    Read from cognito-config.json, which cognito_setup.py writes: the staff user pool
    (`user_pool_id`) and the `retail-ops-quick-mcp` client-credentials client
    (`quick_mcp.client_id`). There is deliberately no literal fallback — a pool or
    client id from an earlier stack would provision a gateway nobody can get a token for.
    """
    cfg = _cognito_cfg()
    pool_id = cfg.get("user_pool_id")
    quick = cfg.get("quick_mcp") or {}
    client_id = quick.get("client_id")
    if not (pool_id and client_id):
        raise SystemExit(f"app/infra/{naming.config_path('cognito').name} has no user_pool_id / quick_mcp.client_id; "
                         "run `python3 -m app.infra.cognito_setup` first")
    discovery = f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    return discovery, client_id, quick.get("secret_name") or QUICK_MCP_SECRET


def _find_gateway(agc) -> dict | None:
    for g in agc.list_gateways().get("items", []):
        if g.get("name") == GW_NAME:
            return g
    return None


def _find_target(agc, gw_id: str) -> dict | None:
    for t in agc.list_gateway_targets(gatewayIdentifier=gw_id).get("items", []):
        if t.get("name") == GW_TARGET:
            return t
    return None


def _ensure_gateway(agc, gw_role_arn: str, apply: bool, discovery_url: str, allowed_client: str) -> dict:
    existing = _find_gateway(agc)
    if not apply:
        print(f"  [dry-run] would ensure gateway {GW_NAME} (MCP, CUSTOM_JWT; client {allowed_client}, scope {ALLOWED_SCOPE})")
        return existing or {}
    authorizer = {"customJWTAuthorizer": {
        "discoveryUrl": discovery_url,
        "allowedClients": [allowed_client],
        "allowedScopes": [ALLOWED_SCOPE],
    }}
    if existing:
        print(f"  [gateway] {GW_NAME} exists ({existing.get('gatewayId')})")
        return agc.get_gateway(gatewayIdentifier=existing["gatewayId"])
    resp = agc.create_gateway(
        name=GW_NAME,
        description="Retail Ops cross-persona OEM read for AWS Quick (CUSTOM_JWT)",
        roleArn=gw_role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration=authorizer,
        protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26"],
                                       "instructions": "Scoped, field-stripped, cited OEM reads for Quick.",
                                       "searchType": "SEMANTIC"}},
    )
    gw_id = resp["gatewayId"]
    print(f"  [gateway] created {GW_NAME} ({gw_id})")
    deploy_gateway._wait_gateway_ready(agc, gw_id)
    return agc.get_gateway(gatewayIdentifier=gw_id)


def _ensure_target(agc, gw_id: str, lambda_arn: str, apply: bool) -> dict:
    if not apply:
        print(f"  [dry-run] would ensure target {GW_TARGET} -> {lambda_arn} (JWT tool schema)")
        return {}
    tool_schema = deploy_gateway._tool_schema(for_jwt=True)
    target_cfg = {"mcp": {"lambda": {"lambdaArn": lambda_arn,
                                     "toolSchema": {"inlinePayload": tool_schema}}}}
    cred = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    existing = _find_target(agc, gw_id)
    if existing:
        agc.update_gateway_target(gatewayIdentifier=gw_id, targetId=existing["targetId"],
                                  name=GW_TARGET, targetConfiguration=target_cfg,
                                  credentialProviderConfigurations=cred)
        print(f"  [target] updated {GW_TARGET} ({len(tool_schema)} tools, Draft-7, no claims prop)")
        return agc.get_gateway_target(gatewayIdentifier=gw_id, targetId=existing["targetId"])
    resp = agc.create_gateway_target(gatewayIdentifier=gw_id, name=GW_TARGET,
                                     description="OEM read tools (JWT; token-derived scope)",
                                     targetConfiguration=target_cfg,
                                     credentialProviderConfigurations=cred)
    print(f"  [target] created {GW_TARGET} ({resp['targetId']}, {len(tool_schema)} tools)")
    return resp


def apply_stack(apply: bool) -> dict:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    agc = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # Reuse the gateway role + the read Lambda that deploy_gateway.py created.
    gw_role_arn = deploy_gateway._ensure_gateway_role(iam, account, apply)
    lambda_arn = f"arn:aws:lambda:{REGION}:{account}:function:{deploy_gateway.GW_LAMBDA}"
    try:
        lam.get_function(FunctionName=deploy_gateway.GW_LAMBDA)
    except Exception:
        print(f"  WARN: {deploy_gateway.GW_LAMBDA} not found — run deploy_gateway.py --apply first.")

    discovery_url, allowed_client, secret_name = _jwt_settings()
    gw = _ensure_gateway(agc, gw_role_arn, apply, discovery_url, allowed_client)
    gw_id = gw.get("gatewayId", "")
    gw_url = gw.get("gatewayUrl") or gw.get("gatewayEndpoint") or ""
    gw_arn = gw.get("gatewayArn", "")

    if apply and gw_id:
        try:
            lam.remove_permission(FunctionName=deploy_gateway.GW_LAMBDA, StatementId=f"agentcore-gw-{gw_id}")
        except Exception:
            pass
        lam.add_permission(FunctionName=deploy_gateway.GW_LAMBDA, StatementId=f"agentcore-gw-{gw_id}",
                           Action="lambda:InvokeFunction", Principal="bedrock-agentcore.amazonaws.com",
                           SourceArn=gw_arn or f"arn:aws:bedrock-agentcore:{REGION}:{account}:gateway/{gw_id}")

    target = _ensure_target(agc, gw_id, lambda_arn, apply) if gw_id else {}

    cfg = {"region": REGION, "gateway_name": GW_NAME, "gateway_id": gw_id,
           "gateway_arn": gw_arn, "gateway_url": gw_url,
           "mcp_endpoint": gw_url, "target_id": target.get("targetId", ""),
           "authorizer": "CUSTOM_JWT", "discovery_url": discovery_url,
           "allowed_client": allowed_client, "allowed_scope": ALLOWED_SCOPE,
           "oauth_secret": f"arn:aws:secretsmanager:{REGION}:{account}:secret:{secret_name}"}
    if apply:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"  [config] wrote {CONFIG_PATH.name}")
        print(f"\n  Quick MCP endpoint: {gw_url}")
    return cfg


def status() -> None:
    import boto3
    agc = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gw = _find_gateway(agc)
    if not gw:
        print("quick jwt gateway: NOT PROVISIONED")
        return
    full = agc.get_gateway(gatewayIdentifier=gw["gatewayId"])
    print(f"quick jwt gateway: {GW_NAME} ({gw['gatewayId']}) status={full.get('status')}")
    print(f"  authorizer: {full.get('authorizerType')}  url: {full.get('gatewayUrl')}")
    for t in agc.list_gateway_targets(gatewayIdentifier=gw["gatewayId"]).get("items", []):
        print(f"  target {t.get('name')} ({t.get('targetId')}) status={t.get('status')}")


def delete_stack() -> None:
    import boto3
    agc = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gw = _find_gateway(agc)
    if gw:
        gw_id = gw["gatewayId"]
        for t in agc.list_gateway_targets(gatewayIdentifier=gw_id).get("items", []):
            agc.delete_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])
            print(f"  deleted target {t['name']}")
        agc.delete_gateway(gatewayIdentifier=gw_id)
        print(f"  deleted gateway {GW_NAME}")
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Retail Ops CUSTOM_JWT gateway for AWS Quick")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(); return
    if args.delete:
        delete_stack(); return
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] CUSTOM_JWT gateway for AWS Quick")
    cfg = apply_stack(args.apply)
    print(f"[{mode}] done. gateway_id={cfg.get('gateway_id')} url={cfg.get('gateway_url')}")


if __name__ == "__main__":
    main()
