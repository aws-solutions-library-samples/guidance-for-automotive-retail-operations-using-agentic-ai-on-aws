"""Store operations routes (spec §6): F&I Desk, Inventory & Trade Desk, Delivery
Board, documents, Store Cockpit tabs, store-scoped campaigns.

  GET   /fni/queue                              F&I queue + attach / PVR KPIs
  GET   /fni/deals/{deal_id}                    deal worksheet
  PATCH /fni/deals/{deal_id}                    term / down / selected offer / doc fee
  GET   /fni/deals/{deal_id}/menu               F&I product menu (+ AI attach)
  POST  /fni/deals/{deal_id}/menu               {accepted: [codes]}
  POST  /fni/deals/{deal_id}/lender-decision    submit credit app (sandbox F&I connector)
  POST  /fni/deals/{deal_id}/adverse-action     record the adverse-action notice as sent
  POST  /fni/deals/{deal_id}/push               push_deal_to_dms (gate-checked)
  POST  /fni/deals/{deal_id}/documents          {kind, share} worksheet / menu / buyer's order
  POST  /fni/journeys/{journey_id}/advance      advance FINANCE/TRADE_IN/AGREE with recorded evidence
  POST  /fni/journeys/{journey_id}/deal         open the desk: the journey's deal, desked from its build on first touch
  GET   /inventory?status=&model=               VIN grid
  POST  /inventory/{vin}/reserve                {journey_id} · {reserve:false} unreserves
  GET   /inventory/acquisitions                 A4 acquisition recommendations
  GET   /trades/dispositions                    trade disposition queue
  POST  /trades/{disposition_id}/decide         {decision, reason}
  GET   /deliveries                             Delivery Board
  PATCH /deliveries/{journey_id}                checklist / docs / confirmation / reschedule
  POST  /deliveries/{journey_id}/message        delivery-day message
  POST  /deliveries/{journey_id}/deliver        gated mark-delivered
  GET   /documents?journey_id=|deal_id=|customer_id=
  GET   /documents/{id}/url                     short-lived signed content URL
  GET   /documents/{id}/content?exp=&sig=       signed content
  POST  /documents/{id}/share                   share to the customer's My Garage
  GET   /dealer/cockpit/{tab}?dealer=           Store Cockpit tab (materialized)
  POST  /dealer/cockpit/alerts/{alert_id}/ack   dealer acknowledges an A6 alert
  GET   /dealer/campaigns?dealer=               store-scoped campaign ROI
"""
from __future__ import annotations

import re
from typing import Any

from ...common import ddb
from ...dealer import cockpit, delivery, documents, fni, inventory
from .. import routing as r

FNI_ROLES = {"dealer_fni", "dealer_manager"}
FNI_READ = FNI_ROLES | {"dealer_delivery", "dealer_advisor"}
INV_WRITE = {"dealer_inventory", "dealer_manager", "dealer_advisor", "dealer_fni"}
INV_READ = INV_WRITE | {"dealer_bdc", "dealer_parts", "dealer_service_manager"}
DISP_WRITE = {"dealer_inventory", "dealer_manager"}
DLV_ROLES = {"dealer_delivery", "dealer_fni", "dealer_manager"}
DOC_ROLES = FNI_ROLES | {"dealer_delivery", "dealer_advisor"}
# cockpit: GM sees all tabs; inventory and service managers see their tabs (§5.2)
COCKPIT_TABS = {"dealer_manager": set(cockpit.TABS), "dealer_inventory": {"inventory", "trade"},
                "dealer_service_manager": {"service"}}


def _deal_scoped(claims: dict[str, Any], deal_id: str) -> dict[str, Any]:
    """The deal for a deal id — or for a journey id, since the resource-URL
    resolver sends F&I users to /dealer/fni/{journey_id} for journey links."""
    d = ddb.get_item("DEALER_Deals", {"deal_id": deal_id})
    if d is None and deal_id.startswith("J-"):
        j = ddb.get_item("DEALER_Journeys", {"journey_id": deal_id})
        d = fni.deal_for_journey(j) if j else None
    if d is None:
        raise r.Bad(404, f"deal {deal_id} not found")
    r.dealer_in_scope(claims, d.get("dealer_id"))
    return d


def _market(event: dict[str, Any], claims: dict[str, Any]) -> str:
    return r.qs(event).get("market") or claims.get("market_id") or "us"


def _write_actor(claims: dict[str, Any], roles: set[str]) -> str:
    r.roles(claims, roles)
    return claims["user_id"]


# --- F&I -------------------------------------------------------------------------

def _fni_queue(event, claims, _m):
    r.roles(claims, FNI_READ, allow_oem=True)
    return 200, fni.fni_queue(claims, _market(event, claims))


