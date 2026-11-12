"""Outreach interface — SES/SNS (REAL) | Amazon Connect (spec §4.5 A2).

`OutreachProvider.send(channel, to, subject, body, consent)` is the channel-
agnostic send the spec's A2 `schedule_outreach` calls. Implementations:

  BasicOutreachProvider  — SES email + SNS SMS (both usable in this account) +
                           a dealer task fallback; the default (ENABLE_CONNECT
                           false).
  ConnectOutreachProvider— Amazon Connect voice/chat when ENABLE_CONNECT=true
                           (not provisioned here; documented swap).

Every send is consent-checked (CUST_Consent) and quiet-hours honored by the
caller (nurture policy); this layer just delivers. Sends are also recorded to
CUST_Messages so the customer timeline and the "opt-out stops within a cycle"
rule (acceptance #13) hold regardless of channel.
"""
from __future__ import annotations

import datetime as _dt
import os
import uuid
from typing import Any

from ..common import ddb

_MESSAGES = "CUST_Messages"
_CONSENT = "CUST_Consent"
_TASKS = "DEALER_Tasks"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _connect_enabled() -> bool:
    return os.environ.get("ENABLE_CONNECT", "false").lower() == "true"


def _region() -> str:
    """The SES/SNS region: the Lambda's own region (AWS_REGION), as ddb.py does."""
    return os.environ.get("AWS_REGION", "us-east-1")


def _has_consent(customer_id: str | None, channel: str, env_prefix: str | None) -> bool:
    if not customer_id:
        return True
    rows = ddb.scan_filter(_CONSENT, "customer_id", customer_id, env_prefix)
    if not rows:
        return True  # no explicit record = default opt-in in the sandbox
    latest = max(rows, key=lambda r: r.get("updated_at") or "")
    # opt-out on the channel or globally blocks the send
    if str(latest.get("status", "")).lower() in ("opted_out", "unsubscribed"):
        return False
    blocked = latest.get("blocked_channels") or []
    return channel not in blocked


class OutreachProvider:
    def send(self, channel: str, to: str, subject: str, body: str,
             customer_id: str | None = None, dealer_id: str | None = None,
             env_prefix: str | None = None) -> dict[str, Any]:
        raise NotImplementedError


class BasicOutreachProvider(OutreachProvider):
    """SES email / SNS SMS + dealer-task fallback. Records CUST_Messages."""

    def _record(self, customer_id, channel, subject, body, status, dealer_id, env_prefix, extra=None):
        mid = f"MSG-{uuid.uuid4().hex[:12]}"
        row = {"message_id": mid, "customer_id": customer_id, "channel": channel,
               "subject": subject, "body": body[:2000], "direction": "outbound",
               "status": status, "dealer_id": dealer_id, "sent_at": _now(),
               "source_ref": "platform.BasicOutreachProvider"}
        if extra:
            row.update(extra)
        try:
            ddb.put_item(_MESSAGES, row, env_prefix)
        except Exception:
            pass
        return mid

    def send(self, channel, to, subject, body, customer_id=None, dealer_id=None, env_prefix=None):
        if not _has_consent(customer_id, channel, env_prefix):
            return {"sent": False, "reason": "no_consent", "channel": channel, "customer_id": customer_id}
        delivered = False
        detail = None
        try:
            import boto3
            if channel == "email" and to:
                # The sender is deployment configuration (a verified SES identity),
                # never a built-in default: a missing value is recorded on the
                # message as the delivery failure and the dealer-task fallback runs.
                src = os.environ.get("RETAIL_OPS_SES_FROM")
                if not src:
                    raise RuntimeError("RETAIL_OPS_SES_FROM must be set (verified SES identity)")
                ses = boto3.client("ses", region_name=_region())
                r = ses.send_email(Source=src, Destination={"ToAddresses": [to]},
                                   Message={"Subject": {"Data": subject},
                                            "Body": {"Text": {"Data": body}}})
                delivered = True; detail = r.get("MessageId")
            elif channel == "sms" and to:
                sns = boto3.client("sns", region_name=_region())
                r = sns.publish(PhoneNumber=to, Message=body)
                delivered = True; detail = r.get("MessageId")
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
        # dealer-task fallback when a channel isn't deliverable
        if not delivered:
            try:
                ddb.put_item(_TASKS, {"task_id": f"TASK-{uuid.uuid4().hex[:10]}",
                             "customer_id": customer_id, "dealer_id": dealer_id,
                             "text": f"Follow up ({channel}): {subject}", "status": "open",
                             "created_at": _now(), "source_ref": "outreach_fallback"}, env_prefix)
            except Exception:
                pass
        mid = self._record(customer_id, channel, subject, body,
                           "sent" if delivered else "queued_task", dealer_id, env_prefix,
                           {"provider_ref": detail})
        return {"sent": delivered, "channel": channel, "message_id": mid,
                "provider_ref": detail, "fallback_task": not delivered}


class ConnectOutreachProvider(BasicOutreachProvider):
    """Amazon Connect voice/chat (ENABLE_CONNECT=true). Not provisioned in this
    account — delegates to Basic for email/SMS and records a voice intent."""

    def send(self, channel, to, subject, body, customer_id=None, dealer_id=None, env_prefix=None):
        if channel in ("voice", "callback"):
            mid = self._record(customer_id, channel, subject, body, "connect_queued", dealer_id, env_prefix)
            return {"sent": False, "channel": channel, "message_id": mid,
                    "note": "Amazon Connect not provisioned; voice intent recorded"}
        return super().send(channel, to, subject, body, customer_id, dealer_id, env_prefix)


def get_outreach_provider() -> OutreachProvider:
    return ConnectOutreachProvider() if _connect_enabled() else BasicOutreachProvider()
