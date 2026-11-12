#!/usr/bin/env python3
"""AgentCore Gateway — cross-persona data bridge (spec retail-ops-quick-project.md §6).

Provisions the single bridge by which a non-OEM persona reads OEM-owned data
WITHOUT touching retail-ops-<env>-OEM_* directly:

  1. a cross-persona **read Lambda** (retail-ops-<env>-gateway-oem) running
     app/services/gateway/handler.lambda_handler, on its OWN least-privilege role
     that can read only the OEM tables the tools expose;
  2. a **Gateway IAM role** the AgentCore Gateway assumes to invoke that Lambda;
  3. the **Gateway** itself (protocolType=MCP, authorizerType=AWS_IAM);
  4. a **Lambda target** whose toolSchema is generated from
     app.services.gateway.oem_read.TOOLS (so schema never drifts from code).

Writes app/infra/gateway-config.json {gateway_id, gateway_arn, gateway_url,
target_id, target_lambda_arn} which app/services/gateway/client.py reads and
which deploy_l4 wires onto the service + runtime as RETAIL_OPS_GATEWAY_URL /
RETAIL_OPS_GATEWAY_TARGET_LAMBDA.

  --apply   create/update everything
  --status  print what exists
  --delete  tear down gateway + target + lambda + roles
  --code-only [--apply]  only push new code to the read Lambda (both gateways
            call it); nothing else changes

Idempotent: re-running --apply reuses existing resources by name.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import deploy_l4, iam_roles, naming  # reuse app zip + role helpers
from ..services.gateway import oem_read

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
CONFIG_PATH = naming.config_path("gateway")

GW_LAMBDA = naming.name("gateway-oem")
GW_LAMBDA_ROLE = naming.name("gateway-lambda-role")
GW_ROLE = naming.name("gateway-role")          # assumed by bedrock-agentcore
GW_NAME = naming.name("crosspersona")
GW_TARGET = "oem-read"
HANDLER = "app.services.gateway.handler.lambda_handler"

# OEM tables the gateway read Lambda may touch (read-only) — the exact set the
# tools in oem_read expose, nothing wider.
GW_OEM_TABLES = ["OEM_Accounts", "OEM_Alerts", "OEM_CIPrograms"]


# --- IAM --------------------------------------------------------------------

def _ensure_lambda_role(iam, account: str, apply: bool) -> str:
    """Least-privilege role for the gateway read Lambda: read-only on exactly the
    OEM tables the tools expose, plus its own log group."""
    arn = f"arn:aws:iam::{account}:role/{GW_LAMBDA_ROLE}"
    assume = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                       "Action": "sts:AssumeRole"}],
    }
    table_arns = [f"arn:aws:dynamodb:{REGION}:{account}:table/{iam_roles.ENV_PREFIX}{t}"
                  for t in GW_OEM_TABLES]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ReadOEMTables", "Effect": "Allow",
             "Action": ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
             "Resource": table_arns},
            {"Sid": "Logs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": f"arn:aws:logs:{REGION}:{account}:log-group:/aws/lambda/{GW_LAMBDA}:*"},
        ],
    }
    if not apply:
        print(f"  [dry-run] would ensure role {GW_LAMBDA_ROLE} (read {GW_OEM_TABLES})")
        return arn
    _upsert_role(iam, GW_LAMBDA_ROLE, assume, "gateway-oem-read", policy)
    return arn


def _ensure_gateway_role(iam, account: str, apply: bool) -> str:
    """Role the AgentCore Gateway assumes to invoke the target Lambda."""
    arn = f"arn:aws:iam::{account}:role/{GW_ROLE}"
    assume = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow",
                       "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                       "Action": "sts:AssumeRole"}],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [{"Sid": "InvokeTarget", "Effect": "Allow",
                       "Action": "lambda:InvokeFunction",
                       "Resource": f"arn:aws:lambda:{REGION}:{account}:function:{GW_LAMBDA}"}],
    }
    if not apply:
        print(f"  [dry-run] would ensure gateway role {GW_ROLE}")
        return arn
    _upsert_role(iam, GW_ROLE, assume, "gateway-invoke", policy)
    return arn


def _upsert_role(iam, name: str, assume: dict, policy_name: str, policy: dict) -> None:
    try:
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(assume),
                        Description="Retail Ops AgentCore Gateway (spec §6)")
        print(f"  [iam] created {name}")
        time.sleep(8)  # role propagation
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(assume))
        print(f"  [iam] role {name} exists")
    iam.put_role_policy(RoleName=name, PolicyName=policy_name,
                        PolicyDocument=json.dumps(policy))


# --- tool schema ------------------------------------------------------------

def _tool_schema(for_jwt: bool = False) -> list[dict]:
    """Generate the MCP inlinePayload toolSchema from oem_read.TOOLS.

    JSON-Schema Draft 7: object type, `properties`, `required` as a root array.
    For the CUSTOM_JWT gateway the caller identity comes from the validated
    token (the Lambda ignores a model-supplied `claims`), so `claims` is NOT a
    tool property there; for the AWS_IAM path the bridge forwards `claims`."""
    schema = []
    for name, spec in oem_read.TOOLS.items():
        props, required = {}, []
        for arg, meta in spec["args"].items():
            props[arg] = {"type": meta.get("type", "string")}
            if meta.get("required"):
                required.append(arg)
        if not for_jwt:
            props["claims"] = {"type": "object",
                               "description": "Caller scope (user_id, role, market_id, dealer_ids, is_oem)."}
        # AgentCore's inlinePayload inputSchema accepts only
        # type/properties/required/items/description (it applies Draft-7
        # semantics itself; $schema/additionalProperties are rejected). `required`
        # is a root-level array, which is the Draft-7 shape.
        schema.append({
            "name": name,
            "description": spec["description"],
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        })
    return schema


# --- Gateway ----------------------------------------------------------------

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


def _ensure_gateway(agc, gw_role_arn: str, apply: bool) -> dict:
    existing = _find_gateway(agc)
    if not apply:
        print(f"  [dry-run] would ensure gateway {GW_NAME} (MCP, AWS_IAM)")
        return existing or {}
    if existing:
        print(f"  [gateway] {GW_NAME} exists ({existing.get('gatewayId')})")
        return agc.get_gateway(gatewayIdentifier=existing["gatewayId"])
    resp = agc.create_gateway(
        name=GW_NAME,
        description="Retail Ops cross-persona data bridge (spec §6)",
        roleArn=gw_role_arn,
        protocolType="MCP",
        authorizerType="AWS_IAM",
        protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26"],
                                       "instructions": "Cross-persona OEM read tools; caller scope enforced.",
                                       "searchType": "SEMANTIC"}},
    )
    gw_id = resp["gatewayId"]
    print(f"  [gateway] created {GW_NAME} ({gw_id})")
    _wait_gateway_ready(agc, gw_id)
    return agc.get_gateway(gatewayIdentifier=gw_id)


def _ensure_target(agc, gw_id: str, lambda_arn: str, apply: bool) -> dict:
    if not apply:
        print(f"  [dry-run] would ensure target {GW_TARGET} -> {lambda_arn}")
        return {}
    tool_schema = _tool_schema()
    target_cfg = {"mcp": {"lambda": {"lambdaArn": lambda_arn,
                                     "toolSchema": {"inlinePayload": tool_schema}}}}
    cred = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    existing = _find_target(agc, gw_id)
    if existing:
        agc.update_gateway_target(
            gatewayIdentifier=gw_id, targetId=existing["targetId"],
            name=GW_TARGET, targetConfiguration=target_cfg,
            credentialProviderConfigurations=cred)
        print(f"  [target] updated {GW_TARGET} ({len(tool_schema)} tools)")
        return agc.get_gateway_target(gatewayIdentifier=gw_id, targetId=existing["targetId"])
    resp = agc.create_gateway_target(
        gatewayIdentifier=gw_id, name=GW_TARGET,
        description="OEM read tools (scoped, field-stripped)",
        targetConfiguration=target_cfg,
        credentialProviderConfigurations=cred)
    print(f"  [target] created {GW_TARGET} ({resp['targetId']}, {len(tool_schema)} tools)")
    return resp


def _wait_gateway_ready(agc, gw_id: str, tries: int = 30) -> None:
    for _ in range(tries):
        st = agc.get_gateway(gatewayIdentifier=gw_id).get("status")
        print(f"    status={st}")
        if st in ("READY", "ACTIVE"):
            return
        if st in ("FAILED",):
            raise RuntimeError(f"gateway {gw_id} failed to provision")
        time.sleep(6)


# --- orchestration ----------------------------------------------------------

def apply_stack(apply: bool) -> dict:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    agc = boto3.client("bedrock-agentcore-control", region_name=REGION)

    lambda_role_arn = _ensure_lambda_role(iam, account, apply)
    gw_role_arn = _ensure_gateway_role(iam, account, apply)

    code = deploy_l4._zip_code()
    print(f"  [pkg] app zip = {len(code)} bytes")
    lambda_arn = deploy_l4.ensure_function(
        lam, GW_LAMBDA, HANDLER, lambda_role_arn, code,
        {"RETAIL_OPS_ENV": naming.ENV, "RETAIL_OPS_ENV_PREFIX": iam_roles.ENV_PREFIX}, apply)
    print(f"  [lambda] {GW_LAMBDA} -> {lambda_arn}")

    gw = _ensure_gateway(agc, gw_role_arn, apply)
    gw_id = gw.get("gatewayId", "")
    gw_url = gw.get("gatewayUrl") or gw.get("gatewayEndpoint") or ""
    gw_arn = gw.get("gatewayArn", "")

    # allow the gateway to invoke the target lambda (resource policy)
    if apply and gw_id:
        try:
            lam.remove_permission(FunctionName=GW_LAMBDA, StatementId=f"agentcore-gw-{gw_id}")
        except lam.exceptions.ResourceNotFoundException:
            pass
        except Exception:
            pass
        lam.add_permission(FunctionName=GW_LAMBDA, StatementId=f"agentcore-gw-{gw_id}",
                           Action="lambda:InvokeFunction",
                           Principal="bedrock-agentcore.amazonaws.com",
                           SourceArn=gw_arn or f"arn:aws:bedrock-agentcore:{REGION}:{account}:gateway/{gw_id}")

    target = _ensure_target(agc, gw_id, lambda_arn, apply) if gw_id else {}

    # Wire the gateway onto the service Lambda so its /gateway/oem/* route
    # reaches the Gateway, and grant that role permission to invoke the target
    # Lambda (the sanctioned cross-persona read path). The service role reads its
    # own persona tables directly but must go THROUGH the gateway for OEM data.
    if apply and lambda_arn:
        try:
            svc_role = f"arn:aws:iam::{account}:role/{iam_roles.ROLE_SERVICE}"
            iam.put_role_policy(
                RoleName=iam_roles.ROLE_SERVICE, PolicyName="invoke-gateway-target",
                PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                    "Sid": "InvokeGatewayTarget", "Effect": "Allow",
                    "Action": "lambda:InvokeFunction", "Resource": lambda_arn}]}))
            print("  [iam] service role may invoke gateway target lambda")
        except Exception as e:
            print(f"  [iam] warn: could not grant service->gateway invoke: {e}")
        try:
            cur = lam.get_function_configuration(FunctionName=deploy_l4.SVC_FN)
            env = dict((cur.get("Environment") or {}).get("Variables") or {})
            env["RETAIL_OPS_GATEWAY_URL"] = gw_url
            env["RETAIL_OPS_GATEWAY_ARN"] = gw_arn
            env["RETAIL_OPS_GATEWAY_TARGET_LAMBDA"] = lambda_arn
            deploy_l4._wait_updated(lam, deploy_l4.SVC_FN)
            lam.update_function_configuration(
                FunctionName=deploy_l4.SVC_FN, Environment={"Variables": env})
            print(f"  [lambda] wired RETAIL_OPS_GATEWAY_* onto {deploy_l4.SVC_FN}")
        except Exception as e:
            print(f"  [lambda] warn: could not wire gateway env onto service: {e}")

    cfg = {
        "region": REGION, "gateway_name": GW_NAME,
        "gateway_id": gw_id, "gateway_arn": gw_arn, "gateway_url": gw_url,
        "target_id": target.get("targetId", ""), "target_name": GW_TARGET,
        "target_lambda_arn": lambda_arn,
        "tools": sorted(oem_read.TOOLS),
    }
    if apply:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"  [config] wrote {CONFIG_PATH.name}")
    return cfg


def update_code(apply: bool, lam=None) -> str:
    """Code-only update of the read Lambda that BOTH gateways invoke: re-zip
    app/ and call UpdateFunctionCode. Roles, the function configuration, the
    gateways, their targets and their auth are left as they are. Returns the
    deployed CodeSha256 (the current one on a dry run)."""
    if lam is None:
        import boto3
        lam = boto3.client("lambda", region_name=REGION)
    cur = lam.get_function(FunctionName=GW_LAMBDA)["Configuration"]
    code = deploy_l4._zip_code()
    print(f"  [pkg] app zip = {len(code)} bytes")
    if not apply:
        print(f"  [dry-run] would update the code of {GW_LAMBDA} "
              f"(handler {cur.get('Handler')}, now CodeSha256 {cur.get('CodeSha256')})")
        return cur.get("CodeSha256", "")
    deploy_l4._wait_updated(lam, GW_LAMBDA)
    sha = lam.update_function_code(FunctionName=GW_LAMBDA, ZipFile=code).get("CodeSha256", "")
    deploy_l4._wait_updated(lam, GW_LAMBDA)
    from app.infra import freshness
    freshness.stamp_lambda(lam, GW_LAMBDA, code)
    print(f"  [lambda] updated the code of {GW_LAMBDA}: {cur.get('CodeSha256')} -> {sha}")
    return sha


def status() -> None:
    import boto3
    agc = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gw = _find_gateway(agc)
    if not gw:
        print("gateway: NOT PROVISIONED")
        return
    gw_id = gw["gatewayId"]
    full = agc.get_gateway(gatewayIdentifier=gw_id)
    print(f"gateway: {GW_NAME} ({gw_id}) status={full.get('status')} url={full.get('gatewayUrl')}")
    for t in agc.list_gateway_targets(gatewayIdentifier=gw_id).get("items", []):
        print(f"  target {t.get('name')} ({t.get('targetId')}) status={t.get('status')}")


def delete_stack() -> None:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    agc = boto3.client("bedrock-agentcore-control", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    gw = _find_gateway(agc)
    if gw:
        gw_id = gw["gatewayId"]
        for t in agc.list_gateway_targets(gatewayIdentifier=gw_id).get("items", []):
            agc.delete_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])
            print(f"  deleted target {t['name']}")
        agc.delete_gateway(gatewayIdentifier=gw_id)
        print(f"  deleted gateway {GW_NAME}")
    try:
        lam.delete_function(FunctionName=GW_LAMBDA)
        print(f"  deleted lambda {GW_LAMBDA}")
    except lam.exceptions.ResourceNotFoundException:
        pass
    for role, pol in ((GW_LAMBDA_ROLE, "gateway-oem-read"), (GW_ROLE, "gateway-invoke")):
        try:
            iam.delete_role_policy(RoleName=role, PolicyName=pol)
            iam.delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except iam.exceptions.NoSuchEntityException:
            pass
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Retail Ops AgentCore Gateway deployer (spec §6)")
    ap.add_argument("--apply", action="store_true", help="create/update resources")
    ap.add_argument("--status", action="store_true", help="print current state")
    ap.add_argument("--delete", action="store_true", help="tear down")
    ap.add_argument("--code-only", action="store_true",
                    help=f"only update the code of {GW_LAMBDA} (dry run unless --apply)")
    args = ap.parse_args()
    if args.status:
        status(); return
    if args.delete:
        delete_stack(); return
    mode = "APPLY" if args.apply else "DRY-RUN"
    if args.code_only:
        print(f"[{mode}] {GW_LAMBDA} code-only update (gateways, targets and auth unchanged)")
        update_code(args.apply)
        return
    print(f"[{mode}] AgentCore Gateway (cross-persona bridge, spec §6)")
    cfg = apply_stack(args.apply)
    print(f"[{mode}] done. gateway_id={cfg.get('gateway_id')} url={cfg.get('gateway_url')}")


if __name__ == "__main__":
    main()
