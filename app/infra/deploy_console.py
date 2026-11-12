#!/usr/bin/env python3
"""Deploy the Retail Ops operator console (Next.js static export) to S3 + CloudFront.

One distribution serves the whole console; the stakeholder surfaces (OEM
Workspace, Dealer Console, Service Desk, Admin, Customer Garage) are **screens
within the same SPA**, gated by persona token at the API — not separate sites
(spec §5 apps model). So this provisions a single private S3 origin fronted by
CloudFront with Origin Access Control (OAC), uploads the static build, and
invalidates the cache.

  1. S3 bucket  retail-ops-<env>-console-<account>  (private; OAC-only access)
  2. CloudFront Origin Access Control + distribution, with a viewer-request
     CloudFront Function that (a) maps `/a/b` -> `/a/b/index.html` (the
     trailing-slash export) and (b) rewrites dynamic routes (`/dealer/advisor/<id>`)
     onto the single generated placeholder page (`/dealer/advisor/_/index.html`);
     the page reads the real id from the browser path. Templates are discovered
     from the build (every `_` directory in out/). 404 -> /index.html remains the
     last-resort fallback.
  3. bucket policy allowing only this distribution to read
  4. upload app/console/out/ (built with RETAIL_OPS_STATIC_EXPORT=1) + invalidate

Build the bundle first (this deployer does NOT build):
  cd app/console && rm -rf out && \
    RETAIL_OPS_STATIC_EXPORT=1 NEXT_PUBLIC_ENABLE_QUICK=true \
    NEXT_PUBLIC_RETAIL_OPS_API=<api-base> npm run build

Then:
  python3 -m app.infra.deploy_console --apply     # create/update + upload + invalidate
  python3 -m app.infra.deploy_console --status     # print distribution domain + state
  python3 -m app.infra.deploy_console --delete     # tear down (disable+delete dist, empty+remove bucket)

Writes app/infra/console-config.json {bucket, distribution_id, domain, url}.
Idempotent: reuses the bucket + distribution by a stable caller-reference tag.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import time
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
OUT_DIR = ROOT / "app" / "console" / "out"
CONFIG_PATH = naming.config_path("console")

OAC_NAME = naming.name("console-oac")
DIST_COMMENT = f"Retail Ops operator console ({naming.ENV})"
CALLER_REF = naming.name("console")   # stable id so we find our own distribution
REWRITE_FN = naming.name("console-rewrite")


def dynamic_templates(out_dir: Path = OUT_DIR) -> list[str]:
    """Route templates with a placeholder segment, e.g. '/dealer/advisor/_' and
    '/dealer/service/_/estimate' (every page whose path has a `_` segment)."""
    out = []
    for f in sorted(out_dir.rglob("index.html")):
        rel = "/" + str(f.parent.relative_to(out_dir)).replace("\\", "/")
        if "_" in rel.split("/"):
            out.append(rel)
    # most specific first so /dealer/service/_/estimate wins over /dealer/service/_
    return sorted(out, key=lambda t: (-t.count("/"), t))


def static_routes(out_dir: Path = OUT_DIR) -> list[str]:
    """Every concrete page path (static pages win over dynamic templates)."""
    out = []
    for f in sorted(out_dir.rglob("index.html")):
        rel = "/" + str(f.parent.relative_to(out_dir)).replace("\\", "/")
        rel = "/" if rel == "/." else rel
        if "_" not in rel.split("/"):          # placeholder pages are templates
            out.append(rel.rstrip("/") or "/")
    return out


def rewrite_function_code(templates: list[str], statics: list[str] | None = None) -> str:
    """CloudFront Function (cloudfront-js-2.0) source for the viewer request."""
    pats = []
    for t in templates:
        parts = t.strip("/").split("/")
        rx = "^/" + "/".join("[^/]+" if p == "_" else p.replace(".", "\\.") for p in parts) + "/?$"
        pats.append({"re": rx, "to": t})
    return r"""function handler(event) {
  var req = event.request;
  var uri = req.uri;
  var routes = %s;
  var statics = %s;
  if (uri.indexOf('.') !== -1 && uri.lastIndexOf('.') > uri.lastIndexOf('/')) { return req; }
  var bare = uri.length > 1 ? uri.replace(/\/$/, '') : uri;
  if (statics.indexOf(bare) !== -1) { req.uri = (bare === '/' ? '' : bare) + '/index.html'; return req; }
  for (var i = 0; i < routes.length; i++) {
    if (new RegExp(routes[i].re).test(uri)) { req.uri = routes[i].to + '/index.html'; return req; }
  }
  req.uri = uri.replace(/\/?$/, '/') + 'index.html';
  return req;
}""" % (json.dumps(pats), json.dumps(statics or []))


def _ensure_rewrite_function(cf, apply: bool) -> str | None:
    templates = dynamic_templates()
    code = rewrite_function_code(templates, static_routes()).encode()
    if not apply:
        print(f"  [dry-run] would publish CloudFront Function {REWRITE_FN} ({len(templates)} dynamic routes)")
        return None
    cfg = {"Comment": "Retail Ops console URL rewrite (index.html + dynamic routes)", "Runtime": "cloudfront-js-2.0"}
    try:
        cur = cf.describe_function(Name=REWRITE_FN, Stage="DEVELOPMENT")
        etag = cf.update_function(Name=REWRITE_FN, IfMatch=cur["ETag"], FunctionConfig=cfg, FunctionCode=code)["ETag"]
    except cf.exceptions.NoSuchFunctionExists:
        etag = cf.create_function(Name=REWRITE_FN, FunctionConfig=cfg, FunctionCode=code)["ETag"]
    pub = cf.publish_function(Name=REWRITE_FN, IfMatch=etag)
    arn = pub["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]
    print(f"  [cf] published {REWRITE_FN} ({len(templates)} dynamic routes)")
    return arn


def _function_associations(fn_arn: str | None) -> dict:
    if not fn_arn:
        return {"Quantity": 0}
    return {"Quantity": 1, "Items": [{"FunctionARN": fn_arn, "EventType": "viewer-request"}]}


def _bucket(account: str) -> str:
    return naming.name(f"console-{account}")


# --- S3 ---------------------------------------------------------------------

def _ensure_bucket(s3, account: str, apply: bool) -> str:
    name = _bucket(account)
    if not apply:
        print(f"  [dry-run] would ensure private bucket {name}")
        return name
    try:
        s3.head_bucket(Bucket=name)
        print(f"  [s3] bucket {name} exists")
    except Exception:
        s3.create_bucket(Bucket=name)  # us-east-1: no LocationConstraint
        print(f"  [s3] created {name}")
    s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    return name


def _upload(s3, bucket: str) -> int:
    if not OUT_DIR.is_dir():
        raise SystemExit(f"ERROR: {OUT_DIR} not found — build the console first "
                         "(RETAIL_OPS_STATIC_EXPORT=1 ... npm run build in app/console).")
    n = 0
    for f in OUT_DIR.rglob("*"):
        if not f.is_file():
            continue
        key = str(f.relative_to(OUT_DIR))
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        # Hashed _next assets are immutable; HTML must revalidate.
        cache = "public,max-age=31536000,immutable" if key.startswith("_next/") \
            else "public,max-age=0,must-revalidate"
        s3.put_object(Bucket=bucket, Key=key, Body=f.read_bytes(),
                      ContentType=ctype, CacheControl=cache)
        n += 1
    print(f"  [s3] uploaded {n} objects from {OUT_DIR.relative_to(ROOT)}")
    return n


# --- CloudFront OAC + distribution -----------------------------------------

def _ensure_oac(cf, apply: bool) -> str:
    for item in cf.list_origin_access_controls().get("OriginAccessControlList", {}).get("Items", []):
        if item.get("Name") == OAC_NAME:
            return item["Id"]
    if not apply:
        print(f"  [dry-run] would create OAC {OAC_NAME}")
        return ""
    resp = cf.create_origin_access_control(OriginAccessControlConfig={
        "Name": OAC_NAME, "SigningProtocol": "sigv4", "SigningBehavior": "always",
        "OriginAccessControlOriginType": "s3",
        "Description": "Retail Ops console S3 origin access"})
    oac_id = resp["OriginAccessControl"]["Id"]
    print(f"  [cf] created OAC {OAC_NAME} ({oac_id})")
    return oac_id


def _find_distribution(cf) -> dict | None:
    paginator = cf.get_paginator("list_distributions")
    for page in paginator.paginate():
        for d in (page.get("DistributionList", {}) or {}).get("Items", []) or []:
            if d.get("Comment") == DIST_COMMENT:
                return d
    return None


def _dist_config(bucket: str, oac_id: str, fn_arn: str | None = None) -> dict:
    origin_domain = f"{bucket}.s3.{REGION}.amazonaws.com"
    return {
        "CallerReference": CALLER_REF,
        "Comment": DIST_COMMENT,
        "Enabled": True,
        "DefaultRootObject": "index.html",
        "Origins": {"Quantity": 1, "Items": [{
            "Id": "s3-console",
            "DomainName": origin_domain,
            "OriginAccessControlId": oac_id,
            "S3OriginConfig": {"OriginAccessIdentity": ""},
        }]},
        "DefaultCacheBehavior": {
            "TargetOriginId": "s3-console",
            "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
                               "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
            "Compress": True,
            # CachingOptimized managed policy.
            "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
            "FunctionAssociations": _function_associations(fn_arn),
        },
        # SPA routing: trailingSlash export means routes are dir/index.html; a
        # bare 403/404 from S3 falls back to the app shell so client routing works.
        "CustomErrorResponses": {"Quantity": 2, "Items": [
            {"ErrorCode": 403, "ResponseCode": "200", "ResponsePagePath": "/index.html",
             "ErrorCachingMinTTL": 10},
            {"ErrorCode": 404, "ResponseCode": "200", "ResponsePagePath": "/index.html",
             "ErrorCachingMinTTL": 10},
        ]},
        "PriceClass": "PriceClass_100",
    }


def _ensure_distribution(cf, bucket: str, oac_id: str, apply: bool, fn_arn: str | None = None) -> dict:
    existing = _find_distribution(cf)
    if not apply:
        print(f"  [dry-run] would ensure CloudFront distribution ({DIST_COMMENT})")
        return existing or {}
    if existing:
        print(f"  [cf] distribution {existing['Id']} exists ({existing['DomainName']})")
        if fn_arn:
            got = cf.get_distribution_config(Id=existing["Id"])
            dc = got["DistributionConfig"]
            dc["DefaultCacheBehavior"]["FunctionAssociations"] = _function_associations(fn_arn)
            cf.update_distribution(Id=existing["Id"], IfMatch=got["ETag"], DistributionConfig=dc)
            print("  [cf] attached URL-rewrite function")
        return cf.get_distribution(Id=existing["Id"])["Distribution"]
    resp = cf.create_distribution(DistributionConfig=_dist_config(bucket, oac_id, fn_arn))
    dist = resp["Distribution"]
    print(f"  [cf] created distribution {dist['Id']} ({dist['DomainName']})")
    return dist


# ---------------------------------------------------------------------------
# /stream/* — the floor-streaming Lambda, reached SAME-ORIGIN through this
# distribution with Origin Access Control.
#
# Why this exists: a public Lambda Function URL (AuthType NONE + Principal "*")
# is a world-accessible entry point, and the account's
# `lambda_function_policy_block_public_access` control auto-mitigates it by
# stripping the resource policy — so the URL 403s regardless of what our handler
# does. Fronting it here keeps the URL AuthType=AWS_IAM: CloudFront signs every
# request SigV4 with OAC, and the only invoke grant is CloudFront's service
# principal scoped to this distribution. The browser just calls
# `/stream/journeys/{id}/utterance` on the console's own origin.
#
# OAC overwrites the viewer's `Authorization` header with its signature, so the
# app's token travels in `X-Retail-Ops-Token` (stream_handler reads that first).
STREAM_OAC_NAME = naming.name("stream-oac")
STREAM_ORIGIN_ID = "lambda-floor-stream"
STREAM_PATH = "/stream/*"
# Managed policies: AllViewerExceptHostHeader forwards headers/body but lets OAC
# re-sign; CachingDisabled because an SSE response must never be cached.
_ORP_ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
_CP_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"


def _stream_fn_domain() -> str | None:
    """Host of the streaming Function URL from streaming-config.json."""
    p = naming.config_path("streaming")
    if not p.exists():
        return None
    try:
        url = json.loads(p.read_text()).get("function_url") or ""
        return url.split("://", 1)[-1].strip("/") or None
    except Exception:
        return None


def _ensure_stream_oac(cf, apply: bool) -> str | None:
    for item in cf.list_origin_access_controls().get("OriginAccessControlList", {}).get("Items", []):
        if item.get("Name") == STREAM_OAC_NAME:
            return item["Id"]
    if not apply:
        print(f"  [dry-run] would create OAC {STREAM_OAC_NAME} (lambda origin type)")
        return None
    resp = cf.create_origin_access_control(OriginAccessControlConfig={
        "Name": STREAM_OAC_NAME, "SigningProtocol": "sigv4", "SigningBehavior": "always",
        "OriginAccessControlOriginType": "lambda",
        "Description": "SigV4-signs console -> floor-stream Function URL (keeps it non-public)",
    })
    oac_id = resp["OriginAccessControl"]["Id"]
    print(f"  [cf] created OAC {STREAM_OAC_NAME} ({oac_id})")
    return oac_id


def ensure_stream_origin(cf, lam, dist_id: str, account: str, apply: bool) -> bool:
    """Add/refresh the /stream/* origin + behavior and grant CloudFront invoke."""
    domain = _stream_fn_domain()
    if not domain:
        print("  [stream] no streaming-config.json — skipping /stream/* "
              "(run deploy_streaming --apply first)")
        return False
    oac_id = _ensure_stream_oac(cf, apply)
    if not apply or not oac_id:
        print(f"  [dry-run] would front {domain} at {STREAM_PATH} with OAC")
        return False

    got = cf.get_distribution_config(Id=dist_id)
    dc, etag = got["DistributionConfig"], got["ETag"]
    origins = dc["Origins"]["Items"]
    origins = [o for o in origins if o["Id"] != STREAM_ORIGIN_ID]
    origins.append({
        "Id": STREAM_ORIGIN_ID, "DomainName": domain, "OriginAccessControlId": oac_id,
        "CustomOriginConfig": {"HTTPPort": 80, "HTTPSPort": 443,
                               "OriginProtocolPolicy": "https-only",
                               "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                               "OriginReadTimeout": 60, "OriginKeepaliveTimeout": 5},
        "OriginPath": "", "CustomHeaders": {"Quantity": 0},
    })
    dc["Origins"] = {"Quantity": len(origins), "Items": origins}

    behaviors = [b for b in (dc.get("CacheBehaviors") or {}).get("Items", [])
                 if b.get("PathPattern") != STREAM_PATH]
    behaviors.append({
        "PathPattern": STREAM_PATH, "TargetOriginId": STREAM_ORIGIN_ID,
        "ViewerProtocolPolicy": "https-only",
        "AllowedMethods": {"Quantity": 7,
                           "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                           "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
        "Compress": False,                     # SSE must not be buffered by compression
        "CachePolicyId": _CP_CACHING_DISABLED,
        "OriginRequestPolicyId": _ORP_ALL_VIEWER_EXCEPT_HOST,
        "FunctionAssociations": {"Quantity": 0},
        # UpdateDistribution requires these explicitly on a cache behavior.
        "SmoothStreaming": False,
        "FieldLevelEncryptionId": "",
        "LambdaFunctionAssociations": {"Quantity": 0},
        "TrustedSigners": {"Enabled": False, "Quantity": 0},
        "TrustedKeyGroups": {"Enabled": False, "Quantity": 0},
    })
    dc["CacheBehaviors"] = {"Quantity": len(behaviors), "Items": behaviors}
    cf.update_distribution(Id=dist_id, IfMatch=etag, DistributionConfig=dc)
    print(f"  [cf] {STREAM_PATH} -> {domain} (OAC sigv4, caching disabled)")

    dist_arn = f"arn:aws:cloudfront::{account}:distribution/{dist_id}"
    try:
        lam.add_permission(FunctionName=naming.name("floor-stream"), StatementId="floor-stream-cloudfront",
                           Action="lambda:InvokeFunctionUrl", Principal="cloudfront.amazonaws.com",
                           SourceArn=dist_arn, FunctionUrlAuthType="AWS_IAM")
        print("  [stream] granted CloudFront invoke (scoped to this distribution)")
    except lam.exceptions.ResourceConflictException:
        print("  [stream] CloudFront invoke grant already present")
    return True


def _bucket_policy_for_dist(s3, bucket: str, account: str, dist_arn: str) -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowCloudFrontOAC", "Effect": "Allow",
            "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{bucket}/*",
            "Condition": {"StringEquals": {"AWS:SourceArn": dist_arn}},
        }],
    }
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
    print("  [s3] bucket policy grants this distribution read")


def _invalidate(cf, dist_id: str) -> None:
    cf.create_invalidation(DistributionId=dist_id, InvalidationBatch={
        "Paths": {"Quantity": 1, "Items": ["/*"]},
        "CallerReference": str(int(time.time()))})
    print("  [cf] invalidation /* created")


# --- orchestration ----------------------------------------------------------

def apply_stack(apply: bool) -> dict:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=REGION)
    cf = boto3.client("cloudfront", region_name=REGION)

    bucket = _ensure_bucket(s3, account, apply)
    oac_id = _ensure_oac(cf, apply)
    fn_arn = _ensure_rewrite_function(cf, apply)
    dist = _ensure_distribution(cf, bucket, oac_id, apply, fn_arn)
    dist_id = dist.get("Id", "")
    domain = dist.get("DomainName", "")

    if apply and dist_id:
        dist_arn = f"arn:aws:cloudfront::{account}:distribution/{dist_id}"
        _bucket_policy_for_dist(s3, bucket, account, dist_arn)
        # /stream/* : front the floor-streaming Function URL same-origin with OAC
        # so it never has to be public (see ensure_stream_origin's docstring).
        ensure_stream_origin(cf, boto3.client("lambda", region_name=REGION),
                             dist_id, account, apply)
        _upload(s3, bucket)
        _invalidate(cf, dist_id)

    url = f"https://{domain}" if domain else ""
    cfg = {"region": REGION, "bucket": bucket, "distribution_id": dist_id,
           "domain": domain, "url": url, "account": account,
           # the console's own origin serves the SSE path, so no public Lambda URL
           "stream_path": "/stream"}
    if apply:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"  [config] wrote {CONFIG_PATH.name}")
        print(f"\n  Console URL: {url}")
        print("  (CloudFront takes ~5-15 min to fully propagate on first create.)")
    return cfg


def status() -> None:
    import boto3
    cf = boto3.client("cloudfront", region_name=REGION)
    d = _find_distribution(cf)
    if not d:
        print("console: NOT PROVISIONED")
        return
    full = cf.get_distribution(Id=d["Id"])["Distribution"]
    print(f"console: {d['Id']} status={full['Status']} enabled={full['DistributionConfig']['Enabled']}")
    print(f"  url: https://{d['DomainName']}")


def delete_stack() -> None:
    import boto3
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=REGION)
    cf = boto3.client("cloudfront", region_name=REGION)
    d = _find_distribution(cf)
    if d:
        dist_id = d["Id"]
        # must disable + wait before delete
        cfg = cf.get_distribution_config(Id=dist_id)
        etag, dc = cfg["ETag"], cfg["DistributionConfig"]
        if dc["Enabled"]:
            dc["Enabled"] = False
            cf.update_distribution(Id=dist_id, IfMatch=etag, DistributionConfig=dc)
            print(f"  disabling distribution {dist_id} (waiting for Deployed)…")
            cf.get_waiter("distribution_deployed").wait(Id=dist_id)
            etag = cf.get_distribution_config(Id=dist_id)["ETag"]
        cf.delete_distribution(Id=dist_id, IfMatch=etag)
        print(f"  deleted distribution {dist_id}")
    bucket = _bucket(account)
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objs})
        s3.delete_bucket(Bucket=bucket)
        print(f"  deleted bucket {bucket}")
    except Exception:
        pass
    for item in cf.list_origin_access_controls().get("OriginAccessControlList", {}).get("Items", []):
        if item.get("Name") == OAC_NAME:
            try:
                oac = cf.get_origin_access_control(Id=item["Id"])
                cf.delete_origin_access_control(Id=item["Id"], IfMatch=oac["ETag"])
                print(f"  deleted OAC {OAC_NAME}")
            except Exception:
                pass
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy the Retail Ops console to S3 + CloudFront")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(); return
    if args.delete:
        delete_stack(); return
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Retail Ops console (S3 + CloudFront)")
    cfg = apply_stack(args.apply)
    print(f"[{mode}] done. url={cfg.get('url')}")


if __name__ == "__main__":
    main()
