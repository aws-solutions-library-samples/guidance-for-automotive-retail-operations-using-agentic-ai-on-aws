"""Model-tier guardrail plumbing (generate.guardrail_kwargs / ground) and the
knowledge interface mode switch. Offline: Bedrock is stubbed."""
from __future__ import annotations

import pytest

from app.services.platform import generate


def test_no_guardrail_configured_means_prompt_only(monkeypatch):
    monkeypatch.delenv("RETAIL_OPS_GUARDRAIL_ID", raising=False)
    monkeypatch.setattr(generate, "_GUARDRAIL_CFG", generate._GUARDRAIL_CFG.with_name("nope.json"))
    assert generate.guardrail_config() is None
    assert generate.guardrail_kwargs() == {}
    assert generate.guardrail_outcome({"stopReason": "end_turn"}) == {"applied": False, "intervened": False}


def test_guardrail_from_env_is_attached_with_trace(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-123")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_VERSION", "2")
    kw = generate.guardrail_kwargs()
    assert kw == {"guardrailConfig": {"guardrailIdentifier": "gr-123", "guardrailVersion": "2", "trace": "enabled"}}


def test_the_digest_guardrail_is_its_own_and_falls_back_to_main_never_to_none(monkeypatch, tmp_path):
    monkeypatch.setattr(generate, "_DIGEST_GUARDRAIL_CFG", tmp_path / "missing.json")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-main")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_VERSION", "1")
    monkeypatch.delenv("RETAIL_OPS_DIGEST_GUARDRAIL_ID", raising=False)
    # not provisioned: the digest carries the main guardrail
    assert generate.guardrail_config("digest") == {"guardrailIdentifier": "gr-main", "guardrailVersion": "1"}
    monkeypatch.setenv("RETAIL_OPS_DIGEST_GUARDRAIL_ID", "gr-digest")
    monkeypatch.setenv("RETAIL_OPS_DIGEST_GUARDRAIL_VERSION", "3")
    assert generate.guardrail_kwargs("digest")["guardrailConfig"]["guardrailIdentifier"] == "gr-digest"
    # every other call still carries the main one
    assert generate.guardrail_kwargs()["guardrailConfig"]["guardrailIdentifier"] == "gr-main"
    with pytest.raises(ValueError):
        generate.guardrail_config("loose")


def test_ground_sends_the_named_guardrail(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-main")
    monkeypatch.setenv("RETAIL_OPS_DIGEST_GUARDRAIL_ID", "gr-digest")
    sent = []

    class _RT:
        def converse(self, **kw):
            sent.append(kw["guardrailConfig"]["guardrailIdentifier"])
            return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "ok"}]}}}

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    generate.ground("q", ["f"], [], persona="x", fallback="fb")
    out = generate.ground("q", ["f"], [], persona="x", fallback="fb", guardrail="digest")
    assert sent == ["gr-main", "gr-digest"] and out["guardrail"]["id"] == "gr-digest"


def test_digest_guardrail_spec_keeps_every_policy_and_relaxes_only_grounding():
    from app.infra import deploy_guardrails as dg
    main, digest = dg._spec(), dg._digest_spec()
    assert digest["name"] == dg.DIGEST_NAME != main["name"]
    for key in ("topicPolicyConfig", "contentPolicyConfig", "wordPolicyConfig",
                "sensitiveInformationPolicyConfig", "crossRegionConfig",
                "blockedInputMessaging", "blockedOutputsMessaging"):
        assert digest[key] == main[key], key
    thresholds = {f["type"]: f["threshold"] for f in digest["contextualGroundingPolicyConfig"]["filtersConfig"]}
    assert thresholds == {"GROUNDING": 0.2}                      # relevance check off
    main_t = {f["type"]: f["threshold"] for f in main["contextualGroundingPolicyConfig"]["filtersConfig"]}
    assert main_t == {"GROUNDING": 0.45, "RELEVANCE": 0.35}      # main guardrail unchanged


def test_only_the_store_digest_lambda_gets_the_digest_guardrail():
    from app.infra import deploy_l6
    base = {"RETAIL_OPS_ENV_PREFIX": "retail-ops-dev-", "RETAIL_OPS_GUARDRAIL_ID": "gr-main",
            "RETAIL_OPS_DIGEST_GUARDRAIL_ID": "gr-digest", "RETAIL_OPS_DIGEST_GUARDRAIL_VERSION": "1"}
    for job in deploy_l6.JOB_FN:
        env = deploy_l6.job_env(base, {}, job)
        assert env["RETAIL_OPS_GUARDRAIL_ID"] == "gr-main"
        assert ("RETAIL_OPS_DIGEST_GUARDRAIL_ID" in env) == (job == "store-digest"), job


