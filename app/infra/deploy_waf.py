"""AWS WAF (WAFv2) web ACL in front of the console's CloudFront distribution.

The console is a public CloudFront origin that staff sign in to through an
identity provider, so it needs an edge control plane in front of it. This
deployer creates one CLOUDFRONT-scoped web ACL and associates it with the
distribution `deploy_console` manages.

Rules, in evaluation order:

  1. allowlist      (only when CIDRs are configured) — anything outside the
                    allowed ranges is BLOCKED outright. This is how the pilot is
                    restricted to corporate ranges while an internal identity
                    provider is attached. Off by default so nobody locks
                    themselves out of a running demo by accident.
  2. rate limit     per-IP request ceiling, blocks floods
  3. IP reputation  AWS managed: known malicious sources
  4. common rules   AWS managed: broad OWASP-style protections
  5. bad inputs     AWS managed: known exploit payloads

Scope note: CLOUDFRONT web ACLs must live in us-east-1 (where this stack already
runs), and association is by distribution ARN.

Usage:
  python3 -m app.infra.deploy_waf                 # dry-run
  python3 -m app.infra.deploy_waf --apply         # create/update + associate
  python3 -m app.infra.deploy_waf --status
  python3 -m app.infra.deploy_waf --disassociate --apply   # detach, keep the ACL
  python3 -m app.infra.deploy_waf --delete --apply         # detach + delete the ACL

Restrict access to specific ranges (comma-separated IPv4/IPv6 CIDRs):
  RETAIL_OPS_WAF_ALLOW_CIDRS="203.0.113.0/24,198.51.100.7/32" \
    python3 -m app.infra.deploy_waf --apply

Writes app/infra/waf-config.json {web_acl_id, web_acl_arn, name, allow_cidrs}.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"          # required for CLOUDFRONT-scoped web ACLs
SCOPE = "CLOUDFRONT"
CONFIG_PATH = naming.config_path("waf")
CONSOLE_CONFIG = naming.config_path("console")

ACL_NAME = naming.name("console-waf")
IPSET_NAME = naming.name("console-allow")
RATE_LIMIT = int(os.environ.get("RETAIL_OPS_WAF_RATE_LIMIT", "2000"))  # per 5 min, per IP


def _allow_cidrs() -> list[str]:
    raw = os.environ.get("RETAIL_OPS_WAF_ALLOW_CIDRS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _console_dist() -> dict:
    """{id, arn, domain} for the console distribution, from console-config.json."""
    if not CONSOLE_CONFIG.exists():
        raise SystemExit("no console-config.json — deploy the console stage first")
    cfg = json.loads(CONSOLE_CONFIG.read_text())
    dist_id = cfg.get("distribution_id")
    if not dist_id:
        raise SystemExit("console-config.json has no distribution_id")
    return {"id": dist_id, "domain": cfg.get("domain", ""), "url": cfg.get("url", "")}


# --- rules ------------------------------------------------------------------

def _managed(name: str, vendor: str = "AWS") -> dict:
    return {"ManagedRuleGroupStatement": {"VendorName": vendor, "Name": name}}


def _visibility(metric: str) -> dict:
    return {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True,
            "MetricName": metric}


def build_rules(ipset_arn: str | None) -> list[dict]:
    rules: list[dict] = []
    prio = 0
    if ipset_arn:
        # Deny-by-default: block every request whose source is NOT in the set.
        rules.append({
            "Name": "allowlist-only",
            "Priority": prio,
            "Statement": {"NotStatement": {"Statement": {
                "IPSetReferenceStatement": {"ARN": ipset_arn}}}},
            "Action": {"Block": {}},
            "VisibilityConfig": _visibility("allowlistOnly"),
        })
        prio += 1
    rules.append({
        "Name": "rate-limit",
        "Priority": prio,
        "Statement": {"RateBasedStatement": {"Limit": RATE_LIMIT, "AggregateKeyType": "IP"}},
        "Action": {"Block": {}},
        "VisibilityConfig": _visibility("rateLimit"),
    })
    prio += 1
    for name, metric in (("AWSManagedRulesAmazonIpReputationList", "ipReputation"),
                         ("AWSManagedRulesCommonRuleSet", "commonRules"),
                         ("AWSManagedRulesKnownBadInputsRuleSet", "badInputs")):
        rules.append({
            "Name": name,
            "Priority": prio,
            "Statement": _managed(name),
            "OverrideAction": {"None": {}},     # managed groups use OverrideAction
            "VisibilityConfig": _visibility(metric),
        })
        prio += 1
    return rules


# --- ip set -----------------------------------------------------------------

def _find(lister, key: str, name: str) -> dict | None:
    token = None
    while True:
        kw = {"Scope": SCOPE, "Limit": 100}
        if token:
            kw["NextMarker"] = token
        page = lister(**kw)
        for item in page.get(key, []):
            if item["Name"] == name:
                return item
        token = page.get("NextMarker")
        if not token or not page.get(key):
            return None


def ensure_ipset(waf, cidrs: list[str], apply: bool) -> str | None:
    """The allowlist IP set, or None when no CIDRs are configured."""
    existing = _find(waf.list_ip_sets, "IPSets", IPSET_NAME)
    if not cidrs:
        if existing and apply:
            # allowlist turned off: empty the set so a stale list can't block
            got = waf.get_ip_set(Name=IPSET_NAME, Scope=SCOPE, Id=existing["Id"])
            waf.update_ip_set(Name=IPSET_NAME, Scope=SCOPE, Id=existing["Id"],
                              Addresses=[], LockToken=got["LockToken"])
            print(f"  [waf] allowlist disabled — emptied IP set {IPSET_NAME}")
        return None
    version = "IPV4" if all(":" not in c for c in cidrs) else "IPV6"
    if any(":" in c for c in cidrs) and not all(":" in c for c in cidrs):
        raise SystemExit("RETAIL_OPS_WAF_ALLOW_CIDRS must be all IPv4 or all IPv6 "
                         "(WAF IP sets are single-family)")
    if not apply:
        print(f"  [dry-run] would ensure IP set {IPSET_NAME} ({version}) with {len(cidrs)} CIDR(s)")
        return "(ipset pending)"
    if existing:
        got = waf.get_ip_set(Name=IPSET_NAME, Scope=SCOPE, Id=existing["Id"])
        waf.update_ip_set(Name=IPSET_NAME, Scope=SCOPE, Id=existing["Id"],
                          Addresses=cidrs, LockToken=got["LockToken"])
        print(f"  [waf] updated IP set {IPSET_NAME}: {cidrs}")
        return existing["ARN"]
    r = waf.create_ip_set(Name=IPSET_NAME, Scope=SCOPE, IPAddressVersion=version,
                          Addresses=cidrs,
                          Description="Retail Ops console allowlist (deny all others)")
    print(f"  [waf] created IP set {IPSET_NAME}: {cidrs}")
    return r["Summary"]["ARN"]


# --- web acl ----------------------------------------------------------------

def ensure_acl(waf, rules: list[dict], apply: bool) -> dict:
    existing = _find(waf.list_web_acls, "WebACLs", ACL_NAME)
    if not apply:
        verb = "update" if existing else "create"
        print(f"  [dry-run] would {verb} web ACL {ACL_NAME} with {len(rules)} rule(s): "
              f"{', '.join(r['Name'] for r in rules)}")
        return existing or {}
    args = dict(Name=ACL_NAME, Scope=SCOPE, DefaultAction={"Allow": {}},
                Rules=rules, VisibilityConfig=_visibility("retailOpsConsoleWaf"))
    if existing:
        got = waf.get_web_acl(Name=ACL_NAME, Scope=SCOPE, Id=existing["Id"])
        waf.update_web_acl(Id=existing["Id"], LockToken=got["LockToken"], **args)
        print(f"  [waf] updated web ACL {ACL_NAME} ({existing['Id']})")
        return waf.get_web_acl(Name=ACL_NAME, Scope=SCOPE, Id=existing["Id"])["WebACL"]
    r = waf.create_web_acl(Description="Edge protection for the Retail Ops console", **args)
    print(f"  [waf] created web ACL {ACL_NAME} ({r['Summary']['Id']})")
    return r["Summary"]


def _wait_deployed(cf, dist_id: str, tries: int = 60) -> None:
    for _ in range(tries):
        if cf.get_distribution(Id=dist_id)["Distribution"]["Status"] == "Deployed":
            return
        time.sleep(10)
    print("  [cf] still deploying (safe to leave; propagation continues)")


def associate(cf, dist_id: str, acl_arn: str | None, apply: bool) -> None:
    """Attach (or detach, with acl_arn=None) the web ACL on the distribution."""
    got = cf.get_distribution_config(Id=dist_id)
    dc, etag = got["DistributionConfig"], got["ETag"]
    current = dc.get("WebACLId") or ""
    target = acl_arn or ""
    if current == target:
        print(f"  [cf] distribution {dist_id} web ACL already "
              f"{'attached' if target else 'detached'}")
        return
    if not apply:
        print(f"  [dry-run] would {'attach' if target else 'detach'} the web ACL "
              f"on distribution {dist_id}")
        return
    dc["WebACLId"] = target
    cf.update_distribution(Id=dist_id, IfMatch=etag, DistributionConfig=dc)
    print(f"  [cf] {'attached' if target else 'detached'} web ACL on {dist_id}")
    _wait_deployed(cf, dist_id)


# --- entry points -----------------------------------------------------------

def apply_stack(apply: bool) -> dict:
    import boto3
    waf = boto3.client("wafv2", region_name=REGION)
    cf = boto3.client("cloudfront")
    dist = _console_dist()
    cidrs = _allow_cidrs()
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] Retail Ops console WAF (scope={SCOPE}, region={REGION})")
    print(f"  target: distribution {dist['id']} {dist['url']}")
    if cidrs:
        print(f"  allowlist ACTIVE — only {len(cidrs)} CIDR(s) may reach the console")
    else:
        print("  allowlist off — managed rules + rate limit only "
              "(set RETAIL_OPS_WAF_ALLOW_CIDRS to restrict by source IP)")

    ipset_arn = ensure_ipset(waf, cidrs, apply)
    rules = build_rules(ipset_arn)
    acl = ensure_acl(waf, rules, apply)
    acl_arn = acl.get("ARN")
    if apply and acl_arn:
        associate(cf, dist["id"], acl_arn, apply)
        out = {"region": REGION, "scope": SCOPE, "name": ACL_NAME,
               "web_acl_id": acl.get("Id"), "web_acl_arn": acl_arn,
               "distribution_id": dist["id"], "allow_cidrs": cidrs,
               "rate_limit_per_5min": RATE_LIMIT,
               "rules": [r["Name"] for r in rules]}
        CONFIG_PATH.write_text(json.dumps(out, indent=2) + "\n")
        print(f"[APPLY] done. web ACL {ACL_NAME} protecting {dist['url']}")
        return out
    if not apply:
        associate(cf, dist["id"], "(pending)", apply)
        print("[DRY-RUN] done.  (re-run with --apply)")
    return {}


def status() -> None:
    import boto3
    waf = boto3.client("wafv2", region_name=REGION)
    cf = boto3.client("cloudfront")
    dist = _console_dist()
    acl = _find(waf.list_web_acls, "WebACLs", ACL_NAME)
    if not acl:
        print(f"web ACL {ACL_NAME}: not created")
    else:
        full = waf.get_web_acl(Name=ACL_NAME, Scope=SCOPE, Id=acl["Id"])["WebACL"]
        print(f"web ACL {ACL_NAME} ({acl['Id']})")
        print(f"  default action: {list(full['DefaultAction'])[0]}")
        for r in full.get("Rules", []):
            act = list(r.get("Action") or r.get("OverrideAction") or {"None": {}})[0]
            print(f"  - {r['Name']} (priority {r['Priority']}, {act})")
    ips = _find(waf.list_ip_sets, "IPSets", IPSET_NAME)
    if ips:
        got = waf.get_ip_set(Name=IPSET_NAME, Scope=SCOPE, Id=ips["Id"])["IPSet"]
        print(f"allowlist {IPSET_NAME}: {got['Addresses'] or '(empty — not enforcing)'}")
    attached = cf.get_distribution_config(Id=dist["id"])["DistributionConfig"].get("WebACLId") or ""
    print(f"distribution {dist['id']}: web ACL {'attached: ' + attached if attached else 'NOT attached'}")


def delete_stack(apply: bool, detach_only: bool = False) -> None:
    import boto3
    waf = boto3.client("wafv2", region_name=REGION)
    cf = boto3.client("cloudfront")
    dist = _console_dist()
    associate(cf, dist["id"], None, apply)
    if detach_only:
        print("  web ACL left in place (re-attach with --apply)")
        return
    acl = _find(waf.list_web_acls, "WebACLs", ACL_NAME)
    if not acl:
        print("  no web ACL to delete")
    elif not apply:
        print(f"  [dry-run] would delete web ACL {ACL_NAME}")
    else:
        # CloudFront must finish propagating the detach before WAF will release it
        for attempt in range(12):
            got = waf.get_web_acl(Name=ACL_NAME, Scope=SCOPE, Id=acl["Id"])
            try:
                waf.delete_web_acl(Name=ACL_NAME, Scope=SCOPE, Id=acl["Id"],
                                   LockToken=got["LockToken"])
                print(f"  [waf] deleted web ACL {ACL_NAME}")
                break
            except waf.exceptions.WAFAssociatedItemException:
                if attempt == 11:
                    print("  [waf] still associated — retry once CloudFront finishes deploying")
                    break
                time.sleep(15)
    ips = _find(waf.list_ip_sets, "IPSets", IPSET_NAME)
    if ips and apply:
        got = waf.get_ip_set(Name=IPSET_NAME, Scope=SCOPE, Id=ips["Id"])
        try:
            waf.delete_ip_set(Name=IPSET_NAME, Scope=SCOPE, Id=ips["Id"],
                              LockToken=got["LockToken"])
            print(f"  [waf] deleted IP set {IPSET_NAME}")
        except Exception as e:  # noqa: BLE001 - referenced by a surviving ACL
            print(f"  [waf] IP set not deleted: {e}")
    if apply and CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WAF web ACL for the Retail Ops console CloudFront distribution.")
    ap.add_argument("--apply", action="store_true", help="perform real changes (default: dry-run)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--disassociate", action="store_true", help="detach from CloudFront, keep the ACL")
    ap.add_argument("--delete", action="store_true", help="detach and delete the ACL + IP set")
    args = ap.parse_args(argv)
    if args.status:
        status(); return 0
    if args.delete or args.disassociate:
        delete_stack(args.apply, detach_only=args.disassociate and not args.delete)
        return 0
    apply_stack(args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
