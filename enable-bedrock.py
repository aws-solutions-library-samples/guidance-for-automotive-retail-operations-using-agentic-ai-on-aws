#!/usr/bin/env python3
"""Make one Amazon Bedrock foundation model usable in this account and Region.

    python3 enable-bedrock.py '<model-id>' '<region>'

Exit 0 when the model is usable (or access was just granted); non-zero, with the
reason on stderr, otherwise. Safe to re-run: a model that is already available
returns at once and nothing is submitted twice.

What it does, following "Request access to models" in the Amazon Bedrock user
guide (Manage model access using SDK and CLI):
  1. Takes a base model id. A cross-Region inference profile id
     (`us.anthropic.claude-...`, `eu.`, `apac.`, `global.`, ...) is mapped to its
     base model, because access is granted per base model.
  2. GetFoundationModelAvailability. AVAILABLE + AUTHORIZED: done.
  3. Anthropic models only: submits the one-time first-time-use (use case) form
     with PutUseCaseForModelAccess, unless the agreement is already set up or
     pending. The form has no e-mail field; the contact address goes in the use
     case text. Override the neutral defaults with BEDROCK_USE_CASE_COMPANY,
     BEDROCK_USE_CASE_WEBSITE, BEDROCK_USE_CASE_EMAIL and BEDROCK_USE_CASE_TEXT.
  4. Third-party models: ListFoundationModelAgreementOffers (PUBLIC) and
     CreateFoundationModelAgreement with the offer token. First-party models
     (Amazon) have no agreement and are enabled by default; an embeddings model
     that does not report AVAILABLE is probed with one tiny InvokeModel call.
  5. Polls GetFoundationModelAvailability until AVAILABLE (up to
     BEDROCK_ENABLE_WAIT_SECONDS, default 600): an agreement can stay PENDING for
     a few minutes after the form is submitted.

Serverless models are otherwise enabled automatically on their first
invocation, given the AWS Marketplace permissions (aws-marketplace:Subscribe,
Unsubscribe, ViewSubscriptions) and, for Anthropic, the use case form. This
script does the same steps up front, so a deployment fails early and clearly.

Documentation:
  https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html
  https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetFoundationModelAvailability.html
  https://docs.aws.amazon.com/bedrock/latest/APIReference/API_PutUseCaseForModelAccess.html
  https://docs.aws.amazon.com/bedrock/latest/APIReference/API_ListFoundationModelAgreementOffers.html
  https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateFoundationModelAgreement.html
  https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html   (model ids)
  https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Callable

# Geographic and global cross-Region inference profile prefixes.
PROFILE_PREFIXES = ("us-gov.", "us.", "eu.", "apac.", "au.", "jp.", "ca.", "global.")
MODEL_ID = re.compile(r"^[a-z0-9-]+\.[A-Za-z0-9._:-]+$")
REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
DEFAULT_WAIT_S = 600
POLL_S = 15
DEFAULT_EMAIL = "wwso-guidance-deployments-ignore@amazon.com"


class EnableError(RuntimeError):
    """The model cannot be made usable; the message says why and what to do."""


def base_model_id(model_id: str) -> str:
    """`us.anthropic.claude-x-v1:0` -> `anthropic.claude-x-v1:0`; a base id is returned unchanged."""
    model_id = model_id.strip()
    for p in PROFILE_PREFIXES:
        if model_id.startswith(p) and model_id.count(".") >= 2:
            return model_id[len(p):]
    return model_id


def provider(model_id: str) -> str:
    return model_id.split(".", 1)[0]


def use_case_form() -> dict[str, str]:
    """The Anthropic first-time-use form (field names and limits from the user guide)."""
    email = os.environ.get("BEDROCK_USE_CASE_EMAIL") or DEFAULT_EMAIL
    text = os.environ.get("BEDROCK_USE_CASE_TEXT") or (
        "Automated deployment and validation of AWS Solutions Guidance sample code in a test account: "
        "an agentic AI reference implementation for automotive retail operations that answers staff "
        "questions over synthetic data. No end customers. Contact: " + email)
    return {
        "companyName": (os.environ.get("BEDROCK_USE_CASE_COMPANY") or "AWS Solutions Guidance sample code")[:128],
        "companyWebsite": (os.environ.get("BEDROCK_USE_CASE_WEBSITE") or "https://aws.amazon.com/solutions/guidance/")[:128],
        "intendedUsers": "0",                      # 0 internal, 1 external, 2 both
        "industryOption": "Other",
        "otherIndustryOption": "Automotive retail sample code",
        "useCases": text[:8192],
    }


def _code(e: BaseException) -> str:
    return str((getattr(e, "response", None) or {}).get("Error", {}).get("Code", "")) or type(e).__name__


def _msg(e: BaseException) -> str:
    return str((getattr(e, "response", None) or {}).get("Error", {}).get("Message", "")) or str(e)


def _usable(a: dict[str, Any]) -> bool:
    return ((a.get("agreementAvailability") or {}).get("status") == "AVAILABLE"
            and a.get("authorizationStatus") == "AUTHORIZED"
            and a.get("entitlementAvailability") == "AVAILABLE"
            and a.get("regionAvailability") == "AVAILABLE")


def _describe(a: dict[str, Any]) -> str:
    agr = a.get("agreementAvailability") or {}
    text = (f"agreement={agr.get('status')} authorization={a.get('authorizationStatus')} "
            f"entitlement={a.get('entitlementAvailability')} region={a.get('regionAvailability')}")
    return text + (f" ({agr['errorMessage']})" if agr.get("errorMessage") else "")


def _availability(bedrock, model: str) -> dict[str, Any]:
    try:
        return bedrock.get_foundation_model_availability(modelId=model)
    except Exception as e:  # noqa: BLE001 - every failure becomes one clear message
        code = _code(e)
        if code == "AccessDeniedException":
            raise EnableError(f"not allowed to read model access for {model}: grant "
                              "bedrock:GetFoundationModelAvailability to this identity") from None
        if code in ("ValidationException", "ResourceNotFoundException"):
            raise EnableError(f"{model} is not a Bedrock foundation model id in this Region ({_msg(e)}); "
                              "see https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html") from None
        raise EnableError(f"GetFoundationModelAvailability failed for {model}: {code}: {_msg(e)}") from None


def _submit_use_case(bedrock, out: Callable[[str], None]) -> None:
    try:
        bedrock.put_use_case_for_model_access(formData=json.dumps(use_case_form()).encode())
        out("  submitted the Anthropic first-time-use (use case) form")
    except Exception as e:  # noqa: BLE001
        code = _code(e)
        if code == "AccessDeniedException":
            raise EnableError("not allowed to submit the Anthropic use case form: grant "
                              "bedrock:PutUseCaseForModelAccess to this identity") from None
        if code in ("ConflictException", "ResourceInUseException"):
            out("  the Anthropic use case form is already on file")
            return
        raise EnableError(f"PutUseCaseForModelAccess failed: {code}: {_msg(e)}") from None


def _create_agreement(bedrock, model: str, out: Callable[[str], None]) -> bool:
    """True when an agreement now exists or is being set up; False when the model has none."""
    try:
        offers = bedrock.list_foundation_model_agreement_offers(modelId=model, offerType="PUBLIC").get("offers") or []
    except Exception as e:  # noqa: BLE001
        code, msg = _code(e), _msg(e)
        if code == "ValidationException" and "not supported" in msg.lower():
            out("  no agreement for this model (first-party model, enabled by default)")
            return False
        if code == "AccessDeniedException":
            raise EnableError("not allowed to list model offers: grant bedrock:ListFoundationModelAgreementOffers "
                              "and aws-marketplace:ViewSubscriptions to this identity") from None
        raise EnableError(f"ListFoundationModelAgreementOffers failed for {model}: {code}: {msg}") from None
    if not offers or not offers[0].get("offerToken"):
        raise EnableError(f"no public offer for {model} in this Region")
    try:
        bedrock.create_foundation_model_agreement(offerToken=offers[0]["offerToken"], modelId=model)
        out("  created the model agreement")
    except Exception as e:  # noqa: BLE001
        code = _code(e)
        if code in ("ConflictException", "ResourceInUseException"):
            out("  the model agreement already exists")
        elif code == "AccessDeniedException":
            raise EnableError("not allowed to create the model agreement: grant "
                              "bedrock:CreateFoundationModelAgreement and aws-marketplace:Subscribe, "
                              "aws-marketplace:ViewSubscriptions to this identity") from None
        else:
            raise EnableError(f"CreateFoundationModelAgreement failed for {model}: {code}: {_msg(e)}") from None
    return True


def _probe_embeddings(runtime, model: str) -> bool:
    """One tiny embeddings call (first use enables a default-on model). True when it worked."""
    if runtime is None or not model.startswith("amazon.titan-embed"):
        return False
    try:
        runtime.invoke_model(modelId=model, contentType="application/json", accept="application/json",
                             body=json.dumps({"inputText": "ok"}))
        return True
    except Exception:  # noqa: BLE001 - reported by the caller as not usable
        return False


def enable(model_id: str, region: str, *, bedrock, runtime=None, wait_s: int = DEFAULT_WAIT_S,
           poll_s: int = POLL_S, sleep: Callable[[float], None] = time.sleep,
           out: Callable[[str], None] = print) -> str:
    """Make `model_id` usable; returns a one-line summary or raises EnableError."""
    if not REGION.fullmatch(region or ""):
        raise EnableError(f"{region!r} is not an AWS Region name (for example us-east-1)")
    model = base_model_id(model_id or "")
    if not MODEL_ID.fullmatch(model):
        raise EnableError(f"{model_id!r} is not a Bedrock model id (for example anthropic.claude-sonnet-4-5-20250929-v1:0)")
    if model != model_id.strip():
        out(f"  {model_id} is a cross-Region inference profile; enabling its base model {model}")
    a = _availability(bedrock, model)
    out(f"  {model} in {region}: {_describe(a)}")
    if _usable(a):
        return f"{model} is available in {region}"
    if a.get("regionAvailability") == "NOT_AVAILABLE":
        raise EnableError(f"{model} is not offered in {region}; choose a Region where it is")
    if a.get("authorizationStatus") == "NOT_AUTHORIZED":
        raise EnableError(f"this identity is not authorized to use {model} (an IAM policy or SCP denies it)")
    status = (a.get("agreementAvailability") or {}).get("status")
    if status == "ERROR":
        raise EnableError(f"the agreement for {model} is in ERROR: {_describe(a)}")
    has_agreement = True
    if status != "PENDING":
        if provider(model) == "anthropic":
            _submit_use_case(bedrock, out)
        has_agreement = _create_agreement(bedrock, model, out)
    if not has_agreement and _probe_embeddings(runtime, model):
        return f"{model} answered a test call in {region}"
    deadline = wait_s
    while True:
        a = _availability(bedrock, model)
        if _usable(a):
            return f"{model} is available in {region}"
        agr = (a.get("agreementAvailability") or {}).get("status")
        if agr == "ERROR":
            raise EnableError(f"the agreement for {model} failed: {_describe(a)}")
        if deadline <= 0:
            raise EnableError(f"{model} is still not usable after {wait_s} s ({_describe(a)}). Anthropic "
                              "agreements can stay PENDING for a while after the use case form; AWS sends an "
                              "e-mail when it is ready. Re-run this script then.")
        out(f"  waiting for access ({_describe(a)}) ...")
        sleep(poll_s)
        deadline -= poll_s


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] in ("-h", "--help"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        print("usage: python3 enable-bedrock.py '<model-id>' '<region>'", file=sys.stderr)
        return 2
    model_id, region = args
    try:
        import boto3
    except ImportError:
        print("ERROR: boto3 is not installed (pip install --upgrade boto3)", file=sys.stderr)
        return 1
    wait_s = int(os.environ.get("BEDROCK_ENABLE_WAIT_SECONDS") or DEFAULT_WAIT_S)
    print(f"Enabling Amazon Bedrock model {model_id} in {region}")
    try:
        summary = enable(model_id, region, bedrock=boto3.client("bedrock", region_name=region),
                         runtime=boto3.client("bedrock-runtime", region_name=region), wait_s=wait_s)
    except EnableError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - credentials, network: one line, non-zero
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"OK: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