def test_figures_a_guardrail_held_are_retried_under_a_new_guardrail(fake_ddb, monkeypatch):
    import datetime as _dt
    from app.services.dealer import store_digest
    monkeypatch.setenv("RETAIL_OPS_DIGEST_GUARDRAIL_ID", "gr-strict")
    monkeypatch.setenv("RETAIL_OPS_DIGEST_GUARDRAIL_VERSION", "1")
    monkeypatch.setattr(store_digest.generate, "ground", lambda *a, **k: {
        "text": k["fallback"], "engine": "deterministic (guardrail grounding check)", "model": None})
    store_digest.run_store_digest("us", apply=True, limit=1)
    t1 = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
    assert store_digest.run_store_digest("us", apply=True, limit=1, now=t1)["by_status"].get("held")
    monkeypatch.setenv("RETAIL_OPS_DIGEST_GUARDRAIL_ID", "gr-relaxed")       # a new guardrail
    monkeypatch.setattr(store_digest.generate, "ground",
                        lambda *a, **k: {"text": "Written.", "engine": "bedrock", "model": "m"})
    out = store_digest.run_store_digest("us", apply=True, limit=1, now=t1)
    assert "held" not in out["by_status"] and out["by_status"].get("written")


def test_the_store_digest_asks_for_its_own_guardrail(fake_ddb, monkeypatch):
    from app.services.dealer import store_digest
    asked = []

    def ground(*_a, guardrail="main", **k):
        asked.append(guardrail)
        return {"text": "Written.", "engine": "bedrock", "model": "m"}

    monkeypatch.setattr(store_digest.generate, "ground", ground)
    store_digest.run_store_digest("us", apply=True, limit=1)
    assert asked and set(asked) == {"digest"}


def test_guardrail_outcome_reports_intervention_and_matches(monkeypatch):
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-123")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_VERSION", "2")
    resp = {"stopReason": "guardrail_intervened",
            "trace": {"guardrail": {"inputAssessment": {"gr-123": {
                "topicPolicy": {"topics": [{"name": "CreditDecisions", "type": "DENY", "action": "BLOCKED"}]},
                "wordPolicy": {"customWords": [{"match": "death trap", "action": "BLOCKED"}]}}}}}}
    out = generate.guardrail_outcome(resp)
    assert out["applied"] and out["intervened"]
    assert "topicPolicy:CreditDecisions" in out["matched"]
    assert "wordPolicy:death trap" in out["matched"]


def test_ground_is_deterministic_offline_and_reports_guardrail_state(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "local")
    out = generate.ground("q", ["f1"], [], persona="x", fallback="fb")
    assert out == {"text": "fb", "engine": "deterministic", "model": None,
                   "guardrail": {"applied": False, "intervened": False}}


def test_ground_in_aws_mode_falls_back_when_bedrock_unavailable(monkeypatch):
    # conftest replaces boto3.client with a raising stub -> the aws path must degrade.
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-123")
    out = generate.ground("q", ["f1"], ["p1"], persona="x", fallback="fb")
    assert out["text"] == "fb" and out["engine"].startswith("deterministic (")
    assert out["guardrail"]["applied"] is True and out["guardrail"]["intervened"] is False


def test_ground_tags_grounding_source_and_query_when_guardrail_present(monkeypatch):
    """When a guardrail is configured the context is sent as a grounding_source
    guardContent block and the question as the query block."""
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-1")
    captured = {}

    class _RT:
        def converse(self, **kw):
            captured.update(kw)
            return {"stopReason": "end_turn",
                    "output": {"message": {"content": [{"text": "grounded answer"}]}}}

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    out = generate.ground("what's due?", ["fact A"], ["passage B"], persona="an advisor", fallback="fb")
    assert out["text"] == "grounded answer" and out["engine"] == "bedrock"
    assert out["guardrail"] == {"applied": True, "intervened": False, "id": "gr-1", "version": "DRAFT"}
    content = captured["messages"][0]["content"]
    quals = [c["guardContent"]["text"]["qualifiers"] for c in content if "guardContent" in c]
    assert quals == [["grounding_source"], ["query", "guard_content"]]
    assert captured["guardrailConfig"]["guardrailIdentifier"] == "gr-1"


def test_direct_call_sites_attach_guardrail_kwargs(monkeypatch):
    """Every direct Converse call site splats generate.guardrail_kwargs()."""
    import inspect
    from app.services.agents import discovery, equity, objection
    from app.services.journeys import qa
    from app.services.oem import campaigns, today
    for mod in (discovery, equity, objection, qa, campaigns, today):
        src = inspect.getsource(mod)
        assert "guardrail_kwargs()" in src, mod.__name__


