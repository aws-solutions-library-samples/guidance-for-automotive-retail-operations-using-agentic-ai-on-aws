"""A1 — configurator + shortlist tools for Discovery (spec §3.1, §5.5).

  get_configurator_options(model_id)          -> trims + packages (the sandbox configurator connector)
  get_similar_vehicles(model_id)              -> same-segment alternatives (A1 similar-items fallback)
  shortlist(customer_id, answers, need)       -> LifestyleProfile + ranked trims (stateless)
  record_shortlist(journey_id, model, trim)   -> PLAT_Configurations row + build.saved; the journey's
                                                 shortlist / saved_build context is set and the journey
                                                 advances out of DISCOVER / CONFIGURE when the gate allows
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any

from ..common import ddb
from ..events import bus
from ..journeys import supervisor
from . import discovery


class NotFound(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_configurator_options(model_id: str, env_prefix: str | None = None) -> dict[str, Any]:
    rows = ddb.scan_filter("OEM_Catalog", "model_id", model_id, env_prefix)
    trims = sorted([r for r in rows if r.get("kind") is None], key=lambda r: float(r.get("list_price") or 0))
    packages = [r for r in rows if r.get("kind") == "package"]
    if not trims:
        raise NotFound(f"no trims for model {model_id}")
    model = next((m for m in ddb.scan_all("OEM_CatalogModels", env_prefix) if m.get("model_id") == model_id), {})
    keep = ("trim_id", "trim_name", "list_price", "currency", "powertrain", "drive", "seats", "efficiency",
            "efficiency_unit", "ev_range", "range_unit", "features", "source_ref")
    return {"model_id": model_id, "name": model.get("name"), "body": model.get("body"),
            "trims": [{k: t.get(k) for k in keep} for t in trims],
            "packages": [{k: p.get(k) for k in ("trim_id", "package_name", "list_price", "currency",
                                                 "addresses_need", "source_ref")} for p in packages],
            "source_ref": f"configurator(sandbox):OEM_Catalog:{model_id}"}


def get_similar_vehicles(model_id: str, env_prefix: str | None = None) -> list[dict[str, Any]]:
    models = ddb.scan_all("OEM_CatalogModels", env_prefix)
    me = next((m for m in models if m["model_id"] == model_id), None)
    if me is None:
        raise NotFound(f"unknown model {model_id}")
    fam = lambda m: "suv" if any(w in (m.get("body") or "").lower() for w in ("suv", "crossover")) else (m.get("body") or "").lower()  # noqa: E731
    out = [{"model_id": m["model_id"], "name": m.get("name"), "body": m.get("body"),
            "reason": "same segment" if m.get("segment") == me.get("segment") else "same body family"}
           for m in models if m["model_id"] != model_id and m.get("market_id") == me.get("market_id")
           and (m.get("segment") == me.get("segment") or fam(m) == fam(me))]
    return out


def shortlist(customer_id: str | None, answers: dict[str, Any] | None = None, journey_id: str | None = None,
              env_prefix: str | None = None) -> dict[str, Any]:
    cust = ddb.get_item("DEALER_Customers", {"customer_id": customer_id}, env_prefix) if customer_id else None
    signals = ddb.query_hash("DEALER_Signals", "customer_id", customer_id, env_prefix) if customer_id else []
    profile = discovery.infer_lifestyle_profile(cust, signals, answers or {})
    ranked = discovery.rank_vehicles(journey_id, env_prefix) if journey_id else {"matches": []}
    return {"customer_id": customer_id, "profile": profile, "matches": ranked.get("matches", []),
            "source_ref": "A1.shortlist (infer_lifestyle_profile + rank_vehicles)"}


def record_shortlist(journey_id: str, model_id: str, trim_id: str, actor: str,
                     packages: list[str] | None = None, color: str | None = None,
                     env_prefix: str | None = None, advance: bool = True) -> dict[str, Any]:
    """Save a build (PLAT_Configurations) and record the shortlist / saved-build context.
    advance=True (an explicit "save build") also moves a DISCOVER / CONFIGURE journey on;
    advance=False (presenting a trim on the floor) satisfies both gates and leaves the stage."""
    j = ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix)
    if j is None:
        raise NotFound(f"journey {journey_id} not found")
    opts = get_configurator_options(model_id, env_prefix)
    trim = next((t for t in opts["trims"] if t["trim_id"] == trim_id), None)
    if trim is None:
        raise NotFound(f"trim {trim_id} is not offered on {model_id}")
    pk = [p for p in opts["packages"] if p["trim_id"] in (packages or [])]
    total = float(trim["list_price"]) + sum(float(p["list_price"] or 0) for p in pk)
    cust = ddb.get_item("DEALER_Customers", {"customer_id": j.get("customer_id")}, env_prefix) or {}
    state = (cust.get("address") or {}).get("state")
    market = j.get("market_id") or cust.get("market_id")
    price = discovery.price_build(total, trim.get("powertrain") or "", market, state,
                                  (cust.get("budget") or {}).get("down") or 0, env_prefix)
    ts = _now()
    cid = "CFG-" + hashlib.sha1(f"{journey_id}|{trim_id}|{','.join(sorted(packages or []))}".encode()).hexdigest()[:12]
    row = {"config_id": cid, "journey_id": journey_id, "customer_id": j.get("customer_id"), "model_id": model_id,
           "trim_id": trim_id, "packages": [p["trim_id"] for p in pk], "color": color,
           "currency": price["currency"], "price_vehicle": total,
           # every applicable incentive (price, informational, APR program) — the F&I worksheet labels
           # each by what it does to the deal; `incentives_total` sums the price-reducing ones only
           "incentives_applied": [a["incentive_id"] for a in price["incentives_applied"]],
           "incentives_total": price["incentives_total"],
           "price_tax_est": sum(x.get("amount") or 0 for x in price["tax"]),
           "price_total": price["total_out_the_door"], "est_monthly_buy": price["est_monthly"]["buy"],
           "est_monthly_lease": price["est_monthly"]["lease"], "saved_at": ts, "saved_by": actor,
           "share_token": hashlib.sha1(cid.encode()).hexdigest()[:10], "source_ref": price["source_ref"]}
    ddb.put_item("PLAT_Configurations", row, env_prefix)
    bus.emit("build.saved", {"journey_id": journey_id, "customer_id": j.get("customer_id"),
                             "dealer_id": j.get("dealer_id"), "config_id": cid, "model_id": model_id, "trim_id": trim_id},
             actor=actor, resource_url=f"/r/journey/{journey_id}", env_prefix=env_prefix)
    advanced = None
    if advance and j.get("stage") in ("DISCOVER", "CONFIGURE"):
        try:
            advanced = supervisor.advance_journey(journey_id, f"build {cid} saved", actor,
                                                  context_updates={"shortlist": True, "saved_build": True})
        except (supervisor.HandoffIncomplete, supervisor.InvalidTransition) as e:
            advanced = {"blocked": str(e)}
    else:
        j = dict(ddb.get_item("DEALER_Journeys", {"journey_id": journey_id}, env_prefix))
        j["context"] = {**(j.get("context") or {}), "shortlist": True, "saved_build": True}
        j["primary_model_id"] = model_id
        ddb.put_item("DEALER_Journeys", j, env_prefix)
    return {"configuration": row, "price": price, "journey_advance": advanced}
