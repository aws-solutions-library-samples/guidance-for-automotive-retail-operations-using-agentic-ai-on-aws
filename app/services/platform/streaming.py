"""Streaming generation (spec R2: sub-2s token streaming on the floor).

Two streaming twins of the non-streaming Converse calls, both on bedrock-runtime
`converse_stream` with the managed guardrail in sync stream-processing mode, and
both returning {text, engine, model, guardrail}:

  * `stream_converse()` streams a plain guarded prompt: the twin of a
    `converse(system=..., messages=[prompt], **guardrail_kwargs())` call such as
    the objection agent's REST path (agents/objection._bedrock_response). The
    whole message is guarded (topic, content, word and PII policies on input and
    output); there is no grounding source, so the contextual-grounding check does
    not apply.
  * `stream_ground()` is `generate.ground()`'s twin: facts + passages tagged as
    the grounding source, the question as the guarded query.

On a guardrail intervention Bedrock streams the guardrail's blocked message as
ordinary text deltas, so the deltas are held until the stream's stop reason
(the guardrail's verdict) arrives and only then released through `on_token`:
the blocked message never reaches the caller as an answer. With today's
buffered transport (API Gateway REST + python3.12, ADR 0008) holding the deltas
adds no latency; the SSE wire format still carries one `token` per delta.

Local mode (and any failure) stays honest: the deterministic text is emitted
as ONE token and the returned `engine` says which path ran, so a consumer can
always render something and label it truthfully.
"""
from __future__ import annotations

from typing import Any, Callable

from . import generate as _gen


def _stream_guardrail_kwargs() -> dict[str, Any]:
    """guardrail_kwargs() with the stream-processing mode converse_stream wants:
    sync = evaluate the guardrail before emitting (safer; slightly slower)."""
    gk = _gen.guardrail_kwargs()
    if gk:
        gk["guardrailConfig"]["streamProcessingMode"] = "sync"
    return gk


def _consume(resp: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The text deltas of a converse_stream response, unreleased, and its
    guardrail outcome (generate.guardrail_outcome over the stop reason + trace)."""
    deltas: list[str] = []
    stop_reason, trace = None, {}
    for event in resp.get("stream") or []:
        delta = ((event.get("contentBlockDelta") or {}).get("delta") or {}).get("text")
        if delta:
            deltas.append(delta)
        if "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
        md = event.get("metadata") or {}
        if md.get("trace"):
            trace = md["trace"]
    return deltas, _gen.guardrail_outcome({"stopReason": stop_reason, "trace": trace})


def _failed(e: Exception, fallback: str, on_token: Callable[[str], None]) -> dict[str, Any]:
    on_token(fallback)
    return {"text": fallback, "engine": f"deterministic ({type(e).__name__})", "model": None,
            "guardrail": {"applied": _gen.guardrail_config() is not None, "intervened": False,
                          "error": type(e).__name__}}


def stream_converse(system: str, prompt: str, *, fallback: str, on_token: Callable[[str], None],
                    max_tokens: int = 320, temperature: float = 0.2) -> dict[str, Any]:
    """Stream one guarded Converse call over `prompt`, calling `on_token(delta)`
    per text delta once the guardrail has passed the answer. On an intervention
    (any policy, input or output side) the model's text is withheld and
    `fallback` is emitted as one token, so callers pass a fallback that is safe
    to show in its place; `guardrail` reports what matched. Local mode emits
    `fallback` as one token."""
    if not _gen.enabled():
        on_token(fallback)
        return {"text": fallback, "engine": "deterministic", "model": None,
                "guardrail": {"applied": False, "intervened": False}}
    try:
        import boto3

        rt = boto3.client("bedrock-runtime", region_name=_gen.REGION)
        resp = rt.converse_stream(
            modelId=_gen.MODEL,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            **_stream_guardrail_kwargs(),
        )
        deltas, gr = _consume(resp)
    except Exception as e:  # no access / throttle / offline — stay useful
        return _failed(e, fallback, on_token)
    if gr["intervened"]:
        on_token(fallback)
        return {"text": fallback, "engine": "deterministic (guardrail intervened)", "model": None,
                "guardrail": gr}
    text = "".join(deltas).strip()
    if not text:
        on_token(fallback)
        return {"text": fallback, "engine": "deterministic (empty completion)", "model": None,
                "guardrail": gr}
    for d in deltas:
        on_token(d)
    return {"text": text, "engine": "bedrock", "model": _gen.MODEL, "guardrail": gr}


def stream_ground(question: str, facts: list[str], passages: list[str], *, persona: str,
                  fallback: str, on_token: Callable[[str], None],
                  max_tokens: int = 320, temperature: float = 0.2) -> dict[str, Any]:
    """Stream an answer over `facts` + `passages`, calling `on_token(delta)` per
    text delta once the guardrail has passed it. Returns the same {text, engine,
    model, guardrail} summary as generate.ground(); on an intervention the
    returned text is emitted as one token (the deterministic text for a
    grounding-only intervention). Local mode emits the deterministic text as one
    token."""
    if not _gen.enabled() or (not facts and not passages):
        out = _gen.ground(question, facts, passages, persona=persona, fallback=fallback,
                          max_tokens=max_tokens, temperature=temperature)
        if out.get("text"):
            on_token(out["text"])  # one token; `engine` stays honest about the path
        return out
    try:
        import boto3

        rt = boto3.client("bedrock-runtime", region_name=_gen.REGION)
        ctx = []
        if facts:
            ctx.append("Facts from the system of record:\n" + "\n".join(f"- {f}" for f in facts))
        if passages:
            ctx.append("Retrieved passages:\n" + "\n".join(f"- {p[:600]}" for p in passages[:4]))
        context_text = f"You are {persona}.\n\n" + "\n\n".join(ctx)
        gk = _stream_guardrail_kwargs()
        if gk:
            content = [
                {"guardContent": {"text": {"text": context_text, "qualifiers": ["grounding_source"]}}},
                {"guardContent": {"text": {"text": question, "qualifiers": ["query", "guard_content"]}}},
                {"text": "Answer:"},
            ]
        else:
            content = [{"text": context_text + f"\n\nQuestion: {question}\n\nAnswer:"}]
        resp = rt.converse_stream(
            modelId=_gen.MODEL,
            system=[{"text": _gen._GUARD}],
            messages=[{"role": "user", "content": content}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            **gk,
        )
        deltas, gr = _consume(resp)
    except Exception as e:  # no access / throttle / offline — stay useful
        return _failed(e, fallback, on_token)
    text = "".join(deltas).strip()
    if gr["intervened"]:
        if gr.get("grounding_only"):
            # The MODEL drifted from the supplied context — the deterministic
            # text is the trustworthy answer.
            out = {"text": fallback, "engine": "deterministic (guardrail grounding check)",
                   "model": None, "guardrail": gr}
        else:
            out = {"text": text or fallback, "engine": "bedrock (guardrail intervened)",
                   "model": _gen.MODEL, "guardrail": gr}
        on_token(out["text"])
        return out
    if not text:
        on_token(fallback)
        return {"text": fallback, "engine": "deterministic (empty completion)", "model": None,
                "guardrail": gr}
    for d in deltas:
        on_token(d)
    return {"text": text, "engine": "bedrock", "model": _gen.MODEL, "guardrail": gr}
