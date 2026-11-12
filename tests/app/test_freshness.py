"""Freshness (app/infra/freshness.py): a deploy stamps the source it shipped; verify.sh fails
when any Lambda or agent runtime runs other source than this checkout would ship."""
from __future__ import annotations

import io
import zipfile

from app.infra import freshness


def _zip(entries: dict[str, bytes], date=(2020, 1, 1, 0, 0, 0)) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(zipfile.ZipInfo(name, date_time=date), data)
    return buf.getvalue()


def test_the_hash_is_the_content_not_the_file_times_or_order():
    a = _zip({"app/a.py": b"x = 1", "app/b.py": b"y = 2"})
    b = _zip({"app/b.py": b"y = 2", "app/a.py": b"x = 1"}, date=(2026, 11, 6, 9, 0, 0))
    c = _zip({"app/a.py": b"x = 2", "app/b.py": b"y = 2"})
    assert freshness.zip_source_hash(a) == freshness.zip_source_hash(b) != freshness.zip_source_hash(c)


class _Lam:
    class exceptions:
        class ResourceNotFoundException(Exception):
            pass

    def __init__(self, tags: dict[str, str | None]):
        self.tags = tags

    def get_function(self, FunctionName):
        if FunctionName not in self.tags:
            raise self.exceptions.ResourceNotFoundException(FunctionName)
        return {"Configuration": {"FunctionArn": f"arn:{FunctionName}"}}

    def list_tags(self, Resource):
        v = self.tags[Resource.removeprefix("arn:")]
        return {"Tags": {freshness.TAG: v} if v else {}}

    def tag_resource(self, Resource, Tags):
        self.tags[Resource.removeprefix("arn:")] = Tags[freshness.TAG]


class _Control:
    def __init__(self, tags):
        self.tags = tags

    def list_agent_runtimes(self, **_):
        return {"agentRuntimes": [{"agentRuntimeName": n, "agentRuntimeArn": f"arn:{n}"} for n in self.tags]}

    def list_tags_for_resource(self, resourceArn):
        v = self.tags[resourceArn.removeprefix("arn:")]
        return {"tags": {freshness.TAG: v} if v else {}}

    def tag_resource(self, resourceArn, tags):
        self.tags[resourceArn.removeprefix("arn:")] = tags[freshness.TAG]


def test_check_tells_current_stale_unstamped_and_missing(monkeypatch):
    from app.agentcore import registry
    builders = freshness._lambda_builders()
    names = sorted(builders)
    want = {n: freshness.zip_source_hash(builders[n]()) for n in names[:3]}
    lam = _Lam({names[0]: want[names[0]], names[1]: "0" * 64, names[2]: None})   # names[3:] missing
    specs = list(registry.AGENTS.values())
    control = _Control({specs[0].runtime_name: freshness.runtime_source_hash(specs[0]),
                        specs[1].runtime_name: "f" * 64})
    got = {i.name: i.state for i in freshness.check(lam, control)}
    assert got[names[0]] == "current" and got[names[1]] == "stale" and got[names[2]] == "unstamped"
    assert got[names[3]] == "missing"
    assert got[specs[0].runtime_name] == "current" and got[specs[1].runtime_name] == "stale"
    assert got[specs[2].runtime_name] == "missing"


def test_a_stamp_written_by_a_deploy_reads_back_as_current():
    from app.infra import deploy_l4
    lam = _Lam({deploy_l4.SVC_FN: None})
    code = deploy_l4._zip_code()
    freshness.stamp_lambda(lam, deploy_l4.SVC_FN, code)
    assert lam.tags[deploy_l4.SVC_FN] == freshness.zip_source_hash(deploy_l4._zip_code())


def test_a_stamp_that_cannot_be_written_never_fails_the_deploy(capsys):
    class NoTags:
        def get_function(self, FunctionName):
            return {"Configuration": {}}
    freshness.stamp_lambda(NoTags(), "fn", _zip({"a": b"1"}))
    assert "source stamp not written" in capsys.readouterr().out


def test_every_deployed_lambda_has_a_builder():
    from app.infra import deploy_demo_reset, deploy_l6, naming
    names = set(freshness._lambda_builders())
    extra = 6 if naming.ENV == "dev" else 5           # + the demo-reset Lambda, dev only
    assert set(deploy_l6.JOB_FN.values()) <= names and len(names) == len(deploy_l6.JOB_FN) + extra
    if naming.ENV == "dev":
        assert deploy_demo_reset.FN in names
        assert freshness._redeploy_hint(freshness.Item("lambda", deploy_demo_reset.FN, "stale")) == "demo-reset"
