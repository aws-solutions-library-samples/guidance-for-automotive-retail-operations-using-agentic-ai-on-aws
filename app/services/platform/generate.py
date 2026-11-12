"""Grounded generation on Amazon Bedrock (the model tier, spec §2.6/§4).

One helper every agent uses to turn retrieved facts + passages into an answer
in the agent's own voice. It is deliberately grounding-only: the prompt forbids
going beyond the supplied context, so an answer stays citable by the same
source_refs the caller already collected.

Model safety is layered (spec §2.4 "Bedrock + Guardrails"):
  1. the grounding-only system prompt (always);
  2. the managed Bedrock Guardrail provisioned by app/infra/deploy_guardrails.py,
     attached to EVERY Converse call in the app through `guardrail_kwargs()`
     (content, denied topics, scare words, C4 identifiers, contextual
     grounding). The nightly store digest carries its own copy ("digest": the
     same policies, a relaxed grounding check; user decision 2026-10-22); every
     other call carries the main one. `ground()` tags the context as the grounding source and the
     question as the query so the contextual-grounding filter can score the
     answer against exactly the facts the caller supplied;
  3. the callers' own deterministic classifiers (A2 refuse/escalate, A6 rules).

Falls back to the caller's deterministic text when the model is unreachable
(no access, throttle, offline sandbox), and always reports which path ran via
the returned `engine`, so the UI can be honest about it. When the guardrail
intervenes, the returned text is the guardrail's blocked message and
`guardrail.intervened` is True.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..common import envname
from .mode import integration_mode

MODEL = os.environ.get("RETAIL_OPS_BEDROCK_MODEL", "us.anthropic.claude-opus-4-5-20251101-v1:0")
REGION = os.environ.get("AWS_REGION", "us-east-1")
_INFRA = Path(__file__).resolve().parents[2] / "infra"
_GUARDRAIL_CFG = envname.config_file(_INFRA, "guardrail")
_DIGEST_GUARDRAIL_CFG = envname.config_file(_INFRA, "guardrail-digest")

# The guardrails a call may name (app/infra/deploy_guardrails.py) -> env-var stem:
# "main" carries every call; "digest" is the nightly store digest's own copy with
# the same content, topic, word and PII policies and a relaxed grounding check
# (user decision 2026-10-22). A missing "digest" falls back to "main", never to none.
GUARDRAILS: dict[str, str] = {"main": "RETAIL_OPS_GUARDRAIL", "digest": "RETAIL_OPS_DIGEST_GUARDRAIL"}


def _config_path(name: str) -> Path:
    return _GUARDRAIL_CFG if name == "main" else _DIGEST_GUARDRAIL_CFG


# Generation is on only in aws mode; the local sandbox stays deterministic so
# tests and offline demos never depend on a network call.
def enabled() -> bool:
    return integration_mode() == "aws"


def _named_config(name: str) -> dict[str, str] | None:
    env, path = GUARDRAILS[name], _config_path(name)
    gid, ver = os.environ.get(f"{env}_ID"), os.environ.get(f"{env}_VERSION")
    if not gid and path.exists():
        try:
            cfg = json.loads(path.read_text())
            gid, ver = cfg.get("guardrail_id"), str(cfg.get("version") or "DRAFT")
        except Exception:  # noqa: BLE001 - an unreadable config file means no guardrail of that name
            gid = None
    if not gid:
        return None
    return {"guardrailIdentifier": gid, "guardrailVersion": ver or "DRAFT"}


def guardrail_config(name: str = "main") -> dict[str, str] | None:
    """{guardrailIdentifier, guardrailVersion} from env (RETAIL_OPS_GUARDRAIL_ID /
    RETAIL_OPS_GUARDRAIL_VERSION, or RETAIL_OPS_DIGEST_GUARDRAIL_* for "digest",
    set by the deployers) or the local config file; None when no guardrail is
    provisioned (calls then run prompt-only). An unprovisioned "digest" falls back
    to the main guardrail."""
    if name not in GUARDRAILS:
        raise ValueError(f"unknown guardrail {name!r} (expected one of {sorted(GUARDRAILS)})")
    return _named_config(name) or (_named_config("main") if name != "main" else None)


def guardrail_kwargs(name: str = "main") -> dict[str, Any]:
    """Keyword arguments to splat into bedrock-runtime `converse(...)`."""
    cfg = guardrail_config(name)
    return {"guardrailConfig": {**cfg, "trace": "enabled"}} if cfg else {}


def guardrail_outcome(resp: dict[str, Any], name: str = "main") -> dict[str, Any]:
    """Summarise a Converse response's guardrail result for the caller/UI."""
    cfg = guardrail_config(name)
    if not cfg:
        return {"applied": False, "intervened": False}
    intervened = resp.get("stopReason") == "guardrail_intervened"
    out: dict[str, Any] = {"applied": True, "intervened": intervened, "id": cfg["guardrailIdentifier"],
                           "version": cfg["guardrailVersion"]}
    trace = ((resp.get("trace") or {}).get("guardrail") or {})
    if intervened:
        hits: list[str] = []
        input_side = False
        for side in ("inputAssessment", "outputAssessments"):
            node = trace.get(side) or {}
            assessments = node.values() if isinstance(node, dict) else []
            for a in assessments:
                for item in (a if isinstance(a, list) else [a]):
                    for policy, body in (item or {}).items():
                        if isinstance(body, dict):
                            for key in ("topics", "filters", "customWords", "managedWordLists", "piiEntities"):
                                for h in body.get(key, []) or []:
                                    if h.get("action") not in (None, "NONE"):
                                        hits.append(f"{policy}:{h.get('name') or h.get('type') or h.get('match')}")
                                        input_side = input_side or side == "inputAssessment"
        out["matched"] = sorted(set(hits))[:10]
        out["input_side"] = input_side          # the ASK was fenced (never retried) vs the model's wording
        # A grounding-only intervention means the MODEL drifted from the context, not
        # that the user asked something fenced: callers should fall back to the
        # deterministic text (which is the context itself) rather than show a refusal.
        out["grounding_only"] = bool(hits) and all(h.startswith("contextualGroundingPolicy") for h in hits)
    return out


