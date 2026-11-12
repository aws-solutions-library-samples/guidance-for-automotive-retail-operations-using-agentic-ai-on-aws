"""Corpora component (U5): agent-grounding documents + PLAT_KnowledgeCollections.

Agents answer from documents and records (synthetic-data plan §"agent-grounding
data is first-class"): CRM notes, bulletins, product guides, prior plans, repair
procedures, TSBs, transcripts, message templates, and objection-strategy scripts
must exist and be indexed (Quick Index), or A3/A6 answers are thin and A2 nurture
is generic.

`gen_corpora(g)` (registered at band 40 — after OEM, before storylines/late)
builds an in-memory document corpus in `g.ix["corpus"]` (a list of doc dicts) and
emits one `PLAT_KnowledgeCollections` row per document. U5 is the SOLE writer of
that table (U2 published the KNOWLEDGE_COLLECTIONS vocabulary and emitted no rows,
BR7.7). The Quick Index *export* is U7's job; U5 produces the rows and the corpus.

Every document is derived from an already-generated entity (so citations resolve
to real rows), is clearly synthetic (reserved example domains, fictional names),
and carries a `source_ref` (NFR4). All randomness is `g.rng` (NFR1); dates are
SEED_NOW offsets (NFR6); two markets via `g.m` (NFR7). Only PLAT_ tables are
written here (BR-U5.1, enforced by `_add`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import phase_registry as _pr
from .commerce import OBJECTION_STRATEGY_TYPES as _OBJECTION_STRATEGIES
from .owner_map import split_prefixed
from .platform import KNOWLEDGE_KINDS, KNOWLEDGE_COLLECTIONS

if TYPE_CHECKING:  # pragma: no cover
    from .core import G

# Objection-strategy grounding topics are derived from commerce's authoritative
# OBJECTION_STRATEGY_TYPES (imported above) so they cannot drift from the
# operational vocabulary. commerce runs before corpora (band 26 < 40) and does not
# import corpora, so the import is cycle-free.


def _add(g: "G", table: str, row: dict) -> dict:
    # BR-U5.1 — U5 emits PLAT_ tables only: the resolved owner must be PLAT
    # (not merely any correctly owner-prefixed table).
    owner, _name = split_prefixed(table)
    if owner != "PLAT":
        raise ValueError(f"corpora.py may only emit PLAT_ tables; got {table!r}")
    return g.add(table, row)


def _collection_id(market_id: str, kind: str) -> str:
    return f"KC-{market_id.upper()}-{kind}"


def gen_corpora(g: "G") -> None:
    m, cfg, rng = g.m, g.cfg, g.rng
    mid = m.id
    corpus: list[dict] = g.ix.setdefault("corpus", [])

    def add_doc(kind: str, title: str, body: str, source_ref: str) -> None:
        if kind not in KNOWLEDGE_KINDS:
            raise ValueError(f"unknown knowledge kind {kind!r}")
        doc_id = g.nid("DOC")
        corpus.append({
            "doc_id": doc_id, "collection_id": _collection_id(mid, kind), "kind": kind,
            "title": title, "body": body, "market_id": mid, "source_ref": source_ref,
        })
        # One PLAT_KnowledgeCollections row per document (BR7.7; U5 sole writer).
        _add(g, "PLAT_KnowledgeCollections", {
            "collection_id": _collection_id(mid, kind), "kind": kind, "title": title,
            "market_id": mid, "index_target": "quick_index",
            "doc_id": doc_id, "body_ref": f"corpus:{doc_id}", "source_ref": source_ref,
        })

    de = mid == "de"

    # ── crm_notes — per-customer interaction notes (A2/A3 grounding) ─────────
    customers = g.t.get("DEALER_Customers", [])
    journ_by_cust: dict[str, list[dict]] = {}
    for j in g.t.get("DEALER_Journeys", []):
        journ_by_cust.setdefault(j["customer_id"], []).append(j)
    for c in rng.sample(customers, min(len(customers), 120)):
        js = journ_by_cust.get(c["customer_id"], [])
        stage = js[0]["stage"] if js else "DISCOVER"
        note = (f"Kundennotiz: {c['given_name']} {c['family_name']} — Phase {stage}; letzter Kontakt zum Berater."
                if de else
                f"CRM note: {c['given_name']} {c['family_name']} — stage {stage}; last contact with advisor.")
        add_doc("crm_notes", f"CRM — {c['customer_id']}", note, f"synthetic:crm_note; entity:{c['customer_id']}")

    # ── bulletins — mirrored from OEM_Comms (incl. GR-003/GR-006 boundary) ──
    for comm in g.t.get("OEM_Comms", []):
        tag = f" [GR:{comm['gr_boundary_rule_id']}]" if comm.get("gr_boundary_rule_id") else ""
        add_doc("bulletins", f"{comm['subject']}{tag}", comm["body"],
                f"entity:{comm['comm_id']}; spec:§3.6.1" if comm.get("gr_boundary_rule_id") else f"entity:{comm['comm_id']}")

    # ── product_guides — per model from OEM_CatalogModels ───────────────────
    for mod in g.t.get("OEM_CatalogModels", []):
        pt = ", ".join(mod.get("powertrains", []))
        body = (f"Produktleitfaden {mod['name']} ({mod['segment']}): Antriebe {pt}."
                if de else f"Product guide {mod['name']} ({mod['segment']}): powertrains {pt}.")
        add_doc("product_guides", f"Guide — {mod['name']}", body, f"entity:{mod['model_id']}")

    # ── prior_plans — mirrored from OEM_AccountPlans ────────────────────────
    for p in g.t.get("OEM_AccountPlans", []):
        if p.get("status") == "brief":
            continue
        body = (f"Account-Plan {p['plan_id']} (v{p['version']}, {p['status']}) für Händler {p.get('dealer_id')}."
                if de else f"Account plan {p['plan_id']} (v{p['version']}, {p['status']}) for dealer {p.get('dealer_id')}.")
        add_doc("prior_plans", f"Plan — {p['plan_id']} v{p['version']}", body, f"entity:{p['plan_id']}")

    # ── repair_procedures — keyed to DEALER_LaborOps ────────────────────────
    for op in g.t.get("DEALER_LaborOps", []):
        body = (f"Reparaturablauf {op['name']} — Richtzeit {op['flat_rate_hours']} h."
                if de else f"Repair procedure {op['name']} — flat rate {op['flat_rate_hours']} h.")
        add_doc("repair_procedures", f"Procedure — {op['labor_op_id']}", body,
                f"entity:{op['labor_op_id']}")

    # ── tsbs — keyed to DEALER_DTCReference (A7 lookup_dtc / S6 grounding) ───
    for dtc in g.t.get("DEALER_DTCReference", []):
        body = (f"TSB zu {dtc['dtc']}: {dtc['description']} (System {dtc.get('system')})."
                if de else f"TSB for {dtc['dtc']}: {dtc['description']} (system {dtc.get('system')}).")
        add_doc("tsbs", f"TSB — {dtc['dtc']}", body, f"entity:{dtc['dtc']}")

    # ── transcripts — call/chat transcripts for a subset of journeys ────────
    journeys = g.t.get("DEALER_Journeys", [])
    for j in rng.sample(journeys, min(len(journeys), 40)):
        body = (f"Gesprächsprotokoll {j['journey_id']} (Phase {j['stage']}): Kundenanliegen und nächste Schritte."
                if de else f"Transcript {j['journey_id']} (stage {j['stage']}): customer intent and next steps.")
        add_doc("transcripts", f"Transcript — {j['journey_id']}", body, f"entity:{j['journey_id']}")

    # ── templates — mirrored from PLAT_ConfigNurture (active version) ───────
    for cfgn in g.t.get("PLAT_ConfigNurture", []):
        if not cfgn.get("is_active"):
            continue
        for tpl in cfgn.get("templates", []):
            add_doc("templates", f"Template — {tpl['template_id']}",
                    tpl.get("subject") or tpl.get("body") or tpl["template_id"],
                    f"entity:{tpl['template_id']}; config:nurture v{cfgn['version']}")

    # ── objection_strategies — the 17 scripts (sales + service) ─────────────
    for obj in _OBJECTION_STRATEGIES:
        body = (f"Einwandbehandlung '{obj}': empfohlene Antwortstrategie und Belege."
                if de else f"Objection strategy '{obj}': recommended response approach and evidence.")
        add_doc("objection_strategies", f"Objection — {obj}", body, f"curated:objection_strategy:{obj}")


# ── Phase registration (U4 phase-registry refactor) ─────────────────────────
# Corpora runs at band 40 — after OEM (30), before storylines (50) / late (90).
_pr.register(_pr.BAND_CORPORA, gen_corpora)  # 40