# --- knowledge interface -----------------------------------------------------------

from app.services.platform import knowledge  # noqa: E402


def test_local_mode_uses_keyword_index(monkeypatch, fake_ddb):
    monkeypatch.setenv("INTEGRATION_MODE", "local")
    monkeypatch.setenv("RETAIL_OPS_KNOWLEDGE_BASE_ID", "kb-1")
    kb = knowledge.get_knowledge_base()
    assert isinstance(kb, knowledge.LocalKnowledgeBase)
    out = kb.retrieve("brake pads", k=2)
    assert out["engine"] == "local_keyword" and all(p["source_ref"] for p in out["passages"])


def test_aws_mode_without_kb_id_stays_local(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.delenv("RETAIL_OPS_KNOWLEDGE_BASE_ID", raising=False)
    monkeypatch.setattr(knowledge, "_KB_CFG", knowledge._KB_CFG.with_name("nope.json"))
    assert isinstance(knowledge.get_knowledge_base(), knowledge.LocalKnowledgeBase)


def test_aws_mode_falls_back_to_local_when_bedrock_unavailable(monkeypatch, fake_ddb):
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setenv("RETAIL_OPS_KNOWLEDGE_BASE_ID", "kb-1")
    kb = knowledge.get_knowledge_base()
    assert isinstance(kb, knowledge._FallbackKnowledgeBase)
    out = kb.retrieve("brake pads", k=2)          # boto3 is stubbed -> RuntimeError -> local
    assert out["engine"].startswith("local_keyword (bedrock_kb unavailable")
    assert out["count"] >= 0


def test_bedrock_passages_carry_source_ref_and_kind_filter(monkeypatch):
    captured = {}

    class _RT:
        def retrieve(self, **kw):
            captured.update(kw)
            return {"retrievalResults": [{
                "content": {"text": "# CRM — C-US-00257\nsource_ref: synthetic:crm_note; entity:C-US-00257\n"
                                    "collection_id: KC-US-crm_notes\nmarket: us\n\nCRM note: customer asked about EV range."},
                "location": {"s3Location": {"uri": "s3://b/quick-index/crm_notes/us/C-US-00257.md"}},
                "metadata": {"kind": "crm_notes", "market": "us"}, "score": 0.8123456}]}

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    kb = knowledge.BedrockKnowledgeBase("kb-1")
    out = kb.retrieve("EV range worries", collection="Retail Ops CRM Notes", k=3)
    assert out["engine"] == "bedrock_kb" and out["kind"] == "crm_notes"
    # the earlier "Retail Ops …" display names still resolve to the same kind
    assert knowledge.BedrockKnowledgeBase._kind("Retail Ops CRM Notes") == "crm_notes"
    assert captured["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == \
        {"equals": {"key": "kind", "value": "crm_notes"}}
    [p] = out["passages"]
    assert p["source_ref"] == "synthetic:crm_note; entity:C-US-00257"
    assert p["passage"].startswith("CRM note:") and p["collection"] == "crm_notes" and p["score"] == 0.8123


def test_kind_mapping_accepts_table_names_and_unknowns():
    f = knowledge.BedrockKnowledgeBase._kind
    assert f("DEALER_ObjectionStrategies") == "objection_strategies"
    assert f("tsbs") == "tsbs" and f("Retail Ops Prior Plans") == "prior_plans"
    assert f("PLAT_KnowledgeCollections") is None and f("whatever") is None and f(None) is None


def test_grounding_only_intervention_falls_back_to_context(monkeypatch):
    """A contextual-grounding block means the model drifted, not that the user asked
    something fenced: ground() must return the deterministic text, not the refusal."""
    monkeypatch.setenv("INTEGRATION_MODE", "aws")
    monkeypatch.setenv("RETAIL_OPS_GUARDRAIL_ID", "gr-1")

    class _RT:
        def converse(self, **kw):
            return {"stopReason": "guardrail_intervened",
                    "output": {"message": {"content": [{"text": "That answer was withheld by the safety check."}]}},
                    "trace": {"guardrail": {"outputAssessments": {"gr-1": [{
                        "contextualGroundingPolicy": {"filters": [{"type": "GROUNDING", "threshold": 0.45,
                                                                   "score": 0.2, "action": "BLOCKED"}]}}]}}}}

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    out = generate.ground("q", ["fact"], ["passage"], persona="x", fallback="the passages themselves")
    assert out["text"] == "the passages themselves"
    assert out["engine"] == "deterministic (guardrail grounding check)"
    assert out["guardrail"]["intervened"] and out["guardrail"]["grounding_only"]
