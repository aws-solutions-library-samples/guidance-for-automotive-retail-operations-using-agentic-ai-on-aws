"""AWS Quick — plug-and-play A6/A5 integration (spec retail-ops-quick-project.md).

CORRECTION: AWS Quick IS the `quicksight` service (Quick evolved from
QuickSight; "AWS Quick Sight" is a feature within Quick, and QuickSight APIs
continue to work). The prior Amazon Q Business (`qapps`) build targeted the
DEPRECATED product and is removed.

Feature-flagged provider over the `quicksight` client:
  list_flows()          -> Quick Flows (ListFlows) — the A6 Flow surface
  list_dashboards()     -> Quick Sight dashboards (A5 analytics)
  embed_url(dashboard)  -> registered-user embed URL for a dashboard

  ENABLE_QUICK=false (default) -> DisabledQuick: structured "not enabled"; no API
      call, no behavior change (safe to ship dark).
  ENABLE_QUICK=true            -> QuickClient: real quicksight calls.

Quick Flows / dashboards / connectors live in AWS Quick on their own lifecycle
(spec §7): deploy_quick_content.py creates them where the API allows, the rest is
built in the Quick console; this provider consumes them. quick-config.json
(written by deploy_quick.py) supplies the embed user + discovered resources.
Embed URLs carry AllowedDomains from RETAIL_OPS_QUICK_EMBED_DOMAINS (set by the
l4 stage to the console URL), so no Quick-console domain allowlist is needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..common import envname

# AWS Quick groups (namespace `default`). They exist to SHARE the
# console-authored content — space, topic, knowledge bases, connector, A6 agent,
# Flows, A5 dashboard. Quick content is not row-scoped (no row-level security,
# spec §13 2026-10-21), so a member sees every row of what is shared with their
# group. Created by `deploy_quick_groups --apply`; this module is their single
# source of truth.
QUICK_BUILDERS_GROUP = "retail-ops-builders"
QUICK_GROUP_BY_ROLE: dict[str, str] = {
    "oem_dm": "retail-ops-oem-dm",
    "oem_regional": "retail-ops-oem-regional",
    "oem_ci_planning": "retail-ops-oem-ci-planning",
    "oem_marketing": "retail-ops-oem-marketing",
    # Finance and CX land on the marketing (A5) screens, so they need the same
    # dashboard share as marketing analysts.
    "oem_finance": "retail-ops-oem-marketing",
    "oem_cx": "retail-ops-oem-marketing",
    # The Administrator (SSO allowlist, every staff module) opens all shared
    # content, so it joins the builders group as a reader.
    "platform_admin": QUICK_BUILDERS_GROUP,
}
QUICK_GROUPS: tuple[str, ...] = tuple(dict.fromkeys((QUICK_BUILDERS_GROUP, *QUICK_GROUP_BY_ROLE.values())))


def enabled() -> bool:
    return os.environ.get("ENABLE_QUICK", "false").lower() == "true"


# GenerateEmbedUrlForRegisteredUser accepts at most three allowed domains.
EMBED_DOMAINS_ENV = "RETAIL_OPS_QUICK_EMBED_DOMAINS"
MAX_EMBED_DOMAINS = 3


def embed_domains() -> list[str]:
    """The console origins an embed URL may load in (comma-separated
    RETAIL_OPS_QUICK_EMBED_DOMAINS, written by the l4 stage from the console
    deploy's URL). Only https origins are kept; empty means Quick falls back to
    the domains allowed in the Quick admin console."""
    raw = os.environ.get(EMBED_DOMAINS_ENV, "")
    out = [d.strip().rstrip("/") for d in raw.split(",") if d.strip().startswith("https://")]
    return list(dict.fromkeys(out))[:MAX_EMBED_DOMAINS]


def _region() -> str:
    return os.environ.get("AWS_REGION", "us-east-1")


def _config() -> dict:
    cfg_path = envname.config_file(Path(__file__).resolve().parents[3] / "app" / "infra", "quick")
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text())
        except Exception:
            return {}
    return {}


class QuickProvider:
    def list_flows(self) -> dict[str, Any]:
        raise NotImplementedError

    def list_dashboards(self) -> dict[str, Any]:
        raise NotImplementedError

    def embed_url(self, dashboard: str, caller: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError


class DisabledQuick(QuickProvider):
    """Default: AWS Quick not enabled. No quicksight API call."""

    def _off(self, op: str) -> dict[str, Any]:
        return {"enabled": False, "op": op,
                "note": "AWS Quick is not enabled (set ENABLE_QUICK=true). AWS Quick "
                        "is the quicksight service; Flows/dashboards are authored in the "
                        "AWS Quick console and discovered by deploy_quick.py.",
                "source_ref": "platform.DisabledQuick"}

    def list_flows(self) -> dict[str, Any]:
        return self._off("list_flows")

    def list_dashboards(self) -> dict[str, Any]:
        return self._off("list_dashboards")

    def embed_url(self, dashboard: str, caller: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._off("embed_url")


class QuickClient(QuickProvider):
    """Real AWS Quick over the quicksight client."""

    def _qs(self):
        import boto3
        return boto3.client("quicksight", region_name=_region())

    def _account(self) -> str:
        return os.environ.get("RETAIL_OPS_ACCOUNT_ID") or __import__("boto3").client("sts").get_caller_identity()["Account"]

    def list_flows(self) -> dict[str, Any]:
        try:
            acct = self._account()
            flows = self._qs().list_flows(AwsAccountId=acct).get("FlowSummaryList", [])
            return {"enabled": True, "count": len(flows),
                    "flows": [{"flow_id": f.get("FlowId"), "name": f.get("Name")} for f in flows],
                    "note": None if flows else "No Quick Flows authored yet (build them in the AWS Quick console).",
                    "source_ref": "quicksight ListFlows (AWS Quick)"}
        except Exception as e:
            return {"enabled": True, "error": f"{type(e).__name__}: {e}", "source_ref": "platform.QuickClient"}

    def list_dashboards(self) -> dict[str, Any]:
        try:
            acct = self._account()
            dbs = self._qs().list_dashboards(AwsAccountId=acct).get("DashboardSummaryList", [])
            return {"enabled": True, "count": len(dbs),
                    "dashboards": [{"dashboard_id": d.get("DashboardId"), "name": d.get("Name")} for d in dbs],
                    "source_ref": "quicksight ListDashboards (AWS Quick Sight)"}
        except Exception as e:
            return {"enabled": True, "error": f"{type(e).__name__}: {e}", "source_ref": "platform.QuickClient"}

    _QUICK_NS = "default"

    def register_reader(self, caller: dict[str, Any]) -> dict[str, Any]:
        """Register (idempotently) the signed-in console user as a QuickSight
        READER named `<user_id>`, and add them to the Quick group for their role.

        WHAT THEY CAN OPEN (dashboard, Flows, A6 agent, space) comes from sharing
        those resources with the `retail-ops-oem-*` groups, so the reader must be
        a member of the group for their role (QUICK_GROUP_BY_ROLE). Quick content
        is not row-scoped (no row-level security, spec §13 2026-10-21): a reader
        sees every row of what they can open. Membership failures are reported,
        not raised; the reader then cannot open content shared only with the
        group. Email is synthesized when absent (dev)."""
        acct = self._account()
        qs = self._qs()
        uid = caller.get("user_id") or "anon"
        email = caller.get("email") or f"{uid}@retail-ops.dev.invalid"
        ns = self._QUICK_NS
        try:
            u = qs.describe_user(AwsAccountId=acct, Namespace=ns, UserName=uid)
            arn = u["User"]["Arn"]
        except Exception:
            r = qs.register_user(AwsAccountId=acct, Namespace=ns, IdentityType="QUICKSIGHT",
                                 UserRole="READER", UserName=uid, Email=email)
            arn = r.get("User", {}).get("Arn") or r.get("UserInvitationUrl", "")
        out: dict[str, Any] = {"user_arn": arn, "user_name": uid}
        group = QUICK_GROUP_BY_ROLE.get(caller.get("role") or "")
        if group:
            try:
                qs.create_group_membership(AwsAccountId=acct, Namespace=ns,
                                           GroupName=group, MemberName=uid)
                out["group"] = group
            except Exception as e:  # noqa: BLE001 - sharing gap is reported, not raised
                out["group_error"] = f"{group}: {type(e).__name__}: {e}"
        return out

    def embed_url(self, dashboard: str, caller: dict[str, Any] | None = None) -> dict[str, Any]:
        """Registered-user embed URL for a dashboard (works without the Capacity
        Pricing plan that anonymous embedding needs). `dashboard` is a dashboard
        id or a name resolved against quick-config.json.

        PER-USER (spec §8): when `caller` scope is supplied AND
        RETAIL_OPS_QUICK_PER_USER_EMBED=1, the signed-in user is registered as a Quick
        Sight reader (named by user_id) and added to the Quick group for their
        role, and the embed uses THEIR ARN, so Quick checks that their group may
        open the dashboard. The dashboard is not row-scoped: every reader with
        access sees all rows. Otherwise it falls back to the shared
        RETAIL_OPS_QUICK_EMBED_USER_ARN (admin reader)."""
        cfg = _config()
        dash_id = dashboard
        for d in cfg.get("dashboards", []):
            if d.get("name") == dashboard or d.get("dashboard_id") == dashboard:
                dash_id = d.get("dashboard_id"); break

        per_user = os.environ.get("RETAIL_OPS_QUICK_PER_USER_EMBED", "0") == "1"
        mapped = None
        user_arn = os.environ.get("RETAIL_OPS_QUICK_EMBED_USER_ARN") or cfg.get("embed_user_arn")
        if per_user and caller and caller.get("user_id"):
            try:
                mapped = self.register_reader(caller)
                if mapped.get("user_arn"):
                    user_arn = mapped["user_arn"]
            except Exception as e:
                mapped = {"error": f"per-user mapping failed: {type(e).__name__}: {e}"}

        if not user_arn:
            return {"enabled": True, "dashboard": dashboard, "error": "no embed user configured",
                    "note": "run deploy_quick.py --apply to record a Quick Sight user, or set "
                            "RETAIL_OPS_QUICK_EMBED_USER_ARN", "source_ref": "platform.QuickClient"}
        try:
            acct = self._account()
            req: dict[str, Any] = {
                "AwsAccountId": acct, "UserArn": user_arn,
                "ExperienceConfiguration": {"Dashboard": {"InitialDashboardId": dash_id}},
                "SessionLifetimeInMinutes": int(os.environ.get("RETAIL_OPS_QUICK_EMBED_TTL", "60"))}
            domains = embed_domains()
            if domains:
                # Allows exactly these origins for this URL, so the console embeds
                # without the Quick admin's domain allowlist.
                req["AllowedDomains"] = domains
            resp = self._qs().generate_embed_url_for_registered_user(**req)
            out = {"enabled": True, "dashboard": dashboard, "dashboard_id": dash_id,
                   "embed_url": resp.get("EmbedUrl"),
                   "embed_mode": "per_user" if (per_user and mapped and mapped.get("user_arn")) else "registered_user",
                   "source_ref": f"quicksight GenerateEmbedUrlForRegisteredUser {dash_id}"}
            if mapped:
                out["reader"] = {k: mapped.get(k) for k in ("user_name", "error") if mapped.get(k)}
            return out
        except Exception as e:
            return {"enabled": True, "dashboard": dashboard, "dashboard_id": dash_id,
                    "error": f"{type(e).__name__}: {e}", "source_ref": "platform.QuickClient"}


def get_quick() -> QuickProvider:
    return QuickClient() if enabled() else DisabledQuick()
