#!/usr/bin/env python3
"""Deploy the demo-reset Lambda (dev only) via boto3.

The Admin Console's "Demo data" card (POST /admin/demo/reset) starts this function
asynchronously; it runs app/demo/reset.run(), syncing every table back to the seed, and
records the outcome on the status row the card polls. A reset takes minutes, so it cannot
run inside the API Lambda (API Gateway's 29 s limit).

Creates / updates:
  * IAM role  retail-ops-dev-demo-reset-role   (iam_roles.demo_reset_policy: logs + DynamoDB
              on the four table families; no ListTables, no wildcard)
  * Lambda    retail-ops-dev-demo-reset        (python3.12, 15 min, 1769 MB, no async retries)

The zip carries the app/ source, the loader's helpers under scripts/, and the seed itself
(sample-data/synthetic/{us,de}: manifest.json + the table artifacts it lists, ~1.4 MB
compressed) at the same relative paths, because ddb_load resolves them from the zip root.
Re-deploy after regenerating the seed (freshness reports it stale until then).

The API service role's lambda:InvokeFunction on this one function is in
iam_roles.service_policy (dev only) and lands with the l4 stage.

dry run by default; --apply; --delete.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from pathlib import Path

try:
    from app.infra import iam_roles, naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import iam_roles, naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
FN = iam_roles.DEMO_RESET_FN
ROLE = iam_roles.ROLE_DEMO_RESET
HANDLER = "app.demo.reset_handler.lambda_handler"
RUNTIME = "python3.12"
TIMEOUT_S = 900          # the Lambda maximum; a reset takes a few minutes
MEMORY_MB = 1769         # one full vCPU: the reset is bound by (de)serialising rows
MARKETS = ("us", "de")
# loader helpers ddb_load imports by file path (relative to the repo root = the zip root)
SCRIPT_FILES = ("scripts/apply_owner_prefixes.py", "scripts/seed/generate/owner_map.py",
                "scripts/seed/generate/exporters/ddb_loader.py")


def seed_files() -> list[Path]:
    """Each market's manifest and the table artifacts it lists (what ddb_load reads)."""
    out: list[Path] = []
    for m in MARKETS:
        base = ROOT / "sample-data" / "synthetic" / m
        manifest = base / "manifest.json"
        out.append(manifest)
        arts = json.loads(manifest.read_text()).get("artifacts") or []
        for a in arts:
            if a.get("kind") == "table":
                out.append(base / a["path"])
    return out


def _zip_code() -> bytes:
    buf = io.BytesIO()
    files = [py for py in (ROOT / "app").rglob("*.py")
             if "__pycache__" not in py.parts and "infra" not in py.relative_to(ROOT / "app").parts[:1]
             and py.name != "devserver.py"]
    files += [ROOT / f for f in SCRIPT_FILES]
    files += seed_files()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(set(files)):
            z.write(f, str(f.relative_to(ROOT)))
    return buf.getvalue()


def _wait(lam, name: str) -> None:
    for _ in range(60):
        st = lam.get_function(FunctionName=name)["Configuration"].get("LastUpdateStatus")
        if st in (None, "Successful"):
            return
        if st == "Failed":
            raise RuntimeError(f"{name} update failed")
        time.sleep(2)


def ensure_function(lam, role_arn: str, code: bytes, apply: bool) -> str:
    env = {"RETAIL_OPS_ENV": naming.ENV, "RETAIL_OPS_ENV_PREFIX": naming.PREFIX}
    try:
        lam.get_function(FunctionName=FN)
        exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False
    if not apply:
        print(f"  [lambda] {'would update' if exists else 'would create'} {FN} ({HANDLER}, "
              f"{TIMEOUT_S}s, {MEMORY_MB} MB, zip {len(code) // 1024} KB)")
        return f"(arn pending {FN})"
    cfg = {"Handler": HANDLER, "Role": role_arn, "Runtime": RUNTIME, "Timeout": TIMEOUT_S,
           "MemorySize": MEMORY_MB, "Environment": {"Variables": env}}
    if exists:
        lam.update_function_code(FunctionName=FN, ZipFile=code)
        _wait(lam, FN)
        lam.update_function_configuration(FunctionName=FN, **cfg)
        _wait(lam, FN)
        print(f"  [lambda] updated {FN}")
    else:
        for attempt in range(6):
            try:
                lam.create_function(FunctionName=FN, Code={"ZipFile": code}, **cfg)
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if "cannot be assumed" not in str(e) or attempt == 5:
                    raise
                time.sleep(5)          # the new role is still propagating
        _wait(lam, FN)
        print(f"  [lambda] created {FN}")
    # a failed reset records itself on the status row; never let Lambda re-run it on its own
    lam.put_function_event_invoke_config(FunctionName=FN, MaximumRetryAttempts=0,
                                         MaximumEventAgeInSeconds=900)
    from app.infra import freshness
    freshness.stamp_lambda(lam, FN, code)   # verify.sh freshness: which source (and seed) it runs
    return lam.get_function(FunctionName=FN)["Configuration"]["FunctionArn"]


def delete(lam, iam) -> None:
    try:
        lam.delete_function(FunctionName=FN)
        print(f"  deleted lambda {FN}")
    except lam.exceptions.ResourceNotFoundException:
        pass
    policy = iam_roles.ROLE_CATALOG[ROLE][0]
    for call in (lambda: iam.delete_role_policy(RoleName=ROLE, PolicyName=policy),
                 lambda: iam.delete_role(RoleName=ROLE)):
        try:
            call()
        except iam.exceptions.NoSuchEntityException:
            pass
    print(f"  deleted role {ROLE}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the demo-reset Lambda (dev only).")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args(argv)
    if naming.ENV != iam_roles.DEMO_RESET_ENV:
        print(f"  [demo-reset] skipped: the demo reset exists only on the {iam_roles.DEMO_RESET_ENV} stack "
              f"(this is {naming.ENV})")
        return 0
    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    lam, iam = s.client("lambda"), s.client("iam")
    print(f"account={account} region={REGION}")
    if args.delete:
        delete(lam, iam)
        return 0
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying {FN}")
    code = _zip_code()
    print(f"  [pkg] zip = {len(code) // 1024} KB (app source + seed)")
    role_arn = iam_roles.ensure_role(iam, account, ROLE, args.apply)
    if args.apply:
        time.sleep(8)                     # let the role propagate before create_function
    arn = ensure_function(lam, role_arn, code, args.apply)
    print(f"[{mode}] done. {FN} -> {arn}" + ("" if args.apply else "  (re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
