"""Demo reset (app/demo/reset.py): a table ends up holding exactly the seed rows — rows a
rehearsal wrote are removed, seed rows and protected rows stay, tables are never dropped."""
from __future__ import annotations

import datetime as dt

import pytest

from app.demo import reset
from app.services.common import ddb

SHIFT = dt.timedelta(days=56)
PREFIX = "retail-ops-dev-"


class ResourceNotFoundException(Exception):
    """Named like botocore's modeled error, which is all the reset looks at."""


class FakeClient:
    """Just enough DynamoDB for the reset: list/describe/scan/batch_write over dict tables."""

    def __init__(self, tables: dict[str, tuple[list[str], list[dict]]]):
        self.tables = {name: (keys, {self._k(keys, r): r for r in rows}) for name, (keys, rows) in tables.items()}
        self.dropped: list[str] = []
        self.backups: list[tuple[str, str]] = []

    @staticmethod
    def _k(keys, item):
        return tuple(str(ddb.from_attr(item[k])) for k in keys)

    def list_tables(self, **_):           # the reset must not need this (no Resource "*" grant)
        raise AssertionError("reset called ListTables")

    def describe_table(self, TableName):
        if TableName not in self.tables:
            raise ResourceNotFoundException(f"Requested resource not found: Table: {TableName} not found")
        keys = self.tables[TableName][0]
        return {"Table": {"KeySchema": [{"AttributeName": k} for k in keys]}}

    def create_backup(self, TableName, BackupName):
        self.backups.append((TableName, BackupName))
        return {}

    def put_item(self, TableName, Item):
        keys, rows = self.tables[TableName]
        rows[self._k(keys, Item)] = Item

    def get_item(self, TableName, Key, **_):
        keys, rows = self.tables[TableName]
        item = rows.get(self._k(keys, Key))
        return {"Item": item} if item else {}

    def scan(self, TableName, **_):
        return {"Items": list(self.tables[TableName][1].values())}

    def batch_write_item(self, RequestItems):
        for name, reqs in RequestItems.items():
            keys, rows = self.tables[name]
            for r in reqs:
                if "DeleteRequest" in r:
                    rows.pop(self._k(keys, r["DeleteRequest"]["Key"]), None)
                else:
                    item = r["PutRequest"]["Item"]
                    rows[self._k(keys, item)] = item
        return {"UnprocessedItems": {}}

    def delete_table(self, TableName):   # a reset must never call this
        self.dropped.append(TableName)


def _seeded(bare: str, keys: list[str]) -> tuple[list[str], list[dict]]:
    rows = reset.seed_rows(SHIFT)[bare]
    return keys, [{k: ddb.to_attr(v) for k, v in r.items()} for r in rows]


@pytest.fixture(scope="module")
def seeds():
    return reset.seed_rows(SHIFT)


def test_rows_a_rehearsal_wrote_are_removed_and_seed_rows_stay(seeds):
    journeys = _seeded("DEALER_Journeys", ["journey_id"])
    journeys[1].append({"journey_id": ddb.to_attr("J-US-SVC-00003"), "stage": ddb.to_attr("DISCOVER")})
    users = _seeded("PLAT_Users", ["user_id"])
    users[1].append({"user_id": ddb.to_attr("U-ADMIN-someone")})          # SSO admin: protected
    users[1].append({"user_id": ddb.to_attr("U-US-RUNTIME")})             # anything else: removed
    c = FakeClient({f"{PREFIX}DEALER_Journeys": journeys, f"{PREFIX}PLAT_Users": users})
    plans = {p.table: p for p in reset.plan(c, PREFIX, SHIFT)}
    assert [ddb.from_attr(k["journey_id"]) for k in plans["DEALER_Journeys"].delete] == ["J-US-SVC-00003"]
    assert [ddb.from_attr(k["user_id"]) for k in plans["PLAT_Users"].delete] == ["U-US-RUNTIME"]
    assert plans["PLAT_Users"].protected == 1
    assert len(plans["DEALER_Journeys"].seed_rows) == len(seeds["DEALER_Journeys"])


def test_yesterdays_copy_of_a_seeded_event_is_removed(seeds):
    keys = ["journey_id", "sk"]
    today = _seeded("DEALER_JourneyEvents", keys)
    yesterday = [{k: ddb.to_attr(v) for k, v in r.items()} for r in reset.seed_rows(SHIFT - dt.timedelta(days=1))["DEALER_JourneyEvents"][:5]]
    c = FakeClient({f"{PREFIX}DEALER_JourneyEvents": (keys, today[1] + yesterday)})
    (p,) = reset.plan(c, PREFIX, SHIFT)
    assert len(p.delete) == 5                              # the stale copies, and only those


def test_a_table_the_seed_does_not_have_is_left_alone():
    c = FakeClient({f"{PREFIX}SOMETHING_Else": (["id"], [{"id": ddb.to_attr("x")}])})
    assert reset.plan(c, PREFIX, SHIFT) == []


def test_the_reset_refuses_a_non_demo_stack_and_an_unconfirmed_apply():
    with pytest.raises(SystemExit, match="refusing"):
        reset.main(["--env-prefix", "retail-ops-prod-"])
    with pytest.raises(SystemExit, match="--yes"):
        reset.main(["--apply"])


def test_run_syncs_backs_up_and_keeps_its_own_status_row(seeds):
    journeys = _seeded("DEALER_Journeys", ["journey_id"])
    journeys[1].append({"journey_id": ddb.to_attr("J-US-SVC-00003")})
    traces = (["trace_id"], [])
    c = FakeClient({f"{PREFIX}DEALER_Journeys": journeys, f"{PREFIX}PLAT_AgentTraces": traces})
    reset.write_status(c, PREFIX, "running", actor="admin@example.com")
    summary = reset.run(c, PREFIX, backup=True)
    assert summary["removed"] == 1 and summary["tables"] == 2
    assert summary["backup_tag"].startswith("demoreset-") and len(c.backups) == 2
    assert summary["written"] == len(seeds["DEALER_Journeys"]) + len(seeds.get("PLAT_AgentTraces", []))
    assert len(c.tables[f"{PREFIX}DEALER_Journeys"][1]) == len(seeds["DEALER_Journeys"])
    status = reset.read_status(c, PREFIX)                 # protected: the reset did not delete it
    assert status["state"] == "running" and status["actor"] == "admin@example.com"
    assert not c.dropped


def test_run_refuses_a_non_demo_prefix():
    with pytest.raises(ValueError, match="refusing"):
        reset.run(FakeClient({}), "retail-ops-prod-")


def test_status_round_trip_and_absent_status():
    c = FakeClient({f"{PREFIX}PLAT_AgentTraces": (["trace_id"], [])})
    assert reset.read_status(c, PREFIX) is None
    reset.write_status(c, PREFIX, "done", removed=3, error=None)
    s = reset.read_status(c, PREFIX)
    assert s["state"] == "done" and s["removed"] == 3 and "error" not in s and s["updated_at"].endswith("Z")
