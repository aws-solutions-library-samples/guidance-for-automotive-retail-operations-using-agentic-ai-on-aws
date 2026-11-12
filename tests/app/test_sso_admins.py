# ruff: noqa: S101
"""SSO allowlist -> Administrator (spec §8.1, decided 2026-10-21).

Only the aliases in RETAIL_OPS_SSO_ADMINS get the Administrator role; every
other SSO sign-in is refused exactly as before. Covers the shared alias rules,
the sso-admins deploy step (app/infra/sso_admins.py) and the authorizer.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.infra import sso_admins
from app.services.api import authorizer
from app.services.common import ddb, sso_allowlist
from app.services.common.local_ddb import FakeDynamoDB

ROOT = Path(__file__).resolve().parents[2]
TABLE = ddb.table_name("PLAT_Users")
MARKER = sso_allowlist.MARKER


@pytest.fixture(autouse=True)
def _alias_domain(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_IDP_ALIAS_DOMAIN", "@corp.example.com")


# --- alias rules ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,alias", [
    ("jdoe", "jdoe"), (" JDoe ", "jdoe"), ("jdoe@corp.example.com", "jdoe"),
    ("JDOE@CORP.EXAMPLE.COM", "jdoe"), ("j.doe", "j.doe"), ("a1-b2", "a1-b2"),
    ("", None), ("two words", None), ("a@example.com", None), ("-lead", None),
    ("x" * 65, None), (None, None), ("jdoe;drop", None),
])
def test_normalize_alias(raw, alias):
    assert sso_allowlist.normalize_alias(raw) == alias


def test_parse_dedupes_and_reports_invalid_entries():
    assert sso_allowlist.parse(" jdoe, JDOE@corp.example.com ,, bob ") == (["jdoe", "bob"], [])
    assert sso_allowlist.parse("jdoe, not an alias") == (["jdoe"], ["not an alias"])
    assert sso_allowlist.parse("") == ([], []) and sso_allowlist.parse(None) == ([], [])


def test_admin_row_shape():
    row = sso_allowlist.admin_row("jdoe", "de")
    assert row["user_id"] == "U-ADMIN-jdoe" and row["username"] == "jdoe"
    assert row["groups"] == ["platform_admin"] and row["scope_node_id"] == "OEM"
    assert row["market_id"] == "de" and row["active"] is True and row["provisioned_by"] == MARKER
    assert "test_password" not in row and "email" not in row


# --- the deploy step -----------------------------------------------------------------------

class _Table(FakeDynamoDB):
    """In-memory PLAT_Users that also deletes, and enforces the two marker
    conditions the deploy step sends (so a missing condition would fail here)."""

    def _row(self, uid: str):
        _bare, tbl = self._table(TABLE)
        item = tbl.get((uid,))
        return ddb.item_to_dict(item) if item else None

    def put_item(self, TableName, Item, **kw):
        assert kw.get("ConditionExpression") == "attribute_not_exists(user_id) OR provisioned_by = :m"
        current = self._row(ddb.from_attr(Item["user_id"]))
        if current is not None and current.get("provisioned_by") != MARKER:
            raise AssertionError("conditional put would overwrite a row the allowlist did not write")
        return super().put_item(TableName, Item)

    def delete_item(self, TableName, Key, **kw):
        assert kw.get("ConditionExpression") == "provisioned_by = :m"
        uid = ddb.from_attr(Key["user_id"])
        current = self._row(uid)
        if current is None or current.get("provisioned_by") != MARKER:
            raise AssertionError("conditional delete would remove a row the allowlist did not write")
        _bare, tbl = self._table(TableName)
        del tbl[(uid,)]
        return {}

    def users(self) -> dict[str, dict]:
        return {r["user_id"]: r for r in self.rows("PLAT_Users")}


SEED = [
    {"user_id": "U-US-00002", "username": "elizabeth.thomas.101", "groups": ["dealer_advisor"],
     "scope_node_id": "D-101", "market_id": "us", "active": True},
    {"user_id": "U-US-00051", "username": "andre.wright", "groups": ["admin_tenant"],
     "scope_node_id": "OEM", "market_id": "us", "active": True},
]


@pytest.fixture
def table() -> _Table:
    t = _Table()
    t.seed("PLAT_Users", [dict(r) for r in SEED])
    return t


def _run(table, monkeypatch, value, *args):
    if value is None:
        monkeypatch.delenv(sso_allowlist.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(sso_allowlist.ENV_VAR, value)
    return sso_admins.main(list(args), client=table)


def test_apply_adds_listed_aliases_and_leaves_other_rows_alone(table, monkeypatch, capsys):
    before = table.users()
    assert _run(table, monkeypatch, "jdoe, Bob@Corp.Example.com", "--apply") == 0
    users = table.users()
    assert users["U-ADMIN-jdoe"] == sso_allowlist.admin_row("jdoe", "us")
    assert users["U-ADMIN-bob"]["username"] == "bob"
    assert {k: v for k, v in users.items() if not k.startswith("U-ADMIN-")} == before
    assert "2 to write" in capsys.readouterr().out
    assert _run(table, monkeypatch, "jdoe,bob", "--apply") == 0          # idempotent
    assert "0 to write, 2 unchanged" in capsys.readouterr().out


def test_delisted_alias_is_removed_and_only_marker_rows_are(table, monkeypatch):
    _run(table, monkeypatch, "jdoe,bob", "--apply")
    hand_made = {"user_id": "U-ADMIN-carol", "username": "carol", "groups": ["admin_tenant"], "active": True}
    table.seed("PLAT_Users", [hand_made])                       # same id prefix, no marker
    assert _run(table, monkeypatch, "jdoe", "--apply") == 0
    users = table.users()
    assert "U-ADMIN-bob" not in users and "U-ADMIN-jdoe" in users
    assert users["U-ADMIN-carol"]["groups"] == ["admin_tenant"]  # never touched
    assert {"U-US-00002", "U-US-00051"} <= set(users)


def test_empty_value_removes_every_allowlisted_admin(table, monkeypatch):
    _run(table, monkeypatch, "jdoe,bob", "--apply")
    assert _run(table, monkeypatch, "", "--apply") == 0
    assert not [u for u in table.users() if u.startswith("U-ADMIN-")]
    assert set(table.users()) == {r["user_id"] for r in SEED}


def test_unset_variable_changes_nothing_and_reads_nothing(monkeypatch, capsys):
    class _Refuse:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected DynamoDB call {name}")
    assert _run(_Refuse(), monkeypatch, None, "--apply") == 0
    assert "is not set: PLAT_Users unchanged" in capsys.readouterr().out


def test_invalid_alias_aborts_before_any_write(table, monkeypatch, capsys):
    before = table.users()
    assert _run(table, monkeypatch, "jdoe, not an alias", "--apply") == 2
    assert table.users() == before
    assert "not valid IdP aliases" in capsys.readouterr().err


def test_dry_run_writes_nothing(table, monkeypatch, capsys):
    before = table.users()
    assert _run(table, monkeypatch, "jdoe", "--market", "de") == 0
    assert table.users() == before
    out = capsys.readouterr().out
    assert "[would put] U-ADMIN-jdoe" in out and "market de" in out


def test_conflicts_are_reported_and_skipped(table, monkeypatch, capsys):
    table.seed("PLAT_Users", [{"user_id": "U-ADMIN-dave", "username": "dave", "groups": ["oem_dm"]}])
    assert _run(table, monkeypatch, "dave, andre.wright, jdoe", "--apply") == 1
    users = table.users()
    assert users["U-ADMIN-dave"]["groups"] == ["oem_dm"]           # existing id without the marker
    assert "U-ADMIN-andre.wright" not in users                      # username already taken (U-US-00051)
    assert users["U-ADMIN-jdoe"]["provisioned_by"] == MARKER      # the rest still applied
    out = capsys.readouterr().out
    assert "[skip] dave" in out and "[skip] andre.wright" in out


def test_bad_market_is_refused(table, monkeypatch):
    with pytest.raises(SystemExit):
        _run(table, monkeypatch, "jdoe", "--market", "usa")


# --- deploy.sh wiring ----------------------------------------------------------------------

def test_deploy_sh_runs_sso_admins_right_after_data():
    script = (ROOT / "deploy" / "deploy.sh").read_text()
    stages = re.search(r"^ALL_STAGES=\(([^)]*)\)", script, re.MULTILINE).group(1).split()
    assert stages[stages.index("data") + 1] == "sso-admins"
    assert "should_run sso-admins" in script
    # deploy.sh runs every deployer with the Python it resolved (PY: 3.12+ with boto3)
    assert '"$PY" -m app.infra.sso_admins --market "$MARKET" --region "$REGION" $APPLY_FLAG' in script
    assert script.index("should_run data;") < script.index("should_run sso-admins;") < script.index("should_run events;")
    subprocess.run(["bash", "-n", str(ROOT / "deploy" / "deploy.sh")], check=True)  # noqa: S603,S607


def test_config_example_documents_the_variable_commented_out():
    text = (ROOT / "deploy" / "config.env.example").read_text()
    assert "# export RETAIL_OPS_SSO_ADMINS=alias1,alias2" in text
    assert not re.search(r"^export RETAIL_OPS_SSO_ADMINS", text, re.MULTILINE)


# --- the authorizer --------------------------------------------------------------------------

def _federated(monkeypatch, claims):
    monkeypatch.setattr(authorizer.jwt_verify, "verify_cognito_jwt", lambda *a, **k: claims)
    monkeypatch.setattr(authorizer, "COGNITO_ISSUER", "https://cognito-idp.local")
    monkeypatch.setattr(authorizer, "COGNITO_CLIENT_ID", "test-client")
    return authorizer.resolve_principal("Bearer eyJhbGciOiJub25lIn0.e30.sig")


@pytest.fixture
def listed(fake_ddb):
    ddb.put_item("PLAT_Users", sso_allowlist.admin_row("jdoe", "us"))
    return "U-ADMIN-jdoe"


@pytest.mark.parametrize("sub", ["jdoe", "JDoe", "jdoe@corp.example.com", "JDOE@CORP.EXAMPLE.COM"])
def test_listed_alias_signs_in_as_the_administrator(monkeypatch, listed, sub):
    p = _federated(monkeypatch, {"cognito:username": f"CorpDirectory_{sub}",
                                 "preferred_username": sub, "token_use": "id"})
    assert p["user_id"] == listed and p["role"] == "platform_admin" and p["groups"] == "platform_admin"
    assert p["is_oem"] == "true" and p["scope_node_id"] == "OEM" and p["market_id"] == "us"
    assert p["dealer_ids"] and p["customer_id"] == ""


def test_listed_alias_without_preferred_username_still_resolves(monkeypatch, listed):
    """Only the IdP-prefixed Cognito username: the alias is taken from it."""
    p = _federated(monkeypatch, {"cognito:username": "CorpDirectory_JDoe", "token_use": "id"})
    assert p["user_id"] == listed and p["role"] == "platform_admin"


@pytest.mark.parametrize("sub", ["someone", "jdoe2", "mallory"])
def test_unlisted_alias_is_still_refused(monkeypatch, listed, sub):
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {"cognito:username": f"CorpDirectory_{sub}",
                                 "preferred_username": sub, "token_use": "id"})


def test_delisted_alias_is_refused_again(monkeypatch, fake_ddb, listed):
    fake_ddb.tables["PLAT_Users"].pop((listed,))
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {"cognito:username": "CorpDirectory_jdoe",
                                 "preferred_username": "jdoe", "token_use": "id"})


def test_allowlist_path_accepts_only_marker_rows(monkeypatch, fake_ddb):
    """A U-ADMIN-<alias> row the allowlist did not write is not reachable by alias."""
    ddb.put_item("PLAT_Users", {**sso_allowlist.admin_row("mallory", "us"), "username": "m.allory",
                                "provisioned_by": "hand"})
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {"cognito:username": "CorpDirectory_Mallory",
                                 "preferred_username": "Mallory", "token_use": "id"})


def test_native_sign_in_never_uses_the_allowlist_path(monkeypatch, listed):
    """A native (non-federated) token whose username differs from the alias only
    by case is not an allowlist identity."""
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {"cognito:username": "JDoe", "token_use": "id"})


# --- the IdP rule (review N1): only the IdP recorded on the row ------------------------------

@pytest.mark.parametrize("claims", [
    {"cognito:username": "OtherIdP_jdoe"},
    {"cognito:username": "OtherIdP_jdoe", "preferred_username": "jdoe"},   # exact username match
    {"cognito:username": "OtherIdP_JDoe@corp.example.com", "preferred_username": "JDoe@corp.example.com"},
    {"cognito:username": "corpdirectory_jdoe", "preferred_username": "jdoe"},  # IdP names are exact
    {"cognito:username": "CorpDirectoryX_jdoe", "preferred_username": "jdoe"},
])
def test_token_from_another_idp_is_refused(monkeypatch, listed, claims):
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {**claims, "token_use": "id"})


@pytest.mark.parametrize("claims", [
    {"cognito:username": "jdoe"},
    {"cognito:username": "jdoe", "preferred_username": "jdoe"},     # username equals the alias
    {"cognito:username": "jdoe", "username": "jdoe"},
    {"cognito:username": "jdoe_x", "preferred_username": "jdoe"},   # native name containing '_'
    {"cognito:username": "U-ADMIN-jdoe"},                             # native name equal to the row id
])
def test_native_username_equal_to_the_alias_is_refused(monkeypatch, listed, claims):
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {**claims, "token_use": "id"})


@pytest.mark.parametrize("sub", ["jdoe", "JDoe", "jdoe@corp.example.com", "JDOE@CORP.EXAMPLE.COM"])
def test_case_and_domain_variants_resolve_only_for_the_recorded_idp(monkeypatch, fake_ddb, sub):
    """A row written for another IdP (RETAIL_OPS_IDP_NAME=CorpDirectory) is reached
    through that IdP only, with the same case and @corp.example.com leniency."""
    ddb.put_item("PLAT_Users", sso_allowlist.admin_row("jdoe", "us", "CorpDirectory"))
    p = _federated(monkeypatch, {"cognito:username": f"CorpDirectory_{sub}",
                                 "preferred_username": sub, "token_use": "id"})
    assert p["user_id"] == "U-ADMIN-jdoe" and p["role"] == "platform_admin"
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {"cognito:username": f"OtherIdP_{sub}",
                                 "preferred_username": sub, "token_use": "id"})


def test_alias_must_match_the_row(monkeypatch, listed):
    """The right IdP is not enough: the token must name the row's alias."""
    with pytest.raises(authorizer.authz.Unauthenticated, match="unknown user"):
        _federated(monkeypatch, {"cognito:username": "CorpDirectory_U-ADMIN-jdoe", "token_use": "id"})


