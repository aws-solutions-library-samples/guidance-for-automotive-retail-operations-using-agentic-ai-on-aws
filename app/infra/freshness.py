"""Is every deployed piece running the code in this checkout?

Rounds 2-4 of the demo fixes redeployed the API, console and data, but not the job Lambdas or
the AgentCore runtimes, which import the same service code: the nightly jobs and the agents
kept running the old logic and every verify check still passed. Deploy times cannot say
"stale" (committing code that is already deployed makes the deploy look older than the
commit), so each deploy stamps a hash of the SOURCE it shipped, and this compares that stamp
with the source the same deployer would ship now.

  * a Lambda: sha256 over the (name, bytes) entries of the zip its deployer builds (file times
    and zip order do not matter), stamped as the tag `retail-ops-src`.
  * an AgentCore runtime: sha256 over that agent's own bundled source files (its import
    closure; not the dependency wheels), stamped the same way — so an edit an agent does not
    import does not mark it stale.

    python -m app.infra.freshness            # exit 1 when anything is stale or unstamped
    python -m app.infra.freshness --json
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

TAG = "retail-ops-src"
REGION = "us-east-1"


def zip_source_hash(zip_bytes: bytes) -> str:
    """Content hash of a code zip: entry names and bytes, sorted. Independent of file times."""
    h = hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in sorted(z.namelist()):
            h.update(name.encode() + b"\0" + z.read(name) + b"\0")
    return h.hexdigest()


def runtime_source_hash(spec) -> str:
    """Content hash of one agent's bundled source files (no dependency wheels)."""
    from app.agentcore import bundle
    return bundle.code_sha256(bundle.bundle_files(spec), None)


def stamp_lambda(lam, name: str, zip_bytes: bytes) -> None:
    """Record which source a Lambda now runs. Called by each deployer right after it ships code.
    A stamp that cannot be written only warns: the deploy succeeded, and freshness then reports
    the Lambda as unstamped rather than current."""
    try:
        arn = lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
        lam.tag_resource(Resource=arn, Tags={TAG: zip_source_hash(zip_bytes)})
    except Exception as e:  # noqa: BLE001 - see docstring
        print(f"  WARNING {name}: source stamp not written ({type(e).__name__}); freshness will report it")


def stamp_runtime(control, arn: str, spec) -> None:
    control.tag_resource(resourceArn=arn, tags={TAG: runtime_source_hash(spec)})


# Every deployed Lambda and the zip builder its deployer ships (keep in step with the deployers).
def _lambda_builders() -> dict[str, Callable[[], bytes]]:
    from app.infra import (
        deploy_approvals,
        deploy_events,
        deploy_gateway,
        deploy_l4,
        deploy_l6,
    )
    out: dict[str, Callable[[], bytes]] = {
        deploy_l4.AUTH_FN: deploy_l4._zip_code,
        deploy_l4.SVC_FN: deploy_l4._zip_code,
        deploy_gateway.GW_LAMBDA: deploy_l4._zip_code,
        deploy_events.SUB_FN: deploy_events._zip_code,
        deploy_approvals.SUBMIT_FN: deploy_approvals._zip_code,
    }
    for name in deploy_l6.JOB_FN.values():
        out[name] = deploy_l6._zip_code
    from app.infra import deploy_demo_reset, iam_roles, naming
    if naming.ENV == iam_roles.DEMO_RESET_ENV:          # the demo reset exists only on dev
        out[deploy_demo_reset.FN] = deploy_demo_reset._zip_code
    return out


@dataclass
class Item:
    kind: str
    name: str
    state: str       # current | stale | unstamped | missing
    deployed: str | None = None
    expected: str | None = None


def check(lam=None, control=None) -> list[Item]:
    import boto3
    lam = lam or boto3.client("lambda", region_name=REGION)
    control = control or boto3.client("bedrock-agentcore-control", region_name=REGION)
    items: list[Item] = []
    zips: dict[Callable[[], bytes], str] = {}
    for name, build in sorted(_lambda_builders().items()):
        want = zips.setdefault(build, zip_source_hash(build()))
        try:
            arn = lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
        except lam.exceptions.ResourceNotFoundException:
            items.append(Item("lambda", name, "missing", None, want[:12]))
            continue
        have = lam.list_tags(Resource=arn).get("Tags", {}).get(TAG)
        items.append(Item("lambda", name, _state(have, want), (have or "")[:12] or None, want[:12]))
    from app.agentcore import registry
    deployed = {r["agentRuntimeName"]: r for r in _all_runtimes(control)}
    for spec in registry.AGENTS.values():
        want = runtime_source_hash(spec)
        rt = deployed.get(spec.runtime_name)
        if rt is None:
            items.append(Item("runtime", spec.runtime_name, "missing", None, want[:12]))
            continue
        have = control.list_tags_for_resource(resourceArn=rt["agentRuntimeArn"]).get("tags", {}).get(TAG)
        items.append(Item("runtime", spec.runtime_name, _state(have, want), (have or "")[:12] or None, want[:12]))
    return items


def _state(have: str | None, want: str) -> str:
    return "unstamped" if not have else ("current" if have == want else "stale")


def _all_runtimes(control) -> list[dict]:
    out, kw = [], {}
    while True:
        r = control.list_agent_runtimes(**kw)
        out += r.get("agentRuntimes", [])
        if not r.get("nextToken"):
            return out
        kw["nextToken"] = r["nextToken"]


_FIX = {"lambda": {"demo-reset": "demo-reset", "journeys": "l4", "gateway": "gateway", "event": "events", "approvals": "approvals"},
        "runtime": "agentcore"}


def _redeploy_hint(item: Item) -> str:
    if item.kind == "runtime":
        return "agentcore"
    for key, stage in _FIX["lambda"].items():
        if key in item.name:
            return stage
    return "l6"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    items = check()
    bad = [i for i in items if i.state != "current"]
    if args.json:
        print(json.dumps([i.__dict__ for i in items], indent=1))
    else:
        print("=== FRESHNESS: every deployed piece runs this checkout's code ===")
        for i in items:
            note = "" if i.state == "current" else f"  → deploy/deploy.sh --apply --only {_redeploy_hint(i)}"
            print(f"  {'OK  ' if i.state == 'current' else i.state.upper():9s} {i.kind:7s} {i.name:44s} "
                  f"deployed {i.deployed or '—':12s} source {i.expected}{note}")
        stages = sorted({_redeploy_hint(i) for i in bad})
        print(f"=== FRESHNESS RESULT: {'ALL CURRENT' if not bad else f'{len(bad)} NOT CURRENT — redeploy: ' + ','.join(stages)} "
              f"({len(items) - len(bad)}/{len(items)}) ===")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
