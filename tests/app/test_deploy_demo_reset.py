"""The demo-reset Lambda's package and IAM: the zip carries everything reset.run() reads
(app source, loader helpers, both markets' seed at the paths ddb_load resolves), and the
role is table-family scoped with no wildcard and no ListTables."""
from __future__ import annotations

import io
import json
import zipfile

from app.infra import deploy_demo_reset as d
from app.infra import iam_roles, naming

ACCOUNT = "123456789012"


def _names() -> set[str]:
    with zipfile.ZipFile(io.BytesIO(d._zip_code())) as z:
        return set(z.namelist())


def test_zip_carries_the_handler_loader_and_both_seeds():
    names = _names()
    for f in ("app/demo/reset_handler.py", "app/demo/reset.py", "app/services/l1_data/ddb_load.py",
              "app/services/common/local_ddb.py", *d.SCRIPT_FILES):
        assert f in names, f
    for m in d.MARKETS:
        manifest = json.loads((d.ROOT / "sample-data" / "synthetic" / m / "manifest.json").read_text())
        tables = [a["path"] for a in manifest["artifacts"] if a.get("kind") == "table"]
        assert tables and all(f"sample-data/synthetic/{m}/{p}" in names for p in tables)
        assert f"sample-data/synthetic/{m}/manifest.json" in names
    assert not any(n.startswith(("app/infra/", "app/console/")) or "__pycache__" in n for n in names)
    assert not any("/lake/" in n or "/quick_index/" in n for n in names)        # only what the reset reads


def test_zip_is_deterministic():
    from app.infra import freshness
    assert freshness.zip_source_hash(d._zip_code()) == freshness.zip_source_hash(d._zip_code())


def test_reset_role_is_family_scoped_with_no_wildcard_and_no_list_tables():
    doc = iam_roles.demo_reset_policy(ACCOUNT)
    ddb_stmts = [s for s in doc["Statement"] if any(a.startswith("dynamodb:") for a in s["Action"])]
    (st,) = ddb_stmts
    assert "dynamodb:ListTables" not in st["Action"]
    assert {"dynamodb:BatchWriteItem", "dynamodb:DescribeTable", "dynamodb:CreateBackup"} <= set(st["Action"])
    assert all(r != "*" and not r.endswith(f"table/{naming.PREFIX}*") for r in st["Resource"])
    assert all(f"table/{naming.PREFIX}{fam}" in " ".join(st["Resource"]) for fam in iam_roles.ALL_FAMILIES)
    assert not any(a.split(":")[0] not in ("dynamodb", "logs") for s in doc["Statement"] for a in s["Action"])


def test_service_role_may_start_only_the_reset_function_on_dev():
    stmts = iam_roles.service_policy(ACCOUNT)["Statement"]
    invoke = [s for s in stmts if "lambda:InvokeFunction" in s.get("Action", [])]
    if naming.ENV == iam_roles.DEMO_RESET_ENV:
        (s,) = invoke
        assert s["Resource"] == f"arn:aws:lambda:{iam_roles.REGION}:{ACCOUNT}:function:{naming.PREFIX}demo-reset"
    else:
        assert not invoke


def test_function_name_matches_what_the_api_invokes(monkeypatch):
    from app.services.admin import demo_data
    monkeypatch.delenv("RETAIL_OPS_DEMO_RESET_FN", raising=False)
    monkeypatch.delenv("RETAIL_OPS_ENV_PREFIX", raising=False)
    monkeypatch.setenv("RETAIL_OPS_ENV", naming.ENV)
    assert demo_data.function_name() == d.FN
