#!/usr/bin/env python3
"""Future secure streaming transport (spec R2, ADR 0008).

The active floor path uses the authenticated API's buffered SSE response. This
AWS_IAM-only Function URL deployer is retained for a future runtime that can
progressively stream through CloudFront OAC; it is not part of deploy/deploy.sh.
If used, it creates:
  1. IAM role  retail-ops-<env>-stream-role  (app/infra/iam_roles.stream_policy —
     domain reads, PLAT_AgentMemory write, Bedrock + guardrail; it verifies the
     caller's token itself because a Function URL bypasses the API Gateway
     authorizer)
  2. Lambda    retail-ops-<env>-floor-stream  (python3.12, handler
     app/services/api/stream_handler.lambda_handler; the SAME app zip
     deploy_l4 packages, so deployed behaviour matches local)
  3. Function URL, AuthType AWS_IAM ONLY — never public; reached same-origin
     through the console CloudFront distribution with OAC (SigV4). With
     CORS for the console origin
  4. app/infra/streaming-config.json {function_url, ...} — the console reads it
     into NEXT_PUBLIC_RETAIL_OPS_STREAM_URL

InvokeMode: RESPONSE_STREAM is requested so the URL is ready for a streaming
runtime, but note the Python caveat — the managed python3.12 runtime has no
response-streaming API (only Node's `awslambda.streamifyResponse` or a custom
runtime / Lambda Web Adapter can flush progressively), so the handler buffers
the SSE document and sends it in ONE flush. Pass --buffered to provision the URL
as BUFFERED instead, which matches what the Python handler actually does.

Dry-run by default; --apply to create/update; --delete to tear down.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:
    from app.infra import deploy_l4, iam_roles, naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import deploy_l4, iam_roles, naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
ENV_PREFIX = naming.PREFIX
STREAM_FN = naming.name("floor-stream")
RUNTIME = "python3.12"
BEDROCK_MODEL = "us.anthropic.claude-opus-4-5-20251101-v1:0"
CONFIG_PATH = naming.config_path("streaming")
HANDLER = "app.services.api.stream_handler.lambda_handler"


def _env() -> dict[str, str]:
    """Same env-injection pattern as deploy_l4: read the sibling deployers'
    *-config.json files and carry what the streaming path needs."""
    env = {"RETAIL_OPS_ENV": naming.ENV, "RETAIL_OPS_ENV_PREFIX": ENV_PREFIX, "RETAIL_OPS_BEDROCK_MODEL": BEDROCK_MODEL,
           "INTEGRATION_MODE": "aws", **deploy_l4.ses_from_env()}
    gc = naming.config_path("guardrail")
    if gc.exists():
        cfg = json.loads(gc.read_text())
        if cfg.get("guardrail_id"):
            env["RETAIL_OPS_GUARDRAIL_ID"] = cfg["guardrail_id"]
            env["RETAIL_OPS_GUARDRAIL_VERSION"] = str(cfg.get("version") or "DRAFT")
            print(f"  [guardrail] wired {cfg.get('name')} v{cfg.get('version')} onto {STREAM_FN}")
    mc = naming.config_path("memory")
    if mc.exists():
        cfg = json.loads(mc.read_text())
        if cfg.get("memory_id"):
            env["RETAIL_OPS_MEMORY_ID"] = cfg["memory_id"]
            print(f"  [memory] wired AgentCore Memory {cfg['memory_id']} onto {STREAM_FN}")
    cog = naming.config_path("cognito")
    if cog.exists():
        cfg = json.loads(cog.read_text())
        env["RETAIL_OPS_COGNITO_ISSUER"] = cfg["issuer"]
        auds = [cfg["client_id"]]
        hosted = (cfg.get("federate") or {}).get("hosted_ui_client_id")
        if hosted and hosted not in auds:
            auds.append(hosted)
        env["RETAIL_OPS_COGNITO_CLIENT_ID"] = ",".join(auds)
        print(f"  [cognito] stream handler verifies pool {cfg['user_pool_id']} tokens in-process")
    return env


def _allowed_origins() -> list[str]:
    """The console origin (from console-config.json) plus local dev."""
    import json as _json
    origins = ["http://localhost:3100"]
    p = naming.config_path("console")
    if p.exists():
        try:
            url = (_json.loads(p.read_text()).get("url") or "").rstrip("/")
            if url:
                origins.insert(0, url)
        except Exception:
            pass
    return origins


def ensure_function_url(lam, apply: bool, invoke_mode: str, distribution_arn: str | None = None) -> str:
    """Function URL with **AuthType=AWS_IAM only** — never NONE.

    A world-accessible Function URL (AuthType NONE + `Principal: "*"`) is a
    public, unauthenticated entry point into the account. The account's security
    control `lambda_function_policy_block_public_access` detects exactly that and
    auto-mitigates it by stripping the resource policy, which is why the URL
    returned 403 even though the app's own handler required a token: the block
    happens ABOVE Lambda, before our code runs. So the browser must never call
    this URL directly. It is reached same-origin through the console's CloudFront
    distribution with Origin Access Control, which signs each request SigV4 (see
    deploy_console.ensure_stream_origin). The only invoke grant is CloudFront's
    service principal, scoped to this distribution.

    NOTE: OAC replaces the `Authorization` header with its SigV4 signature, so the
    app token travels in `X-Retail-Ops-Token` (stream_handler reads that first).
    """
    cfg = {"FunctionName": STREAM_FN, "AuthType": "AWS_IAM", "InvokeMode": invoke_mode,
           # No permissive CORS: the viewer talks to CloudFront on the console's
           # own origin, so the request is same-origin and needs no CORS grant.
           # Exact origins only (the API rejects "https://*.cloudfront.net"). The
           # browser reaches /stream same-origin via CloudFront, so this CORS grant
           # only matters for a direct local-dev call.
           "Cors": {"AllowOrigins": _allowed_origins(),
                    "AllowMethods": ["POST"],
                    "AllowHeaders": ["content-type", "x-retail-ops-token", "x-correlation-id"],
                    "MaxAge": 300}}
    if not apply:
        print(f"  [url] would ensure Function URL on {STREAM_FN} (InvokeMode={invoke_mode}, AuthType=AWS_IAM)")
        return "(url pending)"
    try:
        existing = lam.get_function_url_config(FunctionName=STREAM_FN)
        lam.update_function_url_config(**cfg)
        url = existing["FunctionUrl"]
        print(f"  [url] updated Function URL (InvokeMode={invoke_mode}, AuthType=AWS_IAM)")
    except lam.exceptions.ResourceNotFoundException:
        url = lam.create_function_url_config(**cfg)["FunctionUrl"]
        print(f"  [url] created Function URL (InvokeMode={invoke_mode}, AuthType=AWS_IAM)")

    # Remove any legacy world-accessible grant left by an older deploy, then
    # grant ONLY CloudFront (scoped to the distribution when we know it).
    try:
        lam.remove_permission(FunctionName=STREAM_FN, StatementId="floor-stream-url")
        print("  [url] removed legacy public invoke grant (AuthType NONE)")
    except Exception:
        pass
    if distribution_arn:
        try:
            lam.add_permission(FunctionName=STREAM_FN, StatementId="floor-stream-cloudfront",
                               Action="lambda:InvokeFunctionUrl", Principal="cloudfront.amazonaws.com",
                               SourceArn=distribution_arn, FunctionUrlAuthType="AWS_IAM")
            print("  [url] granted CloudFront (OAC, SigV4) invoke on the URL")
        except lam.exceptions.ResourceConflictException:
            print("  [url] CloudFront invoke grant already present")
    else:
        print("  [url] NOTE no distribution arn — run deploy_console --apply to attach the "
              "/stream/* origin and grant CloudFront; the URL stays IAM-only (not public) until then")
    _assert_not_public(lam)
    return url


def _assert_not_public(lam) -> None:
    """Fail loudly if this function is ever world-accessible again.

    Guard rail for the regression that tripped `lambda_function_policy_block_public_access`:
    the deployer refuses to finish in a state the account will auto-mitigate."""
    import json as _json
    u = lam.get_function_url_config(FunctionName=STREAM_FN)
    if u["AuthType"] != "AWS_IAM":
        raise SystemExit(f"ABORT {STREAM_FN}: Function URL AuthType is {u['AuthType']!r}; "
                         "only AWS_IAM is allowed (public URLs are auto-mitigated by the account).")
    try:
        pol = _json.loads(lam.get_policy(FunctionName=STREAM_FN)["Policy"])
    except Exception:
        return
    for st in pol.get("Statement", []):
        princ = st.get("Principal")
        wild = princ == "*" or (isinstance(princ, dict) and "*" in princ.values())
        if wild and st.get("Effect") == "Allow":
            raise SystemExit(f"ABORT {STREAM_FN}: resource policy statement {st.get('Sid')!r} allows "
                             "Principal '*' — that is a world-accessible Lambda and will be auto-mitigated.")
    print("  [guard] Function URL is IAM-only with no wildcard principal ✓")


def _console_distribution_arn() -> str | None:
    """The console CloudFront distribution that fronts this URL (console-config.json)."""
    import json as _json
    p = naming.config_path("console")
    if not p.exists():
        return None
    try:
        cfg = _json.loads(p.read_text())
        did, acct = cfg.get("distribution_id"), cfg.get("account")
        if not did:
            return None
        if not acct:
            import boto3 as _b
            acct = _b.client("sts").get_caller_identity()["Account"]
        return f"arn:aws:cloudfront::{acct}:distribution/{did}"
    except Exception:
        return None


def delete_stack(iam, lam) -> None:
    try:
        lam.delete_function_url_config(FunctionName=STREAM_FN)
        print("  deleted Function URL")
    except lam.exceptions.ResourceNotFoundException:
        pass
    try:
        lam.delete_function(FunctionName=STREAM_FN); print(f"  deleted lambda {STREAM_FN}")
    except lam.exceptions.ResourceNotFoundException:
        pass
    for p in (CONFIG_PATH,):
        if p.exists():
            p.unlink(); print(f"  removed {p.name}")
    try:
        for pol in iam.list_role_policies(RoleName=iam_roles.ROLE_STREAM)["PolicyNames"]:
            iam.delete_role_policy(RoleName=iam_roles.ROLE_STREAM, PolicyName=pol)
        iam.delete_role(RoleName=iam_roles.ROLE_STREAM)
        print(f"  deleted role {iam_roles.ROLE_STREAM}")
    except iam.exceptions.NoSuchEntityException:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the SSE streaming floor path (Function URL).")
    ap.add_argument("--apply", action="store_true", help="create/update (default: dry-run)")
    ap.add_argument("--delete", action="store_true", help="tear the stack down")
    ap.add_argument("--buffered", action="store_true",
                    help="provision the URL as BUFFERED (matches what the python3.12 "
                         "handler actually does; see the module docstring)")
    args = ap.parse_args(argv)

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    iam, lam = s.client("iam"), s.client("lambda")
    print(f"account={account} region={REGION} runtime={RUNTIME}")

    if args.delete:
        delete_stack(iam, lam); return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    invoke_mode = "BUFFERED" if args.buffered else "RESPONSE_STREAM"
    dist_arn = _console_distribution_arn()
    print(f"[{mode}] deploying {STREAM_FN}")
    role_arn = iam_roles.ensure_role(iam, account, iam_roles.ROLE_STREAM, args.apply)
    if args.apply:
        print("  [role] waiting for propagation…"); time.sleep(10)
    code = deploy_l4._zip_code()   # the same app zip the service Lambda runs
    print(f"  [pkg] app zip = {len(code)} bytes")
    deploy_l4.ensure_function(lam, STREAM_FN, HANDLER, role_arn, code, _env(), args.apply)
    url = ensure_function_url(lam, args.apply, invoke_mode, dist_arn)
    if args.apply:
        CONFIG_PATH.write_text(json.dumps({
            "function_name": STREAM_FN, "function_url": url, "invoke_mode": invoke_mode,
            "handler": HANDLER, "region": REGION, "auth": "in-process (authorizer.resolve_principal)",
            "transport": "server-sent events (event: token … event: result)",
            "python_streaming_caveat": "the managed python3.12 runtime has no response-streaming "
                                       "API, so the handler buffers the SSE document and sends it "
                                       "in one flush (ADR 0008)",
        }, indent=2) + "\n")
        print(f"  [config] wrote {CONFIG_PATH.name}")
    print(f"[{mode}] done. Function URL: {url}")
    print("  set NEXT_PUBLIC_RETAIL_OPS_STREAM_URL to this URL to turn the console's streaming path on")
    if not args.apply:
        print("  (re-run with --apply to create)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
