"""Core entity generators: org/users, catalog, inventory, customers, signals, leads, consent, journeys, events,
configurations, appointments. Everything downstream (commerce, OEM, platform, corpora) reads from `G.t[...]`."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta

from . import anchors as A
from .config import RunConfig, STAGES, STAGE_MIX, REQUIRED_CONTEXT, iso, day
from .market import Market, market_config_row, region_rules
from .storylines import JOURNEYS, COLD_COHORT_ID, DISTRICT, CONDITIONS, OBJECTION_TYPES

GROUPS_DEALER = ["dealer_manager", "dealer_advisor", "dealer_advisor", "dealer_advisor", "dealer_bdc", "dealer_fni",
                 "dealer_service", "dealer_inventory", "dealer_delivery"]
GROUPS_OEM = ["oem_dm", "oem_regional", "oem_ci_planning", "oem_sales_ops", "oem_finance", "oem_marketing", "oem_cx",
              "admin_tenant", "admin_dealer"]

# First model year a conquest nameplate existed (public launch years; models not listed are
# treated as long-running). Keeps a drawn "current vehicle" from pre-dating its own launch.
_FIRST_MODEL_YEAR_REAL = {
    "EV6": 2022, "IONIQ 5": 2022, "IONIQ 6": 2023, "Kona Electric": 2019, "Mustang Mach-E": 2021,
    "Equinox EV": 2024, "ID.4": 2021, "ID.3": 2020, "ID.7": 2024, "Model Y": 2020, "Model 3": 2017,
    "i4": 2022, "Telluride": 2020, "Palisade": 2020, "Born": 2022, "Enyaq": 2021, "E-208": 2020,
    "5 E-Tech": 2024, "Ranger": 2019, "Traverse": 2018, "Tucson": 2016, "Sportage": 2016,
}
FIRST_MODEL_YEAR = {A.DISPLAY_MODEL.get(k, k): v for k, v in _FIRST_MODEL_YEAR_REAL.items()}

# Incentives that are NOT a point-of-sale reduction of the vehicle price (mirrors
# app/services/agents/discovery.py: loan-interest deductions, charger / wallbox rebates).
_NON_PRICE_INCENTIVE_IDS = {"US-FED-LOAN-INTEREST", "US-UTIL-HOME-CHARGER", "DE-REG-WALLBOX-GENERIC"}
_NON_PRICE_KEYWORDS = ("loan interest", "interest deduction", "home charger", "charger rebate", "wallbox")


def _reduces_vehicle_price(inc: dict) -> bool:
    if inc.get("incentive_id") in _NON_PRICE_INCENTIVE_IDS or inc.get("apr_special"):
        return False
    name = (inc.get("name") or "").lower()
    return not any(k in name for k in _NON_PRICE_KEYWORDS)


VIN_CHARS = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
VIN_VALUES = {c: v for c, v in zip("ABCDEFGHJKLMNPRSTUVWXYZ", [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9])}
VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check(v16: str) -> str:
    total = 0
    for i, ch in enumerate(v16[:8] + "0" + v16[8:]):
        val = int(ch) if ch.isdigit() else VIN_VALUES[ch]
        total += val * VIN_WEIGHTS[i]
    r = total % 11
    return "X" if r == 10 else str(r)


def make_vin(rng, wmi: str, seq: int) -> str:
    vds = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(5))
    year = "T"  # 2026
    plant = rng.choice("ABCDEFGH")
    serial = f"{seq:06d}"
    body = wmi + vds + year + plant + serial  # 16 chars without check digit at pos 9
    return body[:8] + vin_check(body) + body[8:]


class G:
    """Generator state: tables (name -> list[dict]) plus indexes."""

    def __init__(self, cfg: RunConfig, m: Market):
        self.cfg, self.m, self.rng = cfg, m, cfg.rng
        self.t: dict[str, list[dict]] = {}
        self.ix: dict[str, dict] = {}
        self._seq: dict[str, int] = {}

    # ── helpers ─────────────────────────────────────────────────────────────
    def nid(self, prefix: str) -> str:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        return f"{prefix}-{self.m.id.upper()}-{self._seq[prefix]:05d}"

    def add(self, table: str, row: dict) -> dict:
        self.t.setdefault(table, []).append(row)
        return row

    def pick(self, seq):
        return self.rng.choice(seq)

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def region_of_dealer(self, dealer_code: str) -> str:
        return self.ix["dealer"][dealer_code]["region_code"]

    # ── 1. org + users ─────────────────────────────────────────────────────
    def gen_org_users(self):
        m, d = self.m, DISTRICT[self.m.id]
        self.add("OEM_Markets", market_config_row(m))
        for r in region_rules(m):
            self.add("OEM_StateRules", r)
        org = self.t.setdefault("PLAT_OrgHierarchy", [])
        org.append({"node_id": "OEM", "type": "oem", "name": m.brand, "parent_id": None})
        org.append({"node_id": d["region_id"], "type": "region", "name": d["region_name"], "parent_id": "OEM"})
        org.append({"node_id": d["district_id"], "type": "district", "name": d["name"], "parent_id": d["region_id"], "dm_user_id": None})
        other = "DIST-08" if m.id == "us" else "DIST-N"
        org.append({"node_id": other, "type": "district", "name": "District 8 – Gulf" if m.id == "us" else "Bezirk Nord", "parent_id": d["region_id"]})
        groups = [("DG-01", "Peach State Auto Group" if m.id == "us" else "Autohaus-Gruppe Neckar", [0, 1, 2, 3]),
                  ("DG-02", "Coastal Motors Group" if m.id == "us" else "Südbaden Mobil GmbH", [6, 8, 9])]
        for gid, gname, idxs in groups:
            org.append({"node_id": gid, "type": "dealer_group", "name": gname, "parent_id": d["district_id"], "dealer_codes": [m.dealers[i].code for i in idxs]})
        self.ix["dealer"] = {}
        for i, dl in enumerate(m.dealers):
            grp = next((gid for gid, _, idxs in groups if i in idxs), None)
            row = {"node_id": dl.code, "type": "dealer", "name": dl.name, "parent_id": grp or d["district_id"], "district_id": d["district_id"],
                   "dealer_group_id": grp, "city": dl.city, "region_code": dl.region, "lat": dl.lat, "lon": dl.lon, "tier": dl.tier,
                   "pma": f"PMA-{dl.city[:3].upper()}", "condition": dl.condition, "timezone": m.tz}
            org.append(row)
            self.ix["dealer"][dl.code] = row

        # users
        users = self.t.setdefault("PLAT_Users", [])
        self.ix["users_by_dealer"] = {}
        self.ix["dm_user"] = None
        full = {0, 1, 2}  # fully staffed dealers
        for i, dl in enumerate(m.dealers):
            roles = GROUPS_DEALER if i in full else ["dealer_manager", "dealer_advisor"]
            self.ix["users_by_dealer"][dl.code] = {}
            for r in roles:
                fn, ln = self.pick(m.first_names), self.pick(m.last_names)
                uid = self.nid("U")
                u = {"user_id": uid, "username": f"{fn.lower()}.{ln.lower()}.{dl.code[-3:]}", "given_name": fn, "family_name": ln,
                     "email": m.email(fn, ln, self.rng), "groups": [r], "scope_node_id": dl.code, "dealer_id": dl.code,
                     "district_id": d["district_id"], "market_id": m.id, "quick_provisioned": r in ("dealer_manager", "dealer_bdc"),
                     "federated_identity": None, "test_password": "IrpoDemo2026!", "active": True}
                users.append(u)
                self.ix["users_by_dealer"][dl.code].setdefault(r, []).append(uid)
        oem_names = {"oem_dm": d["dm"], "oem_regional": d["regional"]}
        for r in GROUPS_OEM:
            fn, ln = (oem_names.get(r) or f"{self.pick(m.first_names)} {self.pick(m.last_names)}").split(" ", 1)
            uid = self.nid("U")
            scope = {"oem_dm": d["district_id"], "oem_regional": d["region_id"], "admin_dealer": m.dealers[0].code}.get(r, "OEM")
            u = {"user_id": uid, "username": f"{fn.lower()}.{ln.lower()}", "given_name": fn, "family_name": ln,
                 "email": f"{fn.lower()}.{ln.lower()}@{m.brand_short.lower()}-oem.example.com", "groups": [r], "scope_node_id": scope,
                 "dealer_id": m.dealers[0].code if r == "admin_dealer" else None, "district_id": d["district_id"] if r in ("oem_dm", "admin_dealer") else None,
                 "market_id": m.id, "quick_provisioned": r.startswith("oem_"),
                 "federated_identity": f"CorpDirectory:{fn.lower()}{ln[0].lower()}" if r in ("oem_dm", "admin_tenant") else None,
                 "test_password": "IrpoDemo2026!", "active": True}
            users.append(u)
            if r == "oem_dm":
                self.ix["dm_user"] = uid
                org[2]["dm_user_id"] = uid
            self.ix.setdefault("oem_users", {})[r] = uid

    # ── 1b. service staff (technicians, parts, service managers) ──────────────
    def gen_service_staff(self):
        """Fixed Ops personas the spec's §5.2 matrix needs and the first packs
        lacked: dealer_technician (with a skills roster — `hv_certified` gates EV
        high-voltage lines in A7 dispatch_ro), dealer_parts and
        dealer_service_manager. Runs late (band 89) on its OWN deterministic RNG
        stream so it never shifts the ids or draws of the earlier phases: every
        pre-existing row is byte-identical; these users are appended."""
        import random as _random
        m, d = self.m, DISTRICT[self.m.id]
        rng = _random.Random(f"{self.cfg.seed}:{m.id}:service-staff")
        users = self.t.setdefault("PLAT_Users", [])
        full = {0, 1, 2}  # fully staffed dealers (same set as gen_org_users)
        skill_pool = ["brakes", "diagnostics", "alignment", "hvac", "driveline", "electrical", "ev_battery"]
        for i, dl in enumerate(m.dealers):
            plan = ([("dealer_technician", ["hv_certified"]), ("dealer_technician", []),
                     ("dealer_parts", None), ("dealer_service_manager", None)]
                    if i in full else [("dealer_technician", ["hv_certified"] if i % 2 == 0 else [])])
            for r, extra in plan:
                fn, ln = rng.choice(m.first_names), rng.choice(m.last_names)
                uid = self.nid("U")
                u = {"user_id": uid, "username": f"{fn.lower()}.{ln.lower()}.{dl.code[-3:]}", "given_name": fn,
                     "family_name": ln, "email": m.email(fn, ln, rng), "groups": [r], "scope_node_id": dl.code,
                     "dealer_id": dl.code, "district_id": d["district_id"], "market_id": m.id,
                     "quick_provisioned": r == "dealer_service_manager", "federated_identity": None,
                     "test_password": "IrpoDemo2026!", "active": True}
                if r == "dealer_technician":
                    u["skills"] = sorted(set(rng.sample(skill_pool, 3) + (extra or [])))
                    u["certifications"] = ["ev_hv_l2"] if "hv_certified" in u["skills"] else []
                users.append(u)
                self.ix["users_by_dealer"].setdefault(dl.code, {}).setdefault(r, []).append(uid)

    # ── 2. catalog ─────────────────────────────────────────────────────────
    def gen_catalog(self):
        m = self.m
        lu = A.lineup()["brands"][m.id]
        trims_mult = [1.00, 1.08, 1.18, 1.30]
        self.ix["models"] = {}
        self.ix["trims_by_model"] = {}
        for mod in lu["models"]:
            anchor = mod.get("msrp_anchor_usd") or mod.get("uvp_anchor_eur")
            spec = self._model_spec(mod)
            pts = mod["powertrains"]
            model_row = {"model_id": mod["model_id"], "brand": m.brand, "name": mod["name"], "body": mod["body"],
                         "powertrains": pts, "segment": mod.get("epa_class") or mod.get("kba_segment"), "market_id": m.id,
                         "competitors": [{"make": c["make"], "model": c["model"], "base_price": c.get("base_msrp_usd") or c.get("base_uvp_eur"),
                                          "source_url": None, "verify": c.get("verify", True),
                                          "intel": self._intel(mod, c)} for c in mod["competitors"]],
                         "us_final_assembly": mod.get("us_final_assembly"), "source_ref": "curated:lineup.json:" + mod["model_id"]}
            self.ix["models"][mod["model_id"]] = model_row
            self.ix["trims_by_model"][mod["model_id"]] = []
            for ti, tname in enumerate(mod["trims"]):
                pt = pts[min(ti, len(pts) - 1)] if len(pts) > 1 and ti >= len(mod["trims"]) - 1 and "HEV" in pts else pts[0]
                if "PHEV" in pts and ti == len(mod["trims"]) - 1:
                    pt = "PHEV"
                price = round(anchor * trims_mult[ti] / 100) * 100
                s = spec[pt]
                # EV range and battery differ by trim: the segment anchor is the mid trim; a
                # "Standard" pack is smaller, "Extended" larger, AWD/Performance trade a little
                # back for the second motor. Identical range on every trim reads as a stub.
                rf = 1.0
                if pt == "EV":
                    low = tname.lower()
                    rf = 0.86 if "standard" in low else 1.12 if "extended" in low else 1.0
                    if "awd" in low or "performance" in low or "4x4" in low:
                        rf -= 0.06
                ev_range = round(s["range"] * rf) if pt == "EV" and s.get("range") else s.get("range")
                battery = round(s["battery"] * rf) if pt == "EV" and s.get("battery") else s.get("battery")
                # a trim reads with its model ("Crosstrail SE", not "SE"): the lineup names Ion trims in
                # full and the rest by grade alone, and every screen showed whatever it got
                short = mod["name"].split(" ", 1)[1] if " " in mod["name"] else mod["name"]
                full_name = tname if tname.startswith(short.split()[0]) else f"{short} {tname}"
                trim = {"model_id": mod["model_id"], "trim_id": f"{mod['model_id']}-T{ti+1}", "trim_name": full_name,
                        "grade": tname, "powertrain": pt,
                        "list_price": price, "price_includes_tax": m.id == "de", "currency": m.currency,
                        "efficiency": s["eff"], "efficiency_unit": m.efficiency_unit_ev if pt == "EV" else m.efficiency_unit_ice,
                        "ev_range": ev_range, "range_unit": m.distance_unit if pt == "EV" else None,
                        "battery_kwh": battery, "co2_g_km": s.get("co2"), "power_kw": s.get("kw"), "mass_kg": s.get("mass"),
                        "seats": 7 if "3-row" in mod["body"] or "Mittelklasse-SUV" in mod["body"] and ti >= 2 else 5,
                        "drive": "AWD" if ("AWD" in tname or "4x4" in tname or ti >= 2 and "SUV" in mod["body"]) else "FWD" if pt != "EV" else "RWD",
                        "features": self._features(ti, pt), "source_ref": s["source_ref"]}
                self.add("OEM_Catalog", trim)
                self.ix["trims_by_model"][mod["model_id"]].append(trim)
            # packages
            for pi, (pname, pprice, need) in enumerate(self._packages(mod)):
                self.add("OEM_Catalog", {"model_id": mod["model_id"], "trim_id": f"{mod['model_id']}-P{pi+1}", "kind": "package",
                                          "package_name": pname, "list_price": pprice, "currency": m.currency, "addresses_need": need,
                                          "source_ref": "curated:lineup.json:" + mod["model_id"]})
        self.t.setdefault("OEM_CatalogModels", list(self.ix["models"].values()))

    def _model_spec(self, mod) -> dict:
        """Per-powertrain efficiency/range/co2 anchors from EPA (us) or EEA + range anchors (de)."""
        m = self.m
        out = {}
        for pt in mod["powertrains"]:
            if m.id == "us":
                seg = A.epa_segments().get((mod["epa_class"], pt)) or A.epa_segments().get((mod["epa_class"], "ICE"))
                comp = [A.epa_model(c["anchor_make"], c["anchor_model"]) for c in mod["competitors"]]
                comp = [c for c in comp if c and c["powertrain"] == pt] or [c for c in comp if c]
                eff = self._median([A.fnum(c["comb_mpg_or_mpge"]) for c in comp]) or A.fnum(seg and seg["median_comb_mpg_or_mpge"], 28)
                rng_ = self._median([A.fnum(c["ev_range_mi"]) for c in comp if A.fnum(c["ev_range_mi"], 0) > 0]) or A.fnum(seg and seg["median_ev_range_mi"], 280)
                out[pt] = {"eff": round(eff), "range": round(rng_) if pt in ("EV",) else (round(rng_ * 0.12) if pt == "PHEV" else None),
                           "battery": 82 if pt == "EV" and "Mid" in mod["body"] else 65 if pt == "EV" else 18 if pt == "PHEV" else None,
                           "co2": None if pt == "EV" else round(8887 / eff / 1.609) if eff else None,
                           "source_ref": f"epa_fueleconomy:agg:{mod['epa_class']}:{pt}"}
            else:
                fuel = {"EV": "ELECTRIC", "PHEV": "PETROL/ELECTRIC", "HEV": "PETROL/ELECTRIC", "ICE": "PETROL"}[pt]
                rows = [r for k in mod["eea_anchor_models"] for r in A.eea_rows(k) if r["fuel"] == fuel] or \
                       [r for k in mod["eea_anchor_models"] for r in A.eea_rows(k)]
                co2 = self._median([A.fnum(r["co2_wltp_g_km"]) for r in rows if A.fnum(r["co2_wltp_g_km"], 0) > 0]) if pt != "EV" else 0
                fc = self._median([A.fnum(r["fuel_l_100km"]) for r in rows if A.fnum(r["fuel_l_100km"], 0) > 0])
                wh = self._median([A.fnum(r["wh_per_km"]) for r in rows if A.fnum(r["wh_per_km"], 0) > 0])
                kw = self._median([A.fnum(r["power_kw"]) for r in rows]) or 110
                mass = self._median([A.fnum(r["mass_kg"]) for r in rows]) or 1500
                rk = A.de_ev_range().get(mod.get("range_anchor_segment", ""), {})
                if pt == "EV":
                    out[pt] = {"eff": round((wh or 165) / 10, 1), "range": rk.get("anchor_range_km_median", 480),
                               "battery": rk.get("anchor_battery_kwh_median", 77), "co2": 0, "kw": round(kw), "mass": round(mass),
                               "source_ref": f"eea_co2_de:{self._eea_label(mod)}:ELECTRIC;curated:de/ev_range_anchors.json"}
                else:
                    eff = fc or (5.2 if pt == "HEV" else 1.6 if pt == "PHEV" else 6.8)
                    if pt == "HEV":
                        eff, co2 = round(eff * 0.78, 1), round((co2 or 140) * 0.78)
                    if pt == "PHEV":
                        eff, co2 = 1.6, 36
                    out[pt] = {"eff": round(eff, 1), "range": 85 if pt == "PHEV" else None, "battery": 19 if pt == "PHEV" else None,
                               "co2": round(co2 or 135), "kw": round(kw), "mass": round(mass),
                               "source_ref": f"eea_co2_de:{self._eea_label(mod)}:{fuel}"}
        return out

    @staticmethod
    def _median(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else None

    def _features(self, ti: int, pt: str) -> list[str]:
        base = ["LED headlights", "8-in touchscreen", "Wireless CarPlay/Android Auto", "Adaptive cruise", "Lane keeping"]
        mid = ["Heated seats", "Power liftgate", "12.3-in display", "360 camera", "Blind-spot monitor"]
        top = ["Panoramic roof", "Premium audio", "Ventilated seats", "Head-up display", "Hands-free highway assist"]
        f = base + (mid if ti >= 1 else []) + (top if ti >= 2 else [])
        if pt == "EV":
            f += ["Heat pump", "Vehicle-to-load", "DC fast charge 10-80% in ~28 min"]
        return f

    def _packages(self, mod):
        m = self.m
        mult = 1.0 if m.id == "us" else 0.95
        body = mod["body"].lower()
        pk = [("Tech & Safety", round(1800 * mult), "tech_affinity"), ("Cold Weather", round(950 * mult), "commute")]
        if "pickup" in body or "suv" in body or "kombi" in body:
            pk.append(("Tow & Haul" if m.id == "us" else "Anhänger-Paket", round(1450 * mult), "towing_or_hauling"))
        if "ev" in body or mod["powertrains"] == ["EV"]:
            pk.append(("Home Charging Bundle" if m.id == "us" else "Wallbox-Paket", round(1400 * mult), "charging_access"))
        if "3-row" in body or "family" in body:
            pk.append(("Family Entertainment", round(2100 * mult), "household_size"))
        return pk

    @staticmethod
    def _eea_label(mod) -> str:
        """Generic stand-ins for the EEA anchor keys, so output never names a real model."""
        out = []
        for k in mod["eea_anchor_models"]:
            kk = k.lower().replace(" ", "")
            hit = next((c["model"] for c in mod["competitors"]
                        if kk in c["anchor_model"].lower().replace(" ", "") or c["anchor_model"].lower().replace(" ", "") in kk), None)
            out.append(hit or "anchor")
        return ",".join(out)

    def _intel(self, mod, c) -> dict:
        return {"why_we_win": ["Lower total cost of ownership over 5 years", "Longer warranty", "Local dealer service network"],
                "why_we_lose": ["Brand recognition", "Charging network (EV)" if "EV" in mod["powertrains"] else "Resale history"],
                "win_rate": round(self.rng.uniform(0.44, 0.66), 2)}

    # ── 3. inventory ───────────────────────────────────────────────────────
    def gen_inventory(self):
        m, cfg = self.m, self.cfg
        wmi = "7M7" if m.id == "us" else "7N7"
        colors = ["Glacier White", "Obsidian Black", "Meridian Blue" if m.id == "us" else "Nordsee Blau", "Graphite", "Ember Red", "Silver Mist"]
        seq = 1
        trims = [t for t in self.t["OEM_Catalog"] if "trim_name" in t]
        self.ix["inventory_by_dealer"] = {}
        for dl in m.dealers:
            cond = CONDITIONS[dl.condition]
            for _ in range(cfg.volumes.inventory_per_dealer):
                tr = self.pick(trims)
                is_ev = tr["powertrain"] == "EV"
                age = int(self.rng.expovariate(1 / 38))
                if cond.get("ev_aging") and is_ev:
                    age = int(self.rng.expovariate(1 / 95)) + 40
                st = self.rng.choices(["in_stock", "in_transit", "reserved", "sold"], [75, 12, 8, 5])[0]
                row = {"vehicle_id": self.nid("VH"), "vin": make_vin(self.rng, wmi, seq), "dealer_id": dl.code, "model_id": tr["model_id"],
                       "trim_id": tr["trim_id"], "trim_name": tr["trim_name"], "powertrain": tr["powertrain"], "color": self.pick(colors),
                       "status": st, "age_days": 0 if st == "in_transit" else age, "eta_days": self.rng.randint(5, 40) if st == "in_transit" else 0,
                       "list_price": tr["list_price"] + self.pick([0, 0, 950, 1450, 1800]), "cost": round(tr["list_price"] * 0.91),
                       "market_id": m.id, "stock_number": f"{dl.code[-3:]}{seq:05d}", "demand_score": None,
                       "source_ref": tr["source_ref"]}
                seq += 1
                self.add("DEALER_Inventory", row)
                self.ix["inventory_by_dealer"].setdefault(dl.code, []).append(row)

    # ── 4. customers + consent ─────────────────────────────────────────────
    def gen_customers(self):
        m, cfg = self.m, self.cfg
        places = [p for p in A.census_places() if p["state"] in m.bulk_regions] if m.id == "us" else None
        de_cities = [(d.city, d.region, d.lat, d.lon) for d in m.dealers] + [("München", "BY", 48.137, 11.575), ("Frankfurt am Main", "HE", 50.110, 8.682),
                                                                              ("Mainz", "RP", 49.993, 8.247), ("Köln", "NW", 50.938, 6.960), ("Berlin", "BE", 52.520, 13.405)]
        self.ix["customer"] = {}
        self.ix["golden"] = {}
        # golden first (fixed ids)
        for j in JOURNEYS:
            fn, ln = j[m.id]
            dl = m.dealers[j["dealer_idx"]]
            cid = f"C-{m.id.upper()}-{j['id']}"
            cust = self._customer(cid, fn, ln, dl.city, dl.region, dl.lat, dl.lon, dl.code, golden=j)
            self.ix["golden"][j["id"]] = cust
        # bulk
        for i in range(cfg.volumes.customers - len(JOURNEYS)):
            fn, ln = self.pick(m.first_names), self.pick(m.last_names)
            if self.chance(0.8):
                dl = self.pick(m.dealers)
                city, region, lat, lon = dl.city, dl.region, dl.lat, dl.lon
            elif m.id == "us":
                p = self.pick(places)
                city, region, lat, lon = p["place"], p["state"], float(p["lat"]), float(p["lon"])
                dl = min(m.dealers, key=lambda d: (d.lat - lat) ** 2 + (d.lon - lon) ** 2)
            else:
                city, region, lat, lon = self.pick(de_cities)
                dl = min(m.dealers, key=lambda d: (d.lat - lat) ** 2 + (d.lon - lon) ** 2)
            self._customer(self.nid("C"), fn, ln, city, region, lat + self.rng.uniform(-.15, .15), lon + self.rng.uniform(-.15, .15), dl.code)

    def _customer(self, cid, fn, ln, city, region, lat, lon, dealer_code, golden=None) -> dict:
        m = self.m
        rng = self.rng
        advisors = self.ix["users_by_dealer"][dealer_code].get("dealer_advisor") or [None]
        income = rng.choices(["<50k", "50-75k", "75-100k", "100-150k", "150k+"], [15, 22, 25, 25, 13])[0] if m.id == "us" \
            else rng.choices(["<35k", "35-50k", "50-75k", "75-100k", "100k+"], [15, 24, 28, 20, 13])[0]
        hh = golden["household"] if golden else rng.choices([1, 2, 3, 4, 5], [22, 34, 20, 17, 7])[0]
        commute = golden["commute"] if golden else int(rng.gauss(30 if m.id == "us" else 28, 12) // 1) or 10
        has_vehicle = bool(golden and (golden.get("current_vehicle") or golden.get("trade") or golden.get("owned"))) or (not golden and self.chance(0.55))
        cur = None
        if has_vehicle:
            if golden and golden.get("current_vehicle"):
                mk, md, yr, mi = golden["current_vehicle"][m.id]
            elif golden and golden.get("trade"):
                mk, md, yr, mi, _ = golden["trade"][m.id]
            elif golden and golden.get("owned"):
                # "2023 Meridian Ion C", not "2023 Meridian Motors Meridian Ion C"
                mk = m.brand.split()[0]
                md = self.ix["models"][golden["model"][m.id]]["name"]
                md = md[len(mk):].strip() if md.startswith(mk) else md
                yr, mi = 2023, 41000 if m.id == "us" else 62000
            else:
                comp = self.pick([c for mod in self.ix["models"].values() for c in mod["competitors"]])
                mk, md = comp["make"], comp["model"]
                # never a model year before the nameplate existed (a "2019 Pulse 6" reads as fake)
                yr = rng.randint(max(2014, FIRST_MODEL_YEAR.get(md, 2014)), 2024)
                mi = int((2026 - yr) * rng.uniform(9000, 16000) * (1 if m.id == "us" else 1.4))
            own = "lease" if (golden and golden.get("owned")) or self.chance(0.28) else "loan" if self.chance(0.6) else "owned"
            mk, md = A.display_vehicle(mk, md)
            cur = {"make": mk, "model": md, "year": yr, "mileage": mi, "distance_unit": m.distance_unit, "ownership": own,
                   "months_remaining": golden["owned"]["lease_months_left"] if golden and golden.get("owned") else (rng.randint(2, 30) if own != "owned" else 0),
                   "payoff": golden["trade"][m.id][4] if golden and golden.get("trade") else (round(rng.uniform(4000, 22000)) if own == "loan" else 0)}
        lifestyle = None
        if golden or self.chance(0.4):
            pt_open = golden["powertrain"].lower() if golden else rng.choices(["ice", "hybrid", "ev", "undecided"], [35, 25, 20, 20])[0]
            lifestyle = {"household_size": hh, "commute_per_day": commute, "distance_unit": m.distance_unit,
                         "road_trip_frequency": rng.choice(["rare", "monthly", "weekly"]),
                         "towing_or_hauling": bool(golden and "TRAIL" in golden["model"][m.id]) or self.chance(0.15),
                         "outdoor_activities": rng.sample(["camping", "boating", "cycling", "skiing", "hiking", "none"], 2),
                         "tech_affinity": rng.choice(["low", "medium", "high"]), "safety_priority": rng.choice(["medium", "high"]),
                         "powertrain_openness": pt_open, "ownership_style": rng.choice(["buy", "lease"]),
                         "confidence": 0.86 if golden and golden.get("intake_answered") else round(rng.uniform(0.45, 0.8), 2)}
        addr = m.address(rng, city, region)
        c = {"customer_id": cid, "given_name": fn, "family_name": ln, "email": m.email(fn, ln, rng), "phone": m.phone(rng),
             "address": addr, "region_code": region, "lat": round(lat, 4), "lon": round(lon, 4), "market_id": m.id,
             "dealer_id": dealer_code, "assigned_advisor_user_id": self.pick(advisors), "income_band": income, "household_size": hh,
             "commute_per_day": commute, "lifestyle_profile": lifestyle, "current_vehicle": cur,
             "budget": self._budget(income, golden), "account_id": ("ACC-FLEET-01" if golden and golden.get("fleet_account") else None),
             "is_business": bool(golden and golden.get("fleet_account")), "golden_story": golden["id"] if golden else None,
             "notes": [], "created_at": iso(self.cfg.ago(days=rng.randint(30, 900))), "data_class": "C3",
             "source_ref": "synthetic:customers"}
        self.add("DEALER_Customers", c)
        self.ix["customer"][cid] = c
        # Consent varies by customer (not everyone opted in): derived from a hash of the seed + id, so it
        # draws nothing from the RNG and the rest of the data layer does not move. Golden (scripted demo)
        # customers keep their storyline consent.
        _h = hashlib.sha256(f"{self.cfg.seed}:{cid}:consent".encode()).digest()
        email_ok = True if golden else _h[0] / 255 >= 0.22          # ~78% e-mail marketing opt-in
        sms_ok = (not golden.get("sms_opt_out_days_ago")) if golden else _h[1] / 255 >= 0.5   # ~50% SMS
        self.add("CUST_Consent", {"customer_id": cid, "email_opt_in": email_ok, "sms_opt_in": sms_ok,
                                  "sms_opt_out_at": iso(self.cfg.ago(days=golden["sms_opt_out_days_ago"])) if golden and golden.get("sms_opt_out_days_ago") else None,
                                  "voice_opt_in": self.chance(0.4), "quiet_hours": {"start": "20:00", "end": "08:00", "tz": m.tz},
                                  "preferred_channel": rng.choice(["email", "sms", "email", "chat"]), "market_id": m.id,
                                  "updated_at": iso(self.cfg.ago(days=rng.randint(0, 200)))})
        return c

    def _budget(self, income, golden):
        m = self.m
        lo = {"<50k": 22000, "50-75k": 28000, "75-100k": 34000, "100-150k": 42000, "150k+": 55000,
              "<35k": 20000, "35-50k": 26000, "50-75k ": 32000, "75-100k ": 40000, "100k+": 52000}.get(income, 30000)
        if golden:
            mod = self.ix["models"][golden["model"][m.id]]
            base = self.ix["trims_by_model"][mod["model_id"]][0]["list_price"]
            lo = round(base * 0.9 / 1000) * 1000
        return {"price_min": lo, "price_max": round(lo * 1.4 / 1000) * 1000, "monthly_max": round(lo * 0.0185 / 10) * 10, "down": round(lo * 0.1 / 500) * 500, "currency": m.currency}

    # ── 5. signals, leads ──────────────────────────────────────────────────
    def gen_signals_leads(self):
        m, cfg, rng = self.m, self.cfg, self.rng
        models = list(self.ix["models"].values())
        content = ["EV Running Cost vs Gas" if m.id == "us" else "Stromkosten vs. Benzin: der Vergleich", "Towing guide", "Lease vs buy explained",
                   "Charging at home 101", "2026 incentive explainer", "Family SUV comparison"]
        sources = ["web_form", "third_party_adf", "chat", "phone", "walk_in", "service_lane", "campaign"]
        self.ix["signals_by_customer"] = {}
        for c in self.t["DEALER_Customers"]:
            g = next((j for j in JOURNEYS if j["id"] == c["golden_story"]), None)
            n = 27 if g and g["id"] == "J1" else (rng.randint(12, 30) if g else rng.choices([0, 2, 5, 9, 14], [20, 25, 25, 20, 10])[0])
            if g and g["id"] == "J1":
                n = 27
            fav = self.ix["models"][g["model"][m.id]] if g else self.pick(models)
            days_span = 45 if g else rng.randint(10, 180)
            sig = []
            for k in range(n):
                t = self.cfg.ago(days=rng.uniform(0.5, days_span))
                kind = rng.choices(["page_view", "model_view", "trim_view", "configure_save", "compare", "content_read", "test_drive_slot_view", "price_check"],
                                   [25, 20, 12, 8, 8, 12, 5, 10])[0]
                if g and g["id"] == "J1" and k < 4:
                    kind = "configure_save"
                if g and g.get("competitor_compare") and k == 0:
                    kind, t = "compare", self.cfg.ago(days=6)
                mod = fav if self.chance(0.6) else self.pick(models)
                s = {"customer_id": c["customer_id"], "signal_ts": iso(t), "type": kind, "model_id": mod["model_id"],
                     "trim_id": self.pick(self.ix["trims_by_model"][mod["model_id"]])["trim_id"] if kind in ("trim_view", "configure_save", "price_check") else None,
                     "compare_with": (g["competitor"][m.id] if g and g.get("competitor") else self.pick(mod["competitors"])["make"] + " " + self.pick(mod["competitors"])["model"]) if kind == "compare" else None,
                     "content_title": self.pick(content) if kind == "content_read" else None,
                     "dwell_seconds": rng.randint(15, 420), "channel": rng.choice(["web", "web", "app"]), "market_id": m.id, "ttl_days": 180}
                sig.append(s)
                self.add("DEALER_Signals", s)
            self.ix["signals_by_customer"][c["customer_id"]] = sorted(sig, key=lambda s: s["signal_ts"])
        # leads
        self.ix["leads_by_customer"] = {}
        custs = self.t["DEALER_Customers"]
        fresh_count = 0
        for i in range(cfg.volumes.leads):
            c = self.pick(custs) if i >= len(JOURNEYS) else self.ix["golden"][JOURNEYS[i]["id"]]
            dl = self.ix["dealer"][c["dealer_id"]]
            cond = CONDITIONS[dl["condition"]]
            g = next((j for j in JOURNEYS if j["id"] == c["golden_story"]), None)
            src = "campaign" if g and g.get("conquest") else "service_lane" if g and g.get("owned") else self.pick(sources)
            # the first few leads at the demo store (dealer_idx 0) arrived in the last half hour
            # and are untouched, so the Lead Desk opens on a fresh "New" lead inside its SLA
            # a fresh lead comes from a customer with no journey yet (never a golden storyline
            # already mid-purchase), so working it on the Lead Desk creates the DISCOVER journey
            fresh = dl["node_id"] == m.dealers[0].code and fresh_count < 3 and not c.get("golden_story")
            if fresh:
                fresh_count += 1
                self.ix.setdefault("fresh_lead_customers", set()).add(c["customer_id"])
            recv = self.cfg.ago(minutes=rng.randint(6, 25)) if fresh else self.cfg.ago(days=rng.uniform(0.2, 60))
            breach = (not fresh) and self.chance(cond["lead_sla_breach"])
            resp_min = rng.randint(70, 400) if breach else rng.randint(3, 55)
            responded_at = recv + timedelta(minutes=resp_min)
            first_response = iso(responded_at) if (not fresh and responded_at < self.cfg.now) else None
            model_row = self.ix["models"][g["model"][m.id]] if g else self.pick(models)
            model_name = model_row["name"]
            # status follows the response: an unanswered lead is "new"; an answered one has moved on
            status = "new" if first_response is None else rng.choice(["contacted", "contacted", "appointment_set", "converted", "lost"])
            budget = (c.get("budget") or {}).get("price_max")
            comments_us = [f"Interested in a test drive of the {model_name} this weekend", "What's my trade worth against a new one?",
                           f"Do you have the {model_name} in stock in a dark color?", f"Send me your best lease price on a {model_name}",
                           f"Looking to buy before end of month, budget around ${budget:,}" if budget else "Looking to buy before end of month",
                           "How long is the wait on the EV? Can I charge at home on a regular outlet?", ""]
            comments_de = [f"Probefahrt {model_name} am Wochenende möglich?", "Was ist mein Gebrauchter wert?", f"Ist der {model_name} verfügbar?",
                           f"Bitte Leasingangebot für den {model_name} senden", "Kauf noch diesen Monat geplant", ""]
            lead = {"lead_id": self.nid("LD"), "customer_id": c["customer_id"], "dealer_id": dl["node_id"], "source": src,
                    "vehicle_of_interest_model_id": model_row["model_id"],
                    "comments": self.pick(comments_us[:-1] if fresh else comments_us) if m.id == "us" else self.pick(comments_de[:-1] if fresh else comments_de),
                    "received_at": iso(recv), "first_response_at": first_response,
                    "assigned_user_id": None if status == "new" else c["assigned_advisor_user_id"], "status": status,
                    "campaign_id": (f"CMP-{m.id.upper()}-02" if src == "campaign" else None), "market_id": m.id}
            self.add("DEALER_Leads", lead)
            self.add("DEALER_LeadSLA", {"lead_id": lead["lead_id"], "dealer_id": dl["node_id"], "sla_minutes": 30, "sla_due_at": iso(recv + timedelta(minutes=30)),
                                      "first_response_at": lead["first_response_at"], "breached": breach, "response_minutes": resp_min})
            # The lead's agent Activity feed: the runs that produced what the record already
            # says (intake; the drafted and sent first response; the assignment), with the same
            # tool names the live routes write. RNG-free and keyed off the lead id, so no
            # other generated row moves.
            runs = [("A2", "a2.lead_intake", recv, "observe", f"{src} lead → Lead Desk, SLA 30 min")]
            if first_response:
                runs += [("A3", "a3.draft_first_response", responded_at - timedelta(minutes=1), "recommend",
                          "first-response draft for the BDC to approve"),
                         ("A3", "a3.send_first_response", responded_at, "act_with_approval",
                          f"first response sent after {resp_min} min" + (" (SLA breached)" if breach else ""))]
            if lead["assigned_user_id"]:
                runs.append(("A2", "a2.assign_lead", responded_at + timedelta(minutes=2), "act_with_approval",
                             f"assigned to {lead['assigned_user_id']}"))
            for n, (agent, tool, at, level, why) in enumerate(runs):
                if at > self.cfg.now:
                    continue
                self.add("PLAT_AgentTraces", {
                    "trace_id": f"TR-{lead['lead_id']}-{n}", "ts": iso(at), "agent_id": agent, "tool": tool,
                    "lead_id": lead["lead_id"], "journey_kind": "lead", "dealer_id": dl["node_id"], "market_id": m.id, "outcome": "ok",
                    "autonomy_level": level, "latency_ms": 180 + 40 * n, "routing_decision": why,
                    "citations": [], "rule_id": None, "source_ref": "synthetic:lead_desk (lead lifecycle)"})
            self.ix["leads_by_customer"].setdefault(c["customer_id"], []).append(lead)

    # ── 6. journeys + events + configurations + appointments ───────────────
    def gen_journeys(self):
        m, cfg, rng = self.m, self.cfg, self.rng
        self.ix["journey_by_customer"] = {}
        self.ix["journey"] = {}
        for j in JOURNEYS:
            self._journey(self.ix["golden"][j["id"]], j["stage"], golden=j)
        # cold cohort (customers behind a fresh, unworked lead have no journey yet by design)
        fresh = self.ix.get("fresh_lead_customers", set())
        bulk = [c for c in self.t["DEALER_Customers"] if not c["golden_story"] and c["customer_id"] not in fresh]
        rng.shuffle(bulk)
        cold = bulk[:cfg.volumes.cold_cohort]
        for c in cold:
            self._journey(c, rng.choice(["DISCOVER", "CONFIGURE", "QUALIFY"]), stalled_days=rng.randint(20, 60), cohort=COLD_COHORT_ID)
        rest = bulk[cfg.volumes.cold_cohort:]
        n_active = cfg.volumes.journeys_active - len(JOURNEYS) - cfg.volumes.cold_cohort
        stages, weights = zip(*[(s, w) for s, w in STAGE_MIX.items() if s != "OWN"])
        for c in rest[:n_active]:
            st = rng.choices(stages, weights)[0]
            self._journey(c, st, missing=self.chance(0.05))
        for c in rest[n_active:n_active + cfg.volumes.journeys_closed]:
            self._journey(c, "OWN", closed=True)

    def _journey(self, c, stage, golden=None, stalled_days=None, cohort=None, missing=False, closed=False):
        m, cfg, rng = self.m, self.cfg, self.rng
        jid = f"J-{m.id.upper()}-{golden['id']}" if golden else self.nid("J")
        dealer = self.ix["dealer"][c["dealer_id"]]
        mod = self.ix["models"][golden["model"][m.id]] if golden else self.pick(list(self.ix["models"].values()))
        trims = self.ix["trims_by_model"][mod["model_id"]]
        trim = trims[1] if len(trims) > 1 else trims[0]
        si = STAGES.index(stage)
        # timeline: journey age depends on stage
        start_days = {"DISCOVER": rng.uniform(1, 20), "CONFIGURE": rng.uniform(5, 35), "QUALIFY": rng.uniform(8, 45), "FINANCE": rng.uniform(12, 50),
                      "TRADE_IN": rng.uniform(14, 55), "AGREE": rng.uniform(16, 60), "DELIVER": rng.uniform(20, 70), "OWN": rng.uniform(60, 400)}[stage]
        if golden and golden.get("stalled_days"):
            start_days = 24
        if stalled_days:
            start_days = stalled_days + rng.uniform(5, 30)
        if golden and golden.get("owned") and stage == "OWN":
            # an owner's purchase journey ended when their current car arrived: a 36-month lease with 5
            # months left began 31 months ago, so the journey ran up to then (not last spring)
            cv = c.get("current_vehicle") or {}
            if cv.get("ownership") == "lease" and cv.get("months_remaining"):
                start_days = max(1, 36 - int(cv["months_remaining"])) * 30.4 + 25
            elif cv.get("year"):
                start_days = max(60, (cfg.now.year - int(cv["year"])) * 365 + 120 + 25)
        t0 = cfg.ago(days=start_days)
        last = t0
        events = []
        ctx = {}
        delivered_at = None
        owner = c["assigned_advisor_user_id"]
        bdc = (self.ix["users_by_dealer"][dealer["node_id"]].get("dealer_bdc") or [owner])[0]
        source = "campaign" if golden and golden.get("conquest") else "service_lane" if golden and golden.get("owned") else rng.choice(["web", "web", "third_party_lead", "walk_in", "phone", "campaign", "service_lane"])

        def ev(kind, dt, actor, evidence, **payload):
            nonlocal last
            last = max(last, dt)
            e = {"journey_id": jid, "event_ts": iso(dt), "event_type": kind, "actor": actor, "evidence": evidence, "payload": payload,
                 "correlation_id": hashlib.sha1(f"{jid}{kind}{iso(dt)}".encode()).hexdigest()[:16]}
            events.append(e)
            return e

        ev("journey.created", t0, {"type": "system"}, f"lead source={source}", source=source, stage="DISCOVER")
        ev("lead.received", t0 + timedelta(minutes=2), {"type": "system"}, "DEALER_Leads", source=source)
        ev("stage_changed", t0 + timedelta(minutes=3), {"type": "agent", "agent": "A2"}, "journey.created", **{"from": None, "to": "DISCOVER"})
        # DISCOVER content
        t = t0 + timedelta(hours=rng.uniform(1, 30))
        if golden and golden.get("intake_answered"):
            ev("lifestyle.intake_completed", t, {"type": "agent", "agent": "A1"}, "4 intake questions answered", questions=4, confidence=0.86)
        ev("shortlist.saved", t + timedelta(hours=1), {"type": "agent", "agent": "A1"}, "A1 DiscoveryResult",
           shortlist=[trim["trim_id"]] + ([self.ix["trims_by_model"][golden["alt_model"][m.id]][0]["trim_id"]] if golden and golden.get("alt_model") else []))
        ctx["shortlist"] = True
        if bdc != owner:
            ev("handoff.created", t + timedelta(hours=2), {"type": "user", "user_id": bdc}, "BDC → advisor", from_user=bdc, to_user=owner, stage="DISCOVER")
            ev("handoff.accepted", t + timedelta(hours=3), {"type": "user", "user_id": owner}, "accepted", from_user=bdc, to_user=owner)
        # progress through stages up to `stage`
        cfg_row = None
        for k in range(1, si + 1):
            s = STAGES[k]
            t = last + timedelta(days=rng.uniform(1, 6))
            if s == "CONFIGURE":
                cfg_row = self._configuration(c, jid, mod, trim, t)
                ev("build.saved", t, {"type": "user", "user_id": None, "customer_id": c["customer_id"]}, cfg_row["config_id"], config_id=cfg_row["config_id"], price=cfg_row["price_total"])
                ctx["saved_build"] = True
            if s == "QUALIFY":
                ap = self._appointment(c, jid, dealer, "test_drive", t + timedelta(days=rng.uniform(1, 5)) if not (golden and golden.get("test_drive_in_days")) else cfg.ahead(days=golden["test_drive_in_days"]))
                ev("appointment.booked", t, {"type": "agent", "agent": "A2"}, ap["appointment_id"], appointment_id=ap["appointment_id"], type="test_drive")
                ev("qualification.captured", t + timedelta(hours=2), {"type": "user", "user_id": owner}, "budget+timeline recorded",
                   budget=c["budget"]["price_max"], timeline_days=rng.choice([14, 30, 60]))
                ctx["budget"] = ctx["timeline"] = True
                if golden and golden.get("objections"):
                    for o in golden["objections"]:
                        ev("objection.detected", t + timedelta(hours=3), {"type": "agent", "agent": "A3"}, "live transcript", objection_type=o)
                        ev("response_used", t + timedelta(hours=3, minutes=2), {"type": "user", "user_id": owner}, "A3 strategy", objection_type=o)
                ev("vehicle_presented", t + timedelta(hours=3, minutes=10), {"type": "user", "user_id": owner}, "Advisor Present button", trim_id=trim["trim_id"])
            if s == "FINANCE":
                ev("prequal.requested", t, {"type": "user", "user_id": None, "customer_id": c["customer_id"]}, "My Garage prequal link")
                ev("prequal.decided", t + timedelta(hours=1), {"type": "system", "connector": "fni"}, "F&I sandbox", tier=self.pick(m.credit_tiers)["tier"])
                ctx["finance_offer"] = True
            if s == "TRADE_IN":
                ev("trade.valued", t, {"type": "agent", "agent": "A4"}, "valuation connector", equity=(golden["trade"]["equity"] if golden and golden.get("trade") else round(rng.uniform(-4000, 6000))))
                if not (golden and golden.get("demo_missing_context")):
                    ev("trade.accepted", t + timedelta(hours=5), {"type": "user", "user_id": None, "customer_id": c["customer_id"]}, "My Garage")
                    ctx["trade_condition"] = True
            if s == "AGREE":
                ev("deal.structured", t, {"type": "agent", "agent": "A3"}, "structure_deal", scenarios=3)
                if golden and golden.get("handoff_chain"):
                    fni = (self.ix["users_by_dealer"][dealer["node_id"]].get("dealer_fni") or [owner])[0]
                    ev("handoff.created", t + timedelta(hours=1), {"type": "user", "user_id": owner}, "advisor → F&I", from_user=owner, to_user=fni, stage="AGREE")
                    ev("handoff.accepted", t + timedelta(hours=1, minutes=20), {"type": "user", "user_id": fni}, "accepted", from_user=owner, to_user=fni)
                ev("deal.pushed", t + timedelta(hours=2), {"type": "system", "connector": "dms"}, "push_deal_to_dms")
                ev("document.shared", t + timedelta(hours=2, minutes=5), {"type": "user", "user_id": owner}, "deal worksheet → My Garage", types=golden.get("documents", ["deal_worksheet"]) if golden else ["deal_worksheet"])
                ctx["deal"] = True
            if s == "DELIVER":
                ev("deal.booked", t, {"type": "system", "connector": "dms"}, "DMS booked")
                slot = cfg.ahead(days=golden["delivery_in_days"]) if golden and golden.get("delivery_in_days") else t + timedelta(days=rng.uniform(2, 9))
                ap = self._appointment(c, jid, dealer, "delivery", slot)
                ev("appointment.booked", t + timedelta(minutes=30), {"type": "user", "user_id": owner}, ap["appointment_id"], appointment_id=ap["appointment_id"], type="delivery")
                if golden and golden.get("handoff_chain"):
                    dlv = (self.ix["users_by_dealer"][dealer["node_id"]].get("dealer_delivery") or [owner])[0]
                    ev("handoff.created", t + timedelta(hours=1), {"type": "user", "user_id": owner}, "F&I → delivery", from_user=owner, to_user=dlv, stage="DELIVER")
            if s == "OWN":
                ev("vehicle.delivered", t, {"type": "user", "user_id": owner}, "mark_delivered", vin=None)
                ev("survey.sent", t + timedelta(days=1), {"type": "agent", "agent": "A2"}, "OutreachProvider email")
                ctx["delivered"] = True
                delivered_at = t                      # the VIN master's delivered_at is THIS moment
            ev("stage_changed", t + timedelta(minutes=5), {"type": "agent", "agent": "A2"}, "validate_handoff ok", **{"from": STAGES[k - 1], "to": s})
        # nurture history for golden
        if golden and golden.get("nurture_touches"):
            for n in range(golden["nurture_touches"]):
                ev("message.outbound", t0 + timedelta(days=3 + n * 5), {"type": "agent", "agent": "A2"}, "nurture plan", channel=rng.choice(["email", "sms"]), intent=["welcome_shortlist", "incentive_expiring", "test_drive_nudge"][n % 3])
        if golden and golden.get("inbound_questions"):
            ev("message.inbound", cfg.ago(days=2, hours=3), {"type": "user", "customer_id": c["customer_id"]}, "/shop/chat", channel="chat", intent="payment_question")
            ev("message.outbound", cfg.ago(days=2, hours=2, minutes=55), {"type": "agent", "agent": "A2"}, "answer_customer_question (cited)", channel="chat", citations=3)
        if golden and golden.get("credit_question"):
            ev("message.inbound", cfg.ago(days=1, hours=6), {"type": "user", "customer_id": c["customer_id"]}, "/shop/chat", channel="chat", intent="credit_approval_question")
            ev("handoff.to_human", cfg.ago(days=1, hours=5, minutes=58), {"type": "agent", "agent": "A2"}, "policy: credit decisions → human", reason="credit_approval")
            ev("consent.changed", cfg.ago(days=golden["sms_opt_out_days_ago"]), {"type": "user", "customer_id": c["customer_id"]}, "My Garage preferences", sms_opt_in=False)
        if golden and golden.get("competitor_compare"):
            ev("signal.recorded", cfg.ago(days=6), {"type": "system"}, "CDP compare event", compare_with=self.pick(mod["competitors"])["model"])
            ev("journey.stalled", cfg.ago(days=2), {"type": "system", "rule": "no_event_gt_7d"}, "scheduler", idle_days=7)
            ev("intent.shift_detected", cfg.ago(days=2, minutes=-5), {"type": "agent", "agent": "A2"}, "detect_intent_shift", shift="competitor_considered")
            ev("task.created", cfg.ago(days=2, minutes=-8), {"type": "agent", "agent": "A2"}, "dealer follow-up", assignee=owner)
        if stalled_days and not golden:
            last = cfg.ago(days=stalled_days)
        if golden and golden.get("stalled_days"):
            last = cfg.ago(days=golden["stalled_days"]) if not golden.get("competitor_compare") else last
        if missing:
            ctx.pop(rng.choice(["budget", "timeline", "saved_build"]), None)
        if golden and golden.get("demo_missing_context"):
            for k_ in golden["demo_missing_context"]:
                ctx.pop(k_, None)
        req = REQUIRED_CONTEXT[stage]
        missing_ctx = [k for k in req if not ctx.get(k)] + (["trade_condition"] if golden and golden.get("demo_missing_context") else [])
        stalled = bool(stalled_days or (golden and golden.get("stalled_days")))
        row = {"journey_id": jid, "customer_id": c["customer_id"], "dealer_id": dealer["node_id"], "district_id": dealer["district_id"], "market_id": m.id,
               "stage": stage, "previous_stage": STAGES[si - 1] if si > 0 else None, "status": "closed_won" if closed else "active",
               # attribution spread over the campaign calendar (conquest / EV-adoption campaigns feed
               # campaign-source journeys; retention / loyalty campaigns feed service-lane ones), so
               # the ROI table ranks six campaigns instead of two rows and four zeros
               "source": source, "campaign_id": (f"CMP-{m.id.upper()}-{rng.choices(['01', '02', '05'], [30, 50, 20])[0]}" if source == "campaign"
                                                 else (f"CMP-{m.id.upper()}-{rng.choices(['03', '04', '06'], [25, 50, 25])[0]}" if source == "service_lane" else None)),
               "owner_user_id": owner, "team": "sales", "primary_model_id": mod["model_id"], "primary_trim_id": trim["trim_id"],
               "account_id": c["account_id"], "created_at": iso(t0), "updated_at": iso(last), "last_event_at": iso(last),
               "idle_days": round((cfg.now - last).total_seconds() / 86400, 1), "stalled": stalled or (cfg.now - last).days > 7,
               "context": ctx, "missing_context": missing_ctx, "demo_missing_context": bool(missing or (golden and golden.get("demo_missing_context"))),
               "golden_story": golden["id"] if golden else cohort, "nurture_active": not closed and not (golden and golden.get("credit_question")),
               "config_id": cfg_row["config_id"] if cfg_row else None, "summary": golden["summary"] if golden else None,
               # one delivery moment for the journey's events, its vehicle row and My Garage ("193d ago" next to
               # "took delivery Nov 4" came from two independent draws)
               **({"delivered_at": iso(delivered_at)} if delivered_at else {})}
        self.add("DEALER_Journeys", row)
        self.ix["journey"][jid] = row
        self.ix["journey_by_customer"][c["customer_id"]] = row
        for e in sorted(events, key=lambda e: e["event_ts"]):
            self.add("DEALER_JourneyEvents", e)
        # handoffs table from events
        for e in events:
            if e["event_type"] == "handoff.created":
                self.add("DEALER_Handoffs", {"handoff_id": self.nid("HO"), "journey_id": jid, "from_user": e["payload"]["from_user"], "to_user": e["payload"]["to_user"],
                                           "stage": e["payload"]["stage"], "note": e["evidence"], "created_at": e["event_ts"],
                                           "accepted_at": next((x["event_ts"] for x in events if x["event_type"] == "handoff.accepted" and x["payload"]["to_user"] == e["payload"]["to_user"]), None)})
        return row

    def _configuration(self, c, jid, mod, trim, t):
        m = self.m
        pkgs = [p for p in self.t["OEM_Catalog"] if p.get("kind") == "package" and p["model_id"] == mod["model_id"]]
        chosen = self.rng.sample(pkgs, min(len(pkgs), self.rng.randint(0, 2)))
        inc = [i for i in A.incentives(m.id) if i["status"] == "active" and (trim["powertrain"] in i["powertrains"]) and i.get("scope") in ("US", "DE", c["region_code"])]
        # Only point-of-sale reductions come off the vehicle price — the same rule the runtime
        # pricer applies (app/services/agents/discovery.py `_reduces_vehicle_price`): a loan-interest
        # deduction or a charger rebate is real money but not cash off the car, and an APR program
        # changes the rate, not the price. Subtracting them overstated the deal (a $4,000 "incentive"
        # that no line item could explain).
        inc_total = sum((i.get("amount_usd") or i.get("amount_eur") or 0) for i in inc
                        if (i.get("amount_usd") or i.get("amount_eur")) and i["tier"] in ("federal", "state", "oem")
                        and _reduces_vehicle_price(i))
        inc_total = min(inc_total, 6000 if m.id == "de" else 4000)
        price = trim["list_price"] + sum(p["list_price"] for p in chosen)
        tax = 0 if m.id == "de" else round(price * (A.fnum(A.us_state_rules().get(c["region_code"], {}).get("general_sales_tax_pct"), 6) or 6) / 100)
        row = {"config_id": self.nid("CFG"), "journey_id": jid, "customer_id": c["customer_id"], "model_id": mod["model_id"], "trim_id": trim["trim_id"],
               "packages": [p["trim_id"] for p in chosen], "color": self.pick(["Glacier White", "Obsidian Black", "Graphite", "Ember Red"]),
               "price_vehicle": price, "price_tax_est": tax, "incentives_applied": [i["incentive_id"] for i in inc[:3]], "incentives_total": inc_total,
               "price_total": price + tax - inc_total, "est_monthly_buy": round((price + tax - inc_total - c["budget"]["down"]) * 0.0195),
               "est_monthly_lease": round(price * 0.0128), "currency": m.currency, "saved_at": iso(t), "share_token": hashlib.sha1(jid.encode()).hexdigest()[:10],
               "source_ref": trim["source_ref"]}
        self.add("PLAT_Configurations", row)
        return row

    def _appointment(self, c, jid, dealer, kind, dt):
        row = {"appointment_id": self.nid("AP"), "journey_id": jid, "customer_id": c["customer_id"], "dealer_id": dealer["node_id"], "type": kind,
               "slot_start": iso(dt), "slot_end": iso(dt + timedelta(minutes=60 if kind == "test_drive" else 90)),
               "status": "scheduled" if dt > self.cfg.now else "completed", "created_at": iso(min(dt, self.cfg.now) - timedelta(days=1)),
               "advisor_user_id": c["assigned_advisor_user_id"]}
        self.add("DEALER_Appointments", row)
        return row

    def gen_calendar_slots(self):
        """Open dealer slots for the next 14 days (so book_test_drive has inventory)."""
        for dl in self.m.dealers:
            for d in range(1, 15):
                base = self.cfg.ahead(days=d).replace(hour=9, minute=0, second=0)
                for h in (0, 2, 4, 6, 8):
                    if self.chance(0.6):
                        self.add("DEALER_Appointments", {"appointment_id": self.nid("AP"), "kind": "open_slot", "journey_id": None, "customer_id": None, "dealer_id": dl.code,
                                                       "type": "test_drive", "slot_start": iso(base + timedelta(hours=h)), "slot_end": iso(base + timedelta(hours=h + 1)),
                                                       "status": "open", "created_at": iso(self.cfg.now), "advisor_user_id": None})


# ── Phase registration (U4 phase-registry refactor) ─────────────────────────
# Foundation + the sales spine register here instead of being edited into the
# orchestrator's PHASES literal. Orders preserve the exact pre-refactor sequence:
#   0 foundation; 20..25 the six spine phases (catalog, inventory, customers,
#   signals/leads, journeys, calendar slots). commerce.gen_commerce registers at
#   26 (in commerce.py). See phase_registry.py for the band convention.
from . import phase_registry as _pr  # noqa: E402  (import after class definition)

_pr.register(_pr.BAND_FOUNDATION, G.gen_org_users)   # 0
_pr.register(_pr.BAND_PLATFORM_LATE - 1, G.gen_service_staff)  # 89: appends Fixed Ops users
_pr.register(_pr.BAND_COMMERCE + 0, G.gen_catalog)   # 20
_pr.register(_pr.BAND_COMMERCE + 1, G.gen_inventory)  # 21
_pr.register(_pr.BAND_COMMERCE + 2, G.gen_customers)  # 22
_pr.register(_pr.BAND_COMMERCE + 3, G.gen_signals_leads)  # 23
_pr.register(_pr.BAND_COMMERCE + 4, G.gen_journeys)  # 24
_pr.register(_pr.BAND_COMMERCE + 5, G.gen_calendar_slots)  # 25
