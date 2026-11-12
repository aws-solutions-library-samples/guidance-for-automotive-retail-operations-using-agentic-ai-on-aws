#!/usr/bin/env python3
"""AWS Quick sharing groups and their memberships (docs/QUICK_BUILD.md step P1).

Groups decide who may OPEN shared content. The five groups in
`app/services/platform/quick.py` QUICK_GROUPS are created idempotently in the
`default` namespace. Registered console readers whose PLAT_Users role maps to a
group (QUICK_GROUP_BY_ROLE) are added to it; builders listed in
RETAIL_OPS_QUICK_BUILDERS (comma-separated Quick user names) are added to
`retail-ops-builders`. The service adds new readers the same way at embed time.

Quick content is not row-scoped: row-level security was removed by user
decision on 2026-10-21 (spec §13), so a member sees every row of the datasets
and dashboards shared with their group.

Usage:
  python3 -m app.infra.deploy_quick_groups            # dry-run
  python3 -m app.infra.deploy_quick_groups --apply    # create groups + add members
  python3 -m app.infra.deploy_quick_groups --status   # groups and their members
"""
from __future__ import annotations

import argparse
import os

from . import naming
from ..services.platform.quick import QUICK_BUILDERS_GROUP, QUICK_GROUP_BY_ROLE, QUICK_GROUPS

REGION = "us-east-1"
ENV_PREFIX = naming.PREFIX
QUICK_NAMESPACE = "default"


def _quick_names(call, key: str, field: str, **kwargs) -> set[str]:
    """Collect `field` from every page of a NextToken-paginated quicksight list call."""
    names: set[str] = set()
    token = None
    while True:
        resp = call(**kwargs, **({"NextToken": token} if token else {}))
        names.update(x[field] for x in resp.get(key, []) if x.get(field))
        token = resp.get("NextToken")
        if not token:
            return names


def membership_plan() -> dict[str, set[str]]:
    """group -> Quick user names that should be members: builders from
    RETAIL_OPS_QUICK_BUILDERS, and every PLAT_Users user_id whose role maps to a group."""
    from ..services.common import ddb
    plan: dict[str, set[str]] = {g: set() for g in QUICK_GROUPS}
    plan[QUICK_BUILDERS_GROUP].update(
        b.strip() for b in os.environ.get("RETAIL_OPS_QUICK_BUILDERS", "").split(",") if b.strip())
    for usr in ddb.scan_all("PLAT_Users", ENV_PREFIX):
        group = QUICK_GROUP_BY_ROLE.get((usr.get("groups") or [""])[0])
        if group and usr.get("user_id"):
            plan[group].add(usr["user_id"])
    return plan


def ensure_groups(apply: bool) -> None:
    """Create any missing Quick group and add the planned members that are
    already registered Quick users. Never deletes a group or removes a member
    (manual memberships are legitimate). Readers not registered yet are added by
    the service at their first embed."""
    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    qs = s.client("quicksight")
    base = {"AwsAccountId": account, "Namespace": QUICK_NAMESPACE}
    for g in QUICK_GROUPS:
        try:
            qs.describe_group(GroupName=g, **base)
            print(f"  [group] {g} exists")
        except qs.exceptions.ResourceNotFoundException:
            if not apply:
                print(f"  [dry-run] would create group {g}")
                continue
            qs.create_group(GroupName=g, Description=f"Automotive Retail Operations: {g}", **base)
            print(f"  [group] created {g}")
    registered = _quick_names(qs.list_users, "UserList", "UserName", **base)
    for g, wanted in membership_plan().items():
        try:
            have = _quick_names(qs.list_group_memberships, "GroupMemberList", "MemberName",
                                GroupName=g, **base)
        except qs.exceptions.ResourceNotFoundException:
            have = set()
        for member in sorted((wanted & registered) - have):
            if apply:
                qs.create_group_membership(GroupName=g, MemberName=member, **base)
                print(f"  [member] added {member} to {g}")
            else:
                print(f"  [dry-run] would add {member} to {g}")
        pending = sorted(wanted - registered)
        if pending:
            print(f"  [member] {g}: {len(pending)} user(s) not registered in Quick yet "
                  f"(added at first embed): {', '.join(pending)}")


def status() -> None:
    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    qs = s.client("quicksight")
    base = {"AwsAccountId": account, "Namespace": QUICK_NAMESPACE}
    for g in QUICK_GROUPS:
        try:
            members = sorted(_quick_names(qs.list_group_memberships, "GroupMemberList",
                                          "MemberName", GroupName=g, **base))
            print(f"  group {g}: {', '.join(members) if members else '(no members)'}")
        except Exception as e:  # noqa: BLE001
            print(f"  group {g}: {type(e).__name__}")


def main() -> None:
    ap = argparse.ArgumentParser(description="AWS Quick sharing groups and memberships")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(); return
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Quick groups ({len(QUICK_GROUPS)}) and memberships")
    ensure_groups(args.apply)
    print(f"[{mode}] done.")


if __name__ == "__main__":
    main()