_GUARD = (
    "You are an assistant inside a dealership/OEM system. Answer ONLY from the context given. "
    "If the context does not contain the answer, say what is missing and name the next step. "
    "Never invent prices, rates, incentives, coverage, dates or availability. "
    "No credit, legal or approval decisions — hand those to a person. "
    "Be concise and specific: at most 4 sentences, plain language, no preamble."
)
# Appended for the one retry after a grounding-only guardrail intervention.
_REGROUND = (
    " Your previous answer was rejected by the safety check. This time restate ONLY facts that appear in "
    "the context — same numbers, names and labels — as plain sentences: no headings, tables, advice, next "
    "steps, inferred totals, and no characterization of the customer's credit, approval, qualification or "
    "candidacy for anything."
)


def ground(question: str, facts: list[str], passages: list[str], *, persona: str,
           fallback: str, max_tokens: int = 320, temperature: float = 0.2,
           guardrail: str = "main") -> dict[str, Any]:
    """Synthesize an answer over `facts` (structured, from read models) and
    `passages` (retrieved documents). Returns {text, engine, model, guardrail}.
    `guardrail` names the guardrail (GUARDRAILS) the call carries: "main" unless
    the caller has its own, like the store digest's "digest"."""
    if not enabled() or (not facts and not passages):
        return {"text": fallback, "engine": "deterministic", "model": None,
                "guardrail": {"applied": False, "intervened": False}}
    try:
        import boto3

        rt = boto3.client("bedrock-runtime", region_name=REGION)
        ctx = []
        if facts:
            ctx.append("Facts from the system of record:\n" + "\n".join(f"- {f}" for f in facts))
        if passages:
            ctx.append("Retrieved passages:\n" + "\n".join(f"- {p[:600]}" for p in passages[:4]))
        context_text = f"You are {persona}.\n\n" + "\n\n".join(ctx)
        gk = guardrail_kwargs(guardrail)
        if gk:
            # Tag context as the grounding source and the question as the query so the
            # guardrail's contextual-grounding filter scores the answer against them.
            content = [
                {"guardContent": {"text": {"text": context_text, "qualifiers": ["grounding_source"]}}},
                # "query" feeds the grounding check; "guard_content" makes the question itself
                # subject to the topic / content / word policies (without it, guarded=0).
                {"guardContent": {"text": {"text": question, "qualifiers": ["query", "guard_content"]}}},
                {"text": "Answer:"},
            ]
        else:
            content = [{"text": context_text + f"\n\nQuestion: {question}\n\nAnswer:"}]
        def _call(system_text: str) -> tuple[str, dict[str, Any]]:
            resp = rt.converse(
                modelId=MODEL,
                system=[{"text": system_text}],
                messages=[{"role": "user", "content": content}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
                **gk,
            )
            return (resp["output"]["message"]["content"][0]["text"] or "").strip(), guardrail_outcome(resp, guardrail)

        text, gr = _call(_GUARD)
        regrounded = False
        if gr["intervened"] and not gr.get("input_side"):
            # The MODEL's wording tripped the output check — drift from the context (headings, advice,
            # inferred numbers) or a characterization the topic classifier read as a credit call ("strong
            # upgrade candidate") — not a fenced ask. One retry restating facts only; the guardrail still
            # screens that retry, so a genuinely fenced answer stays blocked (2026-10-27/28 eval: one
            # A6 answer per run and 2 of 3 A4 equity answers fell back or were withheld).
            text, gr = _call(_GUARD + _REGROUND)
            regrounded = True
        if gr["intervened"]:
            if gr.get("grounding_only"):
                return {"text": fallback, "engine": "deterministic (guardrail grounding check)", "model": None,
                        "guardrail": gr}
            return {"text": text or fallback, "engine": "bedrock (guardrail intervened)", "model": MODEL,
                    "guardrail": gr}
        if regrounded:
            return {"text": text or fallback, "engine": "bedrock (regrounded)" if text else "deterministic (empty completion)",
                    "model": MODEL if text else None, "guardrail": gr}
        if not text:
            return {"text": fallback, "engine": "deterministic (empty completion)", "model": None,
                    "guardrail": gr}
        return {"text": text, "engine": "bedrock", "model": MODEL, "guardrail": gr}
    except Exception as e:  # no access / throttle / offline — stay useful
        return {"text": fallback, "engine": f"deterministic ({type(e).__name__})", "model": None,
                "guardrail": {"applied": guardrail_config(guardrail) is not None, "intervened": False,
                              "error": type(e).__name__}}
