"""Connector interface wave 1: factory mode selection, sandbox
parity with the pre-migration lookups, PLAT_EntityMap translation at the
boundary, and the live stub's honest NotConfigured failure."""
from __future__ import annotations

import pytest

from app.services import connectors as cx
from app.services.common import ddb
from app.services.connectors import entity_map
from app.services.connectors.base import NotConfigured
from app.services.service import connectors as svc_connectors


def test_factory_defaults_sandbox(fake_ddb):
    c = cx.get_connector("dms")
    assert c.mode == "sandbox" and c.name == "dms-sandbox"
    assert cx.get_connector("labor").name == "labor-sandbox"
    with pytest.raises(KeyError):
        cx.get_connector("telepathy")


def _flip_live(connector_id: str) -> None:
    from app.services.admin import config_store
    from app.services.admin import connectors as admin_cx
    reg = admin_cx.default_registry("us")
    for e in reg:
        if e["connector_id"] == connector_id:
            e["mode"] = "live"
    config_store.publish("connectors", "us", "u-test", content={"connectors": reg})


def test_factory_flips_live_from_admin_registry(fake_ddb):
    _flip_live("labor")
    c = cx.get_connector("labor")
    assert c.mode == "live" and c.name == "labor-live"
    # the other domains stay sandbox
    assert cx.get_connector("parts").mode == "sandbox"


def test_live_mode_raises_not_configured(fake_ddb):
    _flip_live("labor")
    with pytest.raises(NotConfigured) as e:
        svc_connectors.get_labor_time("LOP-BRAKE-F")
    assert "live connector 'labor' has no endpoint configured" in str(e.value)
    # Admin health check reports the same degradation, never a fake pass.
    from app.services.admin import connectors as admin_cx
    h = admin_cx.test_connection("labor", "us", "u-test")
    assert h["status"] == "not_configured"


def test_sandbox_results_identical_to_pre_migration(fake_ddb):
    """Snapshot of one DTC + one labor op: the data fields are byte-identical to
    the pre-migration outputs; source_ref now names the connector boundary."""
    assert svc_connectors.lookup_dtc("P0300") == {
        "dtc": "P0300", "found": True, "system": "P",
        "description": "Random/multiple cylinder misfire",
        "source_ref": "connector:dms-sandbox DEALER_DTCReference:P0300",
    }
    assert svc_connectors.get_labor_time("LOP-BRAKE-F") == {
        "labor_op_id": "LOP-BRAKE-F", "name": "Front brake pads & rotors",
        "flat_rate_hours": 1.8, "shop_rate": 165, "labor_total": 297.0,
        "currency": "USD", "market_id": "us",
        "source_ref": "connector:labor-sandbox DEALER_LaborOps:LOP-BRAKE-F",
    }


def test_entity_map_translation_at_the_boundary(fake_ddb):
    ddb.put_item("PLAT_EntityMap", {
        "item_key": "ro#RO-US-00001", "system": "dms", "entity_type": "ro",
        "app_id": "RO-US-00001", "external_id": "DMS-RO-777", "confidence": 0.99,
        "market_id": "us", "source_ref": "test:entity_map"})
    # a DMS RO number resolves to the same parts as our ro_id
    ours = svc_connectors.lookup_parts(ro_id="RO-US-00001")
    theirs = svc_connectors.lookup_parts(ro_id="DMS-RO-777")
    assert ours["count"] > 0 and theirs["parts"] == ours["parts"]
    assert theirs["ro_id"] == "RO-US-00001"
    assert theirs["entity_map"] == "PLAT_EntityMap:ro#RO-US-00001/dms"
    # unmapped ids pass through unchanged (sandbox pass-through)
    t = entity_map.translate("dms", "DMS-RO-NOPE")
    assert t["mapped"] is False and t["app_id"] == "DMS-RO-NOPE"


def test_health_and_list_labor_ops(fake_ddb):
    h = cx.get_connector("parts").health()
    assert h["status"] == "ok" and h["connector"] == "parts-sandbox"
    ops = cx.get_connector("labor").list_labor_ops("us")
    assert ops["count"] > 0 and ops["source_ref"] == "connector:labor-sandbox DEALER_LaborOps"
    assert all((o.get("market_id") or "us") == "us" for o in ops["labor_ops"])