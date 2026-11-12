"""SSO allowlist -> Administrator rows in PLAT_Users (spec §8.1, decided 2026-10-21).

Only the enterprise-IdP aliases listed in RETAIL_OPS_SSO_ADMINS
(comma-separated, set in the git-ignored deploy/config.env) get the
Administrator role (`platform_admin`: every staff module, tenant-wide). This
step syncs PLAT_Users with that list:

  * each listed alias gets the row `U-ADMIN-<alias>` (username <alias>, groups
    [platform_admin], scope OEM in --market, provisioned_by "sso-allowlist");
  * a row carrying provisioned_by "sso-allowlist" whose alias is no longer
    listed is deleted;
  * no other row is changed. A listed alias whose id or username already
    belongs to a row without the marker is reported and skipped (exit 1).

Aliases are matched case-insensitively and an optional configured corp suffix (RETAIL_OPS_IDP_ALIAS_DOMAIN) is ignored
(app/services/common/sso_allowlist.py holds the rules the authorizer shares).
RETAIL_OPS_SSO_ADMINS unset -> nothing changes; set to an empty value -> every
allowlisted Administrator is removed. Writes are conditional on the marker, so
a row the allowlist did not write is never overwritten or deleted.

Runs after the `data` stage (deploy/deploy.sh stage `sso-admins`), because the
cognito stage runs before PLAT_Users exists and a data reload would otherwise
drop the rows.

Usage:
  python3 -m app.infra.sso_admins                      # dry-run: print the plan
  python3 -m app.infra.sso_admins --apply              # write it
  python3 -m app.infra.sso_admins --market de --apply  # home market of the rows
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

from ..services.common import ddb, sso_allowlist

TABLE = "PLAT_Users"
IDP_NAME = os.environ.get("RETAIL_OPS_IDP_NAME", "CorpDirectory")
_MARKET = re.compile(r"^[a-z]{2}$")
_ONLY_MARKER = {":m": {"S": sso_allowlist.MARKER}}


def plan(aliases: list[str], rows: list[dict[str, Any]], market: str,
         idp_name: str = IDP_NAME) -> dict[str, list[Any]]:
    """What the sync does, without doing it.

    Returns {"put": [row, ...], "keep": [user_id, ...], "delete": [user_id, ...],
    "conflicts": [(alias, reason), ...]}. Only rows carrying the allowlist marker
    are ever in "put" over an existing row or in "delete"."""
    marker = sso_allowlist.MARKER
    by_id = {r.get("user_id"): r for r in rows}
    out: dict[str, list[Any]] = {"put": [], "keep": [], "delete": [], "conflicts": []}
    listed: set[str] = set()
    for alias in aliases:
        uid = sso_allowlist.admin_user_id(alias)
        listed.add(uid)
        existing = by_id.get(uid)
        if existing is not None and existing.get("provisioned_by") != marker:
            out["conflicts"].append((alias, f"{uid} already exists and was not written by the allowlist"))
            continue
        clash = sorted(str(r.get("user_id")) for r in rows
                       if r.get("provisioned_by") != marker
                       and str(r.get("username") or "").lower() == alias)
        if clash:
            out["conflicts"].append((alias, f"username {alias!r} already belongs to {', '.join(clash)}"))
            continue
        desired = sso_allowlist.admin_row(alias, market, idp_name)
        if existing == desired:
            out["keep"].append(uid)
        else:
            out["put"].append(desired)
    out["delete"] = sorted(str(r["user_id"]) for r in rows
                           if r.get("provisioned_by") == marker and r.get("user_id") not in listed)
    return out


def read_rows(client: Any, table: str) -> list[dict[str, Any]]:
    """Every PLAT_Users row as a plain dict (paginated scan)."""
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"TableName": table}
    while True:
        resp = client.scan(**kwargs)
        rows.extend(ddb.item_to_dict(i) for i in resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return rows
        kwargs["ExclusiveStartKey"] = lek


def apply_plan(client: Any, table: str, p: dict[str, list[Any]]) -> None:
    """Write the plan. Both writes are conditional on the marker, so a row the
    allowlist did not write is never overwritten or deleted."""
    for row in p["put"]:
        client.put_item(TableName=table, Item={k: ddb.to_attr(v) for k, v in row.items()},
                        ConditionExpression="attribute_not_exists(user_id) OR provisioned_by = :m",
                        ExpressionAttributeValues=_ONLY_MARKER)
    for uid in p["delete"]:
        client.delete_item(TableName=table, Key={"user_id": {"S": uid}},
                           ConditionExpression="provisioned_by = :m",
                           ExpressionAttributeValues=_ONLY_MARKER)


def _print_plan(p: dict[str, list[Any]], apply: bool) -> None:
    for row in p["put"]:
        print(f"  [{'put' if apply else 'would put'}] {row['user_id']} "
              f"(alias {row['username']}, platform_admin, market {row['market_id']})")
    for uid in p["keep"]:
        print(f"  [keep] {uid} unchanged")
    for uid in p["delete"]:
        print(f"  [{'delete' if apply else 'would delete'}] {uid} (alias no longer listed)")
    for alias, reason in p["conflicts"]:
        print(f"  [skip] {alias}: {reason}")


def main(argv: list[str] | None = None, client: Any = None) -> int:
    ap = argparse.ArgumentParser(description="Sync the SSO allowlist (RETAIL_OPS_SSO_ADMINS) "
                                             "into PLAT_Users as Administrator rows.")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry-run)")
    ap.add_argument("--market", default="us", help="home market of the Administrator rows (default us)")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--env-prefix", default=ddb.DEFAULT_ENV_PREFIX)
    args = ap.parse_args(argv)
    if not _MARKET.fullmatch(args.market):
        ap.error("--market must be a two-letter market id such as us or de")
    mode = "APPLY" if args.apply else "DRY-RUN"

    raw = os.environ.get(sso_allowlist.ENV_VAR)
    if raw is None:
        print(f"[{mode}] {sso_allowlist.ENV_VAR} is not set: PLAT_Users unchanged. Set it in "
              f"deploy/config.env (an empty value removes every allowlisted Administrator).")
        return 0
    aliases, invalid = sso_allowlist.parse(raw)
    if invalid:
        print(f"ERROR: {sso_allowlist.ENV_VAR} has entries that are not valid IdP aliases: "
              f"{', '.join(repr(i) for i in invalid)}", file=sys.stderr)
        return 2
    table = ddb.table_name(TABLE, args.env_prefix)
    print(f"[{mode}] SSO allowlist -> {table} (market {args.market}): "
          f"{', '.join(aliases) if aliases else '(empty: remove every allowlisted Administrator)'}")

    if client is None:
        import boto3
        client = boto3.client("dynamodb", region_name=args.region)
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        rows = read_rows(client, table)
    except (BotoCoreError, ClientError) as e:
        if args.apply:
            print(f"ERROR: could not read {table}: {type(e).__name__}: {e} "
                  f"(run the data stage first)", file=sys.stderr)
            return 1
        print(f"  could not read {table} ({type(e).__name__}); showing the rows a fresh table would get")
        rows = []
    p = plan(aliases, rows, args.market)
    _print_plan(p, args.apply)
    if args.apply:
        apply_plan(client, table, p)
    print(f"[{mode}] {len(p['put'])} to write, {len(p['keep'])} unchanged, "
          f"{len(p['delete'])} to delete, {len(p['conflicts'])} skipped"
          + ("" if args.apply else "  (re-run with --apply to write)"))
    return 1 if p["conflicts"] else 0


if __name__ == "__main__":
    sys.exit(main())
