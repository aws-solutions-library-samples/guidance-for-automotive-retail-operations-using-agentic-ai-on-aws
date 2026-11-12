"""Loaders for open-data snapshots and curated files in sample-data/open/."""
from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path

from .config import OPEN


def _csv(p: Path) -> list[dict]:
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _f(v, default=None):
    try:
        return float(v) if v not in (None, "", "None") else default
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=None)
def lineup() -> dict:
    return _json(OPEN / "curated" / "lineup.json")


@lru_cache(maxsize=None)
def incentives(market: str) -> list[dict]:
    return _json(OPEN / "curated" / market / "incentives.json")["incentives"]


@lru_cache(maxsize=None)
def us_state_rules() -> dict[str, dict]:
    return {r["state"]: r for r in _csv(OPEN / "curated" / "us" / "state_rules.csv")}


@lru_cache(maxsize=None)
def de_tax_rules() -> dict:
    return _json(OPEN / "curated" / "de" / "tax_rules.json")


@lru_cache(maxsize=None)
def de_ev_range() -> dict:
    return {s["segment"]: s for s in _json(OPEN / "curated" / "de" / "ev_range_anchors.json")["segments"]}


@lru_cache(maxsize=None)
def epa_segments() -> dict[tuple[str, str], dict]:
    return {(r["epa_class"], r["powertrain"]): r for r in _csv(OPEN / "us" / "epa_fueleconomy.csv")}


@lru_cache(maxsize=None)
def epa_models() -> list[dict]:
    return _csv(OPEN / "us" / "epa_models_2024plus.csv")


# Generic stand-ins for real automaker brands / nameplates. Open-data lookups (EPA, EEA) keep
# using the real names internally; anything WRITTEN to the synthetic output goes through
# display_vehicle() so no real brand or model name appears in sample data.
DISPLAY_MAKE = {
    "BMW": "Brandt", "Chevrolet": "Brightwater", "Cupra": "Cordero", "Ford": "Larkspur", "Honda": "Halden",
    "Hyundai": "Solari", "Kia": "Verdant", "Nissan": "Kitano", "Opel": "Lindau", "Peugeot": "Lyrien",
    "Renault": "Aurel", "Skoda": "Vltava", "Tesla": "Ampera", "Toyota": "Kestrel", "Volkswagen": "Rheinmark",
}
DISPLAY_MODEL = {
    "3er Touring": "Kombi T3", "X1": "Terra S", "i4": "Arc E4",
    "Colorado": "Mesa Truck", "Equinox EV": "Tide EV", "Traverse": "Harbor", "Born": "Vento",
    "Escape": "Fenland", "Explorer": "Trailmark", "Mustang Mach-E": "Stallion E", "Ranger": "Scout Pick",
    "CR-V": "Alder", "Pilot": "Summit 3R",
    "IONIQ 5": "Lumen 5", "IONIQ 6": "Lumen 6", "Kona Electric": "Cove Electric", "Palisade": "Ridgeway", "Tucson": "Dune",
    "EV6": "Pulse 6", "Sportage": "Sprint X", "Telluride": "Ascent 8", "Pathfinder": "Wayfinder",
    "Astra": "Lyra", "Insignia": "Signum", "E-208": "E-Pico", "5 E-Tech": "Petit E",
    "Enyaq": "Elva", "Karoq": "Karst", "Kodiaq": "Korvo", "Octavia": "Ottavo", "Superb Combi": "Grand Combi",
    "Model 3": "Spark S", "Model Y": "Spark X",
    "C-HR": "Kite", "Corolla": "Corvane", "Highlander": "Highmark", "RAV4": "Trek 4", "Tacoma": "Canyon Pick",
    "Golf": "Linden", "ID.3": "Eco 3", "ID.4": "Eco 4", "ID.7": "Eco 7", "Passat Variant": "Linden Wagon",
    "T-Roc": "Tamar", "Tiguan": "Tarn",
}
def display_vehicle(make: str, model: str) -> tuple[str, str]:
    """Generic (make, model) for output; names not in the maps (already fictional) pass through."""
    return DISPLAY_MAKE.get(make, make), DISPLAY_MODEL.get(model, model)
def epa_model(make: str, model: str) -> dict | None:
    m0 = model.lower().split()[0]
    hits = [r for r in epa_models() if r["make"].lower() == make.lower() and m0 in r["model"].lower()]
    if not hits:
        return None
    # prefer the row with most variants (volume trim)
    return max(hits, key=lambda r: int(r["n_variants"] or 0))


@lru_cache(maxsize=None)
def eea_de() -> list[dict]:
    return _csv(OPEN / "de" / "eea_co2_de.csv")


def eea_rows(model_key: str) -> list[dict]:
    k = model_key.lower()
    return [r for r in eea_de() if k in r["model"].lower()]


@lru_cache(maxsize=None)
def kba() -> list[dict]:
    return _csv(OPEN / "de" / "kba_fz10.csv")


def kba_row(model_key: str) -> dict | None:
    k = model_key.lower()
    hits = [r for r in kba() if k in r["model"].lower()]
    return max(hits, key=lambda r: _f(r["insgesamt_jan_dezember_2025"], 0) or 0) if hits else None


@lru_cache(maxsize=None)
def census_places() -> list[dict]:
    return _csv(OPEN / "us" / "census_gazetteer_places.csv")


@lru_cache(maxsize=None)
def eia_electricity() -> dict[str, dict]:
    return {r["state"]: r for r in _csv(OPEN / "us" / "eia_electricity_state.csv")}


@lru_cache(maxsize=None)
def eia_gas() -> float:
    for r in _csv(OPEN / "us" / "eia_gasoline_weekly.csv"):
        if "All Grades All Formulations" in r["series"]:
            return float(r["usd_per_gallon"])
    return 3.60


@lru_cache(maxsize=None)
def afdc_charging() -> dict[str, dict]:
    return {r["state"]: r for r in _csv(OPEN / "us" / "afdc_charging_ports.csv")}


@lru_cache(maxsize=None)
def afdc_ev_reg() -> dict[str, dict]:
    return {r["state"]: r for r in _csv(OPEN / "us" / "afdc_ev_registrations.csv")}


@lru_cache(maxsize=None)
def afdc_fleet() -> dict[str, dict]:
    return {r["state"]: r for r in _csv(OPEN / "us" / "afdc_vehicle_registration.csv")}


@lru_cache(maxsize=None)
def bnetza() -> dict[str, dict]:
    return {r["bundesland"]: r for r in _csv(OPEN / "de" / "bnetza_ladesaeulen.csv")}


@lru_cache(maxsize=None)
def eurostat_elec(geo: str = "DE") -> float:
    for r in _csv(OPEN / "eu" / "eurostat_electricity.csv"):
        if r["geo"] == geo:
            return float(r["eur_per_kwh"])
    return 0.35


@lru_cache(maxsize=None)
def oil_bulletin(country: str = "Germany") -> dict:
    for r in _csv(OPEN / "eu" / "ec_oil_bulletin.csv"):
        if r["country"] == country:
            return {"euro95": _f(r["euro95_eur_per_l"], 1.8), "diesel": _f(r["diesel_eur_per_l"], 1.7), "date": r["date"]}
    return {"euro95": 1.8, "diesel": 1.7, "date": ""}


@lru_cache(maxsize=None)
def irs_30d() -> list[dict]:
    return _csv(OPEN / "us" / "irs_30d_list.csv")


def fnum(v, default=None):
    return _f(v, default)
