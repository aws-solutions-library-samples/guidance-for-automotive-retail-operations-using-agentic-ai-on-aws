"""enable-bedrock.py (repo root): the Guidance model-access helper, against a fake Bedrock client."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("enable_bedrock", ROOT / "enable-bedrock.py")
eb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eb)

OPUS = "anthropic.claude-opus-4-5-20251101-v1:0"
TITAN = "amazon.titan-embed-text-v2:0"


class ClientError(Exception):
    """botocore.exceptions.ClientError's shape: .response['Error'] has Code and Message."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


def _avail(status="AVAILABLE", auth="AUTHORIZED", ent="AVAILABLE", region="AVAILABLE", error=None):
    agr = {"status": status, **({"errorMessage": error} if error else {})}
    return {"agreementAvailability": agr, "authorizationStatus": auth,
            "entitlementAvailability": ent, "regionAvailability": region}


class FakeBedrock:
    def __init__(self, states, *, offers=None, offers_error=None, put_error=None, create_error=None,
                 avail_error=None):
        self.states = list(states)           # successive GetFoundationModelAvailability answers
        self.offers = offers if offers is not None else [{"offerId": "o-1", "offerToken": "tok-1"}]
        self.offers_error, self.put_error, self.create_error = offers_error, put_error, create_error
        self.avail_error = avail_error
        self.calls: list[tuple[str, dict]] = []

    def get_foundation_model_availability(self, **kw):
        self.calls.append(("availability", kw))
        if self.avail_error:
            raise self.avail_error
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def put_use_case_for_model_access(self, **kw):
        self.calls.append(("put_use_case", kw))
        if self.put_error:
            raise self.put_error
        return {}

    def list_foundation_model_agreement_offers(self, **kw):
        self.calls.append(("offers", kw))
        if self.offers_error:
            raise self.offers_error
        return {"offers": self.offers}

    def create_foundation_model_agreement(self, **kw):
        self.calls.append(("create", kw))
        if self.create_error:
            raise self.create_error
        return {"modelId": kw["modelId"]}

    def names(self):
        return [c[0] for c in self.calls]


class FakeRuntime:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def invoke_model(self, **kw):
        self.calls.append(kw)
        if not self.ok:
            raise ClientError("AccessDeniedException", "no access")
        return {"body": b"{}"}


def _run(bedrock, model=OPUS, region="us-east-1", runtime=None, wait_s=60):
    lines: list[str] = []
    out = eb.enable(model, region, bedrock=bedrock, runtime=runtime, wait_s=wait_s, poll_s=15,
                    sleep=lambda s: None, out=lines.append)
    return out, lines


# --- happy paths and idempotency -------------------------------------------------------

def test_already_available_makes_no_change():
    b = FakeBedrock([_avail()])
    summary, _ = _run(b)
    assert summary == f"{OPUS} is available in us-east-1"
    assert b.names() == ["availability"]


