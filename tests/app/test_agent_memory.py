"""Agent memory (platform/memory) and its write/read wiring:
objections, intake preferences and promises land as memories where the signal is
born; the one-shot assistant, the Sales Package and the nurture preview recall
them with their source_refs. Offline everything runs on the PLAT_AgentMemory
table (the AgentCore data plane is aws-mode only)."""
from __future__ import annotations

from app.services.agents import assistant, objection
from app.services.common import ddb
from app.services.customer import shop
from app.services.journeys import nurture, package
from app.services.platform import memory


def _journey(jid: str):
    return ddb.get_item("DEALER_Journeys", {"journey_id": jid})


# --- the module itself --------------------------------------------------------

def test_remember_and_recall_roundtrip(fake_ddb):
    r = memory.remember("C-US-J1", "preference", "Powertrain preference: ev",
                        source_ref="A1.intake:/shop:C-US-J1", actor="A1")
    assert r["stored"] and r["engine"] == "dynamodb"          # local mode: table only
    assert r["memory_id"].startswith("C-US-J1#") and r["memory_id"].endswith("#preference")
    memory.remember("C-US-J1", "objection", 'Raised price_too_high: "too expensive"',
                    source_ref="A3.classify_objection:J-US-J1", actor="A3")
    out = memory.recall("C-US-J1")
    assert [m["kind"] for m in out] == ["objection", "preference"]   # newest first
    assert all(m["source_ref"] for m in out)                         # provenance always


def test_recall_dedupes_by_kind_and_text_and_caps_k(fake_ddb):
    for i in range(4):
        memory.remember("C-US-J2", "fact", "Prefers Saturday appointments",
                        source_ref=f"ref-{i}", actor="A3")
    memory.remember("C-US-J2", "promise", "Promised a callback from the advisor",
                    source_ref="A3.send_outreach:J-US-J2", actor="u-adv")
    out = memory.recall("C-US-J2", k=8)
    assert len(out) == 2                                            # (kind, text) de-dup
    assert memory.recall("C-US-J2", k=1) == out[:1]


def test_memory_is_enrichment_never_raises(fake_ddb):
    assert memory.remember("C-US-J1", "nope", "x", source_ref="s", actor="a")["stored"] is False
    assert memory.remember("", "fact", "x", source_ref="s", actor="a")["stored"] is False
    assert memory.recall("") == []
    assert memory.recall("C-NOBODY") == []                          # empty, not an error


# --- writes where the signal is born -------------------------------------------

def test_objection_classification_remembers_the_objection(fake_ddb):
    res = objection.classify_objection("J-US-J1", "That price is just too expensive for me")
    assert res["objection_detected"] is True
    cust = _journey("J-US-J1")["customer_id"]
    out = memory.recall(cust)
    assert any(m["kind"] == "objection" and "price_too_high" in m["text"] for m in out)
    assert any(m["source_ref"] == "A3.classify_objection:J-US-J1" for m in out)


def test_intake_answers_remember_preferences(fake_ddb):
    cust = _journey("J-US-J2")["customer_id"]
    shop.intake(cust, {"powertrain_openness": "ev", "household_size": 5})
    kinds = {(m["kind"], m["text"]) for m in memory.recall(cust)}
    assert ("preference", "Powertrain preference: ev") in kinds
    assert ("preference", "Needs seating for a household of 5") in kinds
    # a bare read (no answers submitted) records nothing new
    before = len(memory.recall(cust, k=50))
    shop.intake(cust)
    assert len(memory.recall(cust, k=50)) == before


# --- reads: the surfaces that recall --------------------------------------------

def test_assistant_chat_prepends_recalled_memories_as_facts(fake_ddb):
    j = _journey("J-US-J2")
    memory.remember(j["customer_id"], "promise", "Promised to hold a test-drive time",
                    source_ref="A3.send_outreach:J-US-J2", actor="u-adv")
    out = assistant.assistant_chat("What should I do next with this customer?", "J-US-J2",
                                   "dealer_advisor")
    assert "Promise made: Promised to hold a test-drive time." in out["answer"]
    assert any(c["label"] == "memory:promise" and c["ref"] == "A3.send_outreach:J-US-J2"
               for c in out["citations"])


def test_sales_package_carries_what_we_know_only_when_nonempty(fake_ddb):
    j = _journey("J-US-J1")
    pkg = package.build_sales_package(j)
    assert "what_we_know" not in pkg                                # empty recall = no block
    memory.remember(j["customer_id"], "objection", 'Raised trade_value_low: "lowball offer"',
                    source_ref="A3.classify_objection:J-US-J1", actor="A3")
    pkg = package.build_sales_package(j)
    assert pkg["what_we_know"][0]["kind"] == "objection"
    assert pkg["what_we_know"][0]["source_ref"]


def test_nurture_preview_carries_what_we_know_only_when_nonempty(fake_ddb):
    j = _journey("J-US-J2")
    msg = nurture.compose_message(j, channel="email")
    assert "what_we_know" not in msg
    memory.remember(j["customer_id"], "preference", "Budget USD 30000-40000",
                    source_ref="A1.intake:/shop", actor="A1")
    msg = nurture.compose_message(j, channel="email")
    assert any(m["text"] == "Budget USD 30000-40000" for m in msg["what_we_know"])