def _fni_deal(event, claims, m):
    r.roles(claims, FNI_READ, allow_oem=True)
    d = _deal_scoped(claims, m.group(1))
    return 200, fni.deal_worksheet(d["deal_id"])


def _fni_patch(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    _deal_scoped(claims, m.group(1))
    return 200, fni.update_deal(m.group(1), r.body(event), actor)


def _fni_menu_get(event, claims, m):
    r.roles(claims, FNI_READ, allow_oem=True)
    d = _deal_scoped(claims, m.group(1))
    ws = fni.deal_worksheet(d["deal_id"])
    return 200, {"deal_id": d["deal_id"], "menu": ws["menu"], "currency": ws["currency"],
                 "customer_name": ws["customer_name"], "vehicle": ws["vehicle"], "price": ws["price"],
                 "scenarios": ws["scenarios"]}


def _fni_menu_post(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    _deal_scoped(claims, m.group(1))
    b = r.body(event)
    acc = b.get("accepted")
    if not isinstance(acc, list):
        raise r.Bad(400, "body must include accepted: [product codes]")
    return 200, fni.set_menu(m.group(1), [str(x) for x in acc], actor)


def _fni_lender(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    _deal_scoped(claims, m.group(1))
    return 200, fni.request_lender_decision(m.group(1), actor)


def _fni_adverse(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    _deal_scoped(claims, m.group(1))
    return 200, fni.send_adverse_action(m.group(1), actor)


def _fni_push(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    _deal_scoped(claims, m.group(1))
    try:
        return 200, fni.push_deal_to_dms(m.group(1), actor)
    except fni.GateBlocked as e:
        raise r.Bad(409, str(e), missing=e.missing, gate="credit_compliance")
    except fni.NoVehicle as e:
        raise r.Bad(409, str(e), missing=["vin_reserved"], gate="vehicle")


def _fni_docs(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    d = _deal_scoped(claims, m.group(1))
    b = r.body(event)
    kind = b.get("kind") or "fi_menu"
    if kind not in ("deal_worksheet", "fi_menu", "buyers_order"):
        raise r.Bad(400, "kind must be deal_worksheet, fi_menu, or buyers_order")
    ws = fni.deal_worksheet(m.group(1))
    body = {"deal_worksheet": ws, "fi_menu": {"menu": ws["menu"], "scenarios": ws["scenarios"], "price": ws["price"]},
            "buyers_order": {"vehicle": ws["vehicle"], "price": ws["price"], "trade": ws["trade"],
                             "incentives": ws["incentives"], "disclosures": ws["compliance"]["disclosures"]}}[kind]
    doc = documents.create_document(kind, customer_id=d.get("customer_id"), actor=actor, journey_id=d.get("journey_id"),
                                    deal_id=d["deal_id"], dealer_id=d.get("dealer_id"),
                                    title={"deal_worksheet": "Deal worksheet", "fi_menu": "F&I protection menu",
                                           "buyers_order": "Buyer's order"}[kind],
                                    data_class="C4", share_with_customer=bool(b.get("share")),
                                    body=documents.body_json(body))
    return 201, doc


def _fni_deal_ensure(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    r.journey_in_scope(claims, m.group(1))
    out = fni.ensure_deal(m.group(1), actor)
    return (201 if out["created"] else 200), {**out, "worksheet": fni.deal_worksheet(out["deal"]["deal_id"])}


def _fni_advance(event, claims, m):
    actor = _write_actor(claims, FNI_ROLES)
    r.journey_in_scope(claims, m.group(1))
    from ...journeys import supervisor
    try:
        return 200, fni.advance_from_desk(m.group(1), actor)
    except supervisor.HandoffIncomplete as e:
        raise r.Bad(409, str(e), missing=e.missing, stage=e.stage)


# --- inventory & trades ----------------------------------------------------------------

def _inv_list(event, claims, _m):
    r.roles(claims, INV_READ, allow_oem=True)
    q = r.qs(event)
    return 200, inventory.list_inventory(claims, _market(event, claims), status=q.get("status"), model_id=q.get("model"))


def _inv_reserve(event, claims, m):
    actor = _write_actor(claims, INV_WRITE)
    vin = m.group(1)
    v = ddb.get_item("DEALER_Inventory", {"vin": vin})
    if v is None:
        raise r.Bad(404, f"VIN {vin} not in inventory")
    r.dealer_in_scope(claims, v.get("dealer_id"))
    b = r.body(event)
    if b.get("reserve") is False:
        return 200, inventory.unreserve(vin, actor)
    r.require(b, "journey_id")
    r.journey_in_scope(claims, b["journey_id"])
    return 200, inventory.reserve(vin, b["journey_id"], actor)


def _inv_acq(event, claims, _m):
    r.roles(claims, INV_READ, allow_oem=True)
    return 200, inventory.acquisitions(claims, _market(event, claims))


def _disp_list(event, claims, _m):
    r.roles(claims, INV_READ, allow_oem=True)
    return 200, inventory.trade_dispositions(claims, _market(event, claims))


def _disp_decide(event, claims, m):
    actor = _write_actor(claims, DISP_WRITE)
    t = inventory._get_td(m.group(1), None)
    if t is None:
        raise r.Bad(404, f"trade disposition {m.group(1)} not found")
    cust = ddb.get_item("DEALER_Customers", {"customer_id": t.get("customer_id")}) or {}
    r.dealer_in_scope(claims, t.get("dealer_id") or cust.get("dealer_id"))
    b = r.body(event)
    r.require(b, "decision")
    return 200, inventory.decide_disposition(m.group(1), b["decision"], actor, b.get("reason"))


# --- delivery --------------------------------------------------------------------------

def _dlv_board(event, claims, _m):
    r.roles(claims, DLV_ROLES | {"dealer_advisor"}, allow_oem=True)
    return 200, delivery.build_delivery_board(claims, _market(event, claims))


def _dlv_patch(event, claims, m):
    actor = _write_actor(claims, DLV_ROLES)
    r.journey_in_scope(claims, m.group(1))
    return 200, delivery.update_delivery(m.group(1), r.body(event), actor)


def _dlv_message(event, claims, m):
    actor = _write_actor(claims, DLV_ROLES)
    r.journey_in_scope(claims, m.group(1))
    return 200, delivery.send_delivery_message(m.group(1), actor, note=r.body(event).get("note"))


def _dlv_deliver(event, claims, m):
    actor = _write_actor(claims, DLV_ROLES)
    r.journey_in_scope(claims, m.group(1))
    try:
        return 200, delivery.deliver_journey(m.group(1), actor)
    except delivery.NotReady as e:
        raise r.Bad(409, str(e), missing=e.missing)


# --- documents -------------------------------------------------------------------------

def _doc_scoped(claims: dict[str, Any], doc_id: str) -> dict[str, Any]:
    d = ddb.get_item("CUST_Documents", {"document_id": doc_id})
    if d is None:
        raise r.Bad(404, f"document {doc_id} not found")
    if claims.get("role") == "customer":
        if d.get("customer_id") != claims.get("customer_id") or "customer" not in (d.get("shared_with") or []):
            raise r.Bad(403, "document is not shared with you")
        return d
    r.roles(claims, DOC_ROLES, allow_oem=False)
    dealer = d.get("dealer_id")
    if not dealer and d.get("customer_id"):
        dealer = (ddb.get_item("DEALER_Customers", {"customer_id": d["customer_id"]}) or {}).get("dealer_id")
    r.dealer_in_scope(claims, dealer)
    return d


def _docs_list(event, claims, _m):
    q = r.qs(event)
    if claims.get("role") == "customer":
        cid = r.customer(claims)
        rows = [d for d in documents.list_documents(customer_id=cid) if "customer" in (d.get("shared_with") or [])]
        return 200, {"documents": rows}
    r.roles(claims, DOC_ROLES)
    if q.get("journey_id"):
        r.journey_in_scope(claims, q["journey_id"])
        return 200, {"documents": documents.list_documents(journey_id=q["journey_id"])}
    if q.get("deal_id"):
        _deal_scoped(claims, q["deal_id"])
        return 200, {"documents": documents.list_documents(deal_id=q["deal_id"])}
    if q.get("customer_id"):
        c = ddb.get_item("DEALER_Customers", {"customer_id": q["customer_id"]}) or {}
        r.dealer_in_scope(claims, c.get("dealer_id"))
        return 200, {"documents": documents.list_documents(customer_id=q["customer_id"])}
    raise r.Bad(400, "query must include journey_id, deal_id, or customer_id")


def _doc_url(event, claims, m):
    _doc_scoped(claims, m.group(1))
    return 200, documents.signed_url(m.group(1))


def _doc_content(event, claims, m):
    _doc_scoped(claims, m.group(1))
    q = r.qs(event)
    try:
        return 200, documents.render(m.group(1), q.get("sig"), q.get("exp"))
    except documents.Forbidden as e:
        raise r.Bad(403, str(e))


def _doc_share(event, claims, m):
    d = _doc_scoped(claims, m.group(1))
    if claims.get("role") == "customer":
        raise r.Bad(403, "customers cannot share dealer documents")
    return 200, documents.share(d["document_id"], claims["user_id"])


# --- cockpit + campaigns ---------------------------------------------------------------

def _cockpit_tab(event, claims, m):
    tab = m.group(1)
    if tab not in cockpit.TABS:
        raise r.Bad(404, f"unknown cockpit tab {tab!r}")
    allowed = COCKPIT_TABS.get(claims.get("role") or "")
    if not claims.get("is_oem") and (allowed is None or tab not in allowed):
        raise r.Bad(403, f"role {claims.get('role')!r} may not view the {tab} tab")
    q = r.qs(event)
    if q.get("dealer"):
        r.dealer_in_scope(claims, q["dealer"])
    return 200, cockpit.build_tab(claims, tab, dealer_id=q.get("dealer"), market_id=_market(event, claims))


def _cockpit_ack(event, claims, m):
    actor = _write_actor(claims, {"dealer_manager"})
    try:
        return 200, cockpit.acknowledge_alert(m.group(1), claims, actor, r.body(event).get("note"))
    except LookupError as e:
        raise r.Bad(404, str(e))
    except PermissionError as e:
        raise r.Bad(403, str(e))


def _store_campaigns(event, claims, _m):
    r.roles(claims, {"dealer_manager"}, allow_oem=True)
    q = r.qs(event)
    if q.get("dealer"):
        r.dealer_in_scope(claims, q["dealer"])
    return 200, cockpit.store_campaigns(claims, dealer_id=q.get("dealer"), market_id=_market(event, claims))


ROUTES = [
    ("GET", re.compile(r"^/fni/queue$"), _fni_queue),
    ("GET", re.compile(r"^/fni/deals/([^/]+)$"), _fni_deal),
    ("PATCH", re.compile(r"^/fni/deals/([^/]+)$"), _fni_patch),
    ("GET", re.compile(r"^/fni/deals/([^/]+)/menu$"), _fni_menu_get),
    ("POST", re.compile(r"^/fni/deals/([^/]+)/menu$"), _fni_menu_post),
    ("POST", re.compile(r"^/fni/deals/([^/]+)/lender-decision$"), _fni_lender),
    ("POST", re.compile(r"^/fni/deals/([^/]+)/adverse-action$"), _fni_adverse),
    ("POST", re.compile(r"^/fni/deals/([^/]+)/push$"), _fni_push),
    ("POST", re.compile(r"^/fni/deals/([^/]+)/documents$"), _fni_docs),
    ("POST", re.compile(r"^/fni/journeys/([^/]+)/advance$"), _fni_advance),
    ("POST", re.compile(r"^/fni/journeys/([^/]+)/deal$"), _fni_deal_ensure),
    ("GET", re.compile(r"^/inventory$"), _inv_list),
    ("GET", re.compile(r"^/inventory/acquisitions$"), _inv_acq),
    ("POST", re.compile(r"^/inventory/([^/]+)/reserve$"), _inv_reserve),
    ("PATCH", re.compile(r"^/inventory/([^/]+)/reserve$"), _inv_reserve),
    ("GET", re.compile(r"^/trades/dispositions$"), _disp_list),
    ("POST", re.compile(r"^/trades/([^/]+)/decide$"), _disp_decide),
    ("GET", re.compile(r"^/deliveries$"), _dlv_board),
    ("PATCH", re.compile(r"^/deliveries/([^/]+)$"), _dlv_patch),
    ("POST", re.compile(r"^/deliveries/([^/]+)/message$"), _dlv_message),
    ("POST", re.compile(r"^/deliveries/([^/]+)/deliver$"), _dlv_deliver),
    ("GET", re.compile(r"^/documents$"), _docs_list),
    ("GET", re.compile(r"^/documents/([^/]+)/url$"), _doc_url),
    ("GET", re.compile(r"^/documents/([^/]+)/content$"), _doc_content),
    ("POST", re.compile(r"^/documents/([^/]+)/share$"), _doc_share),
    ("GET", re.compile(r"^/dealer/cockpit/(funnel|sales|inventory|trade|fi|service|cx|alerts|comms)$"), _cockpit_tab),
    ("POST", re.compile(r"^/dealer/cockpit/alerts/([^/]+)/ack$"), _cockpit_ack),
    ("GET", re.compile(r"^/dealer/campaigns$"), _store_campaigns),
]

ERRORS = [
    (fni.NotFound, 404), (inventory.NotFound, 404), (delivery.NotFound, 404), (documents.NotFound, 404),
    (fni.InvalidState, 409), (fni.GateBlocked, 409), (fni.NoVehicle, 409), (inventory.InvalidState, 409),
    (delivery.AlreadyDelivered, 409), (delivery.NotReady, 409), (documents.Forbidden, 403),
]
