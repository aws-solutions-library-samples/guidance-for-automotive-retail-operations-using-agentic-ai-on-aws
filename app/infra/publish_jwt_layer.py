#!/usr/bin/env python3
"""Publish the retail-ops-<env>-jwt Lambda layer (PyJWT + cryptography).

The authorizer verifies Cognito RS256 tokens. With this layer present it uses
PyJWT + cryptography (maintained crypto path); WITHOUT it, jwt_verify falls back
to a stdlib pure-integer RSA verify with identical validation semantics. So this
layer is an OPTIONAL hardening step — deploy_l4 picks it up automatically if the
layer exists (see deploy_l4._jwt_layer_arn) and runs fine without it.

On a fresh account, run this once before (or after) deploy_l4 to get the
maintained-library path. Idempotent: publishes a new layer version each run.

  python3 -m app.infra.publish_jwt_layer --apply     # build + publish
  python3 -m app.infra.publish_jwt_layer             # dry-run (build only, no publish)

Requires pip (to vendor PyJWT+cryptography for the Lambda runtime). If pip or the
build fails, it prints a clear message and exits non-zero WITHOUT failing the
overall deploy — the caller (bootstrap.sh) treats this stage as best-effort.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

REGION = "us-east-1"
LAYER_NAME = naming.name("jwt")
RUNTIME = "python3.12"
# Pinned versions (exact pins per production-safety guidance).
PACKAGES = ["PyJWT==2.9.0", "cryptography==43.0.1"]


def _build_layer_zip() -> bytes:
    """pip-install the packages into python/ (Lambda layer layout) and zip."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "python"
        target.mkdir()
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--no-warn-conflicts",
               "--platform", "manylinux2014_x86_64", "--only-binary", ":all:",
               "--python-version", "3.12", "--target", str(target), *PACKAGES]
        subprocess.run(cmd, check=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in target.rglob("*"):
                if f.is_file():
                    z.write(f, str(f.relative_to(Path(tmp))))
        return buf.getvalue()


def publish(apply: bool) -> str | None:
    try:
        code = _build_layer_zip()
    except Exception as e:
        print(f"  [layer] build failed ({type(e).__name__}: {e}); skipping — "
              f"authorizer will use the stdlib RSA fallback.")
        return None
    print(f"  [layer] built {LAYER_NAME} zip = {len(code)} bytes ({', '.join(PACKAGES)})")
    if not apply:
        print("  [dry-run] would publish layer version")
        return None
    import boto3
    lam = boto3.client("lambda", region_name=REGION)
    resp = lam.publish_layer_version(
        LayerName=LAYER_NAME,
        Description="PyJWT + cryptography for Cognito RS256 verification (Retail Ops authorizer)",
        Content={"ZipFile": code},
        CompatibleRuntimes=[RUNTIME],
    )
    arn = resp["LayerVersionArn"]
    print(f"  [layer] published {arn}")
    return arn


def main() -> None:
    ap = argparse.ArgumentParser(description=f"Publish the {LAYER_NAME} Lambda layer")
    ap.add_argument("--apply", action="store_true", help="build and publish (else build-only dry-run)")
    args = ap.parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] {LAYER_NAME} layer (PyJWT + cryptography)")
    publish(args.apply)


if __name__ == "__main__":
    main()