def test_inference_profile_maps_to_its_base_model():
    b = FakeBedrock([_avail()])
    _, lines = _run(b, model="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert b.calls[0] == ("availability", {"modelId": "anthropic.claude-sonnet-4-5-20250929-v1:0"})
    assert "cross-Region inference profile" in lines[0]


@pytest.mark.parametrize("given,base", [
    ("global.anthropic.claude-opus-4-5-20251101-v1:0", OPUS),
    ("eu.anthropic.claude-opus-4-5-20251101-v1:0", OPUS),
    (OPUS, OPUS),
    (TITAN, TITAN),
])
def test_base_model_id(given, base):
    assert eb.base_model_id(given) == base


def test_anthropic_first_use_submits_the_form_then_the_agreement_and_waits():
    b = FakeBedrock([_avail("NOT_AVAILABLE"), _avail("PENDING"), _avail()])
    summary, _ = _run(b)
    assert summary.endswith("is available in us-east-1")
    assert b.names() == ["availability", "put_use_case", "offers", "create", "availability", "availability"]
    form = json.loads(next(kw for n, kw in b.calls if n == "put_use_case")["formData"])
    assert set(form) == {"companyName", "companyWebsite", "intendedUsers", "industryOption",
                         "otherIndustryOption", "useCases"}
    assert form["intendedUsers"] == "0" and "wwso-guidance-deployments-ignore@amazon.com" in form["useCases"]
    assert all(len(form[k]) <= 128 for k in ("companyName", "companyWebsite", "industryOption", "otherIndustryOption"))
    assert next(kw for n, kw in b.calls if n == "create") == {"offerToken": "tok-1", "modelId": OPUS}
    assert next(kw for n, kw in b.calls if n == "offers")["offerType"] == "PUBLIC"


def test_pending_agreement_is_not_submitted_again():
    b = FakeBedrock([_avail("PENDING"), _avail()])
    _run(b)
    assert "put_use_case" not in b.names() and "create" not in b.names()


def test_existing_form_and_agreement_are_fine():
    b = FakeBedrock([_avail("NOT_AVAILABLE"), _avail()], put_error=ClientError("ConflictException"),
                    create_error=ClientError("ConflictException"))
    summary, lines = _run(b)
    assert summary.endswith("is available in us-east-1")
    assert any("already on file" in ln for ln in lines) and any("already exists" in ln for ln in lines)


def test_non_anthropic_third_party_model_skips_the_form():
    b = FakeBedrock([_avail("NOT_AVAILABLE"), _avail()])
    _run(b, model="cohere.command-r-v1:0")
    assert "put_use_case" not in b.names() and "create" in b.names()


def test_first_party_model_without_agreement_is_probed():
    b = FakeBedrock([_avail("NOT_AVAILABLE")],
                    offers_error=ClientError("ValidationException", "Agreement not supported for this model"))
    rt = FakeRuntime(ok=True)
    summary, _ = _run(b, model=TITAN, runtime=rt)
    assert "answered a test call" in summary and rt.calls[0]["modelId"] == TITAN


# --- failures are clear and non-zero ---------------------------------------------------------

def test_timeout_while_pending_fails_with_a_reason():
    b = FakeBedrock([_avail("NOT_AVAILABLE"), _avail("PENDING")])
    with pytest.raises(eb.EnableError, match="still not usable after 60 s"):
        _run(b, wait_s=60)


def test_agreement_error_fails():
    b = FakeBedrock([_avail("ERROR", error="payment method missing")])
    with pytest.raises(eb.EnableError, match="payment method missing"):
        _run(b)


def test_region_not_offered_fails():
    b = FakeBedrock([_avail("NOT_AVAILABLE", region="NOT_AVAILABLE")])
    with pytest.raises(eb.EnableError, match="not offered in us-east-1"):
        _run(b)


def test_denied_identity_fails():
    b = FakeBedrock([_avail("NOT_AVAILABLE", auth="NOT_AUTHORIZED")])
    with pytest.raises(eb.EnableError, match="not authorized"):
        _run(b)


def test_missing_permission_names_the_action():
    b = FakeBedrock([_avail("NOT_AVAILABLE")], put_error=ClientError("AccessDeniedException", "denied"))
    with pytest.raises(eb.EnableError, match="bedrock:PutUseCaseForModelAccess"):
        _run(b)


def test_unknown_model_fails():
    b = FakeBedrock([_avail()], avail_error=ClientError("ValidationException", "bad model"))
    with pytest.raises(eb.EnableError, match="not a Bedrock foundation model id"):
        _run(b, model="anthropic.claude-nope-v9:0")


@pytest.mark.parametrize("model,region", [("not a model", "us-east-1"), (OPUS, "useast1"), (OPUS, "")])
def test_bad_arguments_fail_before_any_call(model, region):
    b = FakeBedrock([_avail()])
    with pytest.raises(eb.EnableError):
        _run(b, model=model, region=region)
    assert b.calls == []


def test_cli_usage_without_arguments(capsys):
    assert eb.main([]) == 2
    assert "usage: python3 enable-bedrock.py" in capsys.readouterr().err


def test_cli_exit_codes(monkeypatch, capsys):
    fake = FakeBedrock([_avail()])

    class _Boto3:
        @staticmethod
        def client(name, region_name=None):
            return fake if name == "bedrock" else FakeRuntime()

    monkeypatch.setitem(__import__("sys").modules, "boto3", _Boto3)
    assert eb.main([OPUS, "us-east-1"]) == 0
    assert "OK:" in capsys.readouterr().out
    fake.states = [_avail("ERROR", error="nope")]
    assert eb.main([OPUS, "us-east-1"]) == 1
    assert "ERROR:" in capsys.readouterr().err