def test_resolve_applies_the_idp_rule(listed):
    """sso_allowlist.resolve on its own (the authorizer's last step)."""
    assert sso_allowlist.resolve({"cognito:username": "CorpDirectory_JDoe@corp.example.com"}) == listed
    assert sso_allowlist.resolve({"cognito:username": "OtherIdP_jdoe"}) is None
    assert sso_allowlist.resolve({"cognito:username": "OtherIdP_x", "preferred_username": "jdoe"}) is None
    assert sso_allowlist.resolve({"cognito:username": "jdoe", "preferred_username": "jdoe"}) is None


def test_accepts_needs_the_marker_and_the_recorded_idp():
    row = sso_allowlist.admin_row("jdoe", "us")
    ok = {"cognito:username": "CorpDirectory_jdoe"}
    assert sso_allowlist.accepts(ok, row)
    assert not sso_allowlist.accepts(ok, {**row, "provisioned_by": "hand"})
    assert not sso_allowlist.accepts(ok, {**row, "federated_identity": None})
    assert not sso_allowlist.accepts(ok, {**row, "federated_identity": "OtherIdP:jdoe"})
    assert not sso_allowlist.accepts(ok, None)


def test_existing_personas_resolve_unchanged(monkeypatch, listed):
    native = _federated(monkeypatch, {"cognito:username": "U-US-00044", "token_use": "id"})
    assert native["user_id"] == "U-US-00044" and native["role"] == "oem_dm"
    dev = authorizer.resolve_principal("Bearer user:U-US-00002")
    assert dev["role"] == "dealer_advisor" and dev["dealer_ids"] == "D-101"
