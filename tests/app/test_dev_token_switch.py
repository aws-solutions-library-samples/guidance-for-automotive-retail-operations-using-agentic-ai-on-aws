"""deploy_l4 --dev-token: switch the plain development tokens off (and on) without a redeploy."""
from __future__ import annotations

from app.infra import deploy_l4


class _NotFound(Exception):
    pass


class FakeLambda:
    class exceptions:  # noqa: N801 - the boto3 client attribute name
        ResourceNotFoundException = _NotFound

    def __init__(self, envs: dict[str, dict]):
        self.envs = envs
        self.updates: list[tuple[str, dict]] = []

    def get_function_configuration(self, FunctionName):  # noqa: N803 - boto3 keyword
        if FunctionName not in self.envs:
            raise _NotFound(FunctionName)
        return {"Environment": {"Variables": dict(self.envs[FunctionName])}}

    def update_function_configuration(self, FunctionName, Environment):  # noqa: N803
        self.updates.append((FunctionName, Environment["Variables"]))
        self.envs[FunctionName] = Environment["Variables"]

    def get_function(self, FunctionName):  # noqa: N803 - _wait_updated polls this
        return {"Configuration": {"LastUpdateStatus": "Successful"}}


def test_off_sets_only_the_switch_and_keeps_every_other_variable():
    lam = FakeLambda({deploy_l4.AUTH_FN: {"RETAIL_OPS_COGNITO_ISSUER": "iss", "RETAIL_OPS_ENV": "dev"}})
    assert deploy_l4.set_dev_token(lam, [deploy_l4.AUTH_FN, deploy_l4.STREAM_FN], False, apply=True) == 0
    assert lam.updates == [(deploy_l4.AUTH_FN, {"RETAIL_OPS_COGNITO_ISSUER": "iss", "RETAIL_OPS_ENV": "dev",
                                                "RETAIL_OPS_ALLOW_DEV_TOKEN": "0"})]


def test_dry_run_and_unchanged_values_write_nothing(capsys):
    lam = FakeLambda({deploy_l4.AUTH_FN: {"RETAIL_OPS_ALLOW_DEV_TOKEN": "0"}})
    assert deploy_l4.set_dev_token(lam, [deploy_l4.AUTH_FN], False, apply=True) == 0
    assert deploy_l4.set_dev_token(lam, [deploy_l4.AUTH_FN], True, apply=False) == 0
    assert lam.updates == []
    assert "would set" in capsys.readouterr().out


def test_missing_authorizer_fails():
    assert deploy_l4.set_dev_token(FakeLambda({}), [deploy_l4.AUTH_FN], False, apply=True) == 1


def test_redeploys_keep_the_switch():
    assert "RETAIL_OPS_ALLOW_DEV_TOKEN" in deploy_l4._PRESERVE_ENV_KEYS
    merged = deploy_l4.merge_preserved({"RETAIL_OPS_ALLOW_DEV_TOKEN": "0"}, {"RETAIL_OPS_ENV": "dev"},
                                       deploy_l4._PRESERVE_ENV_KEYS)
    assert merged == {"RETAIL_OPS_ENV": "dev", "RETAIL_OPS_ALLOW_DEV_TOKEN": "0"}
