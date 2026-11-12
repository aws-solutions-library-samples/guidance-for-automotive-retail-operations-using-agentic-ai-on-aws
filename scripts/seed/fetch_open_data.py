#!/usr/bin/env python3
"""
Fetch and snapshot the open datasets used to build the Retail Ops pilot seed packs
(US market `us` = Meridian Motors, Europe market `de` = Nordwerk Automobile).

Two-step, reproducible:

  1. fetch   - download raw files into sample-data/open/raw/<source_id>/ (git-ignored)
  2. derive  - reduce raw files into small, committed snapshots under
               sample-data/open/<market>/<source_id>.{csv,json}

Every derived row is traceable: sample-data/open/manifest.json records URL,
license, retrieval timestamp, sha256, size, and row counts per source, and
SOURCES.md is regenerated from the same manifest.

Usage:
    python3 scripts/seed/fetch_open_data.py                 # fetch + derive everything
    python3 scripts/seed/fetch_open_data.py --only kba_fz10 eea_co2_de
    python3 scripts/seed/fetch_open_data.py --derive-only   # reuse existing raw files
    python3 scripts/seed/fetch_open_data.py --list

Spec reference: docs/specs/intelligent-sales-service-orchestration.md §2.5.1
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import statistics
import sys
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

ROOT = Path(__file__).resolve().parents[2]
OPEN = ROOT / "sample-data" / "open"
RAW = OPEN / "raw"
MANIFEST = OPEN / "manifest.json"
UA = {"User-Agent": "app-seed/0.1 (+automotive-sales-advisor pilot; open-data fetch)"}
TIMEOUT = 120


# ─── Source registry ─────────────────────────────────────────────────────────

@dataclass
class Source:
    id: str
    market: str                     # us | de | eu | shared
    name: str
    publisher: str
    urls: dict[str, str]            # raw filename -> URL  (or "sql:..." pseudo-URL)
    license: str
    fields_used: str
    derive: Callable[["Source", dict], list[dict]] | None = None
    notes: str = ""
    status: dict = field(default_factory=dict)

    @property
    def raw_dir(self) -> Path:
        return RAW / self.id

    @property
    def out_dir(self) -> Path:
        return OPEN / self.market


SOURCES: list[Source] = []


def register(src: Source) -> Source:
    SOURCES.append(src)
    return src


# ─── Helpers ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    headers = dict(UA)
    if dest.suffix == ".json":
        headers["Accept"] = "application/json"
    with requests.get(url, headers=headers, timeout=TIMEOUT, stream=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return {
        "url": url,
        "bytes": dest.stat().st_size,
        "sha256": sha256(dest),
        "seconds": round(time.time() - t0, 1),
    }


def discodata_sql(query: str, dest: Path, page_size: int = 1000, max_pages: int = 50) -> dict:
    """Run a SQL query against EEA Discodata with pagination, save JSON rows."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    t0 = time.time()
    for page in range(1, max_pages + 1):
        params = {"query": query, "p": page, "nrOfHits": page_size}
        r = requests.get("https://discodata.eea.europa.eu/sql", params=params, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        batch = r.json().get("results", [])
        rows.extend(batch)
        if len(batch) < page_size:
            break
    dest.write_text(json.dumps(rows, ensure_ascii=False))
    return {"url": "sql:" + query, "bytes": dest.stat().st_size, "sha256": sha256(dest),
            "seconds": round(time.time() - t0, 1), "rows": len(rows)}


def write_csv(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return 0
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_json(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    return len(obj) if isinstance(obj, (list, dict)) else 1


def num(v):
    """Coerce spreadsheet cell to float, else None ('-' means no value in KBA)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(".", "").replace(",", ".")
    if s in ("", "-", "–", "x", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


# ─── US sources ──────────────────────────────────────────────────────────────

def derive_fueleconomy(src: Source, raw: dict) -> list[dict]:
    """EPA fueleconomy.gov vehicle database -> segment anchors + recent model list."""
    import pandas as pd
    z = zipfile.ZipFile(src.raw_dir / "vehicles.csv.zip")
    name = [n for n in z.namelist() if n.endswith(".csv")][0]
    df = pd.read_csv(z.open(name), low_memory=False)
    df = df[df["year"] >= 2023].copy()

    def powertrain(row):
        atv = str(row.get("atvType", "") or "")
        if atv == "EV":
            return "EV"
        if atv == "Plug-in Hybrid":
            return "PHEV"
        if atv == "Hybrid":
            return "HEV"
        if atv == "FFV":
            return "ICE"
        return "ICE"

    df["powertrain"] = df.apply(powertrain, axis=1)
    keep = ["year", "make", "model", "VClass", "powertrain", "fuelType1", "drive", "trany",
            "comb08", "city08", "highway08", "range", "rangeA", "combE", "co2TailpipeGpm",
            "displ", "cylinders", "youSaveSpend", "fuelCost08"]
    keep = [k for k in keep if k in df.columns]
    models = df[keep].copy()
    models = models.rename(columns={"VClass": "epa_class", "comb08": "comb_mpg_or_mpge",
                                    "city08": "city_mpg", "highway08": "hwy_mpg",
                                    "range": "ev_range_mi", "combE": "kwh_per_100mi",
                                    "co2TailpipeGpm": "co2_g_per_mi", "fuelCost08": "annual_fuel_cost_usd"})
    models["source_ref"] = "epa_fueleconomy:vehicles.csv:" + models["year"].astype(str) + ":" + models["make"] + ":" + models["model"]
    # Compact competitor list: one row per make/model/powertrain for 2024+, medians across trims/drivetrains.
    m24 = models[models["year"] >= 2024]
    agg = (m24.groupby(["make", "model", "powertrain"], as_index=False)
              .agg(latest_year=("year", "max"), n_variants=("year", "size"), epa_class=("epa_class", "first"),
                   comb_mpg_or_mpge=("comb_mpg_or_mpge", "median"), ev_range_mi=("ev_range_mi", "median"),
                   kwh_per_100mi=("kwh_per_100mi", "median"), co2_g_per_mi=("co2_g_per_mi", "median"),
                   annual_fuel_cost_usd=("annual_fuel_cost_usd", "median")))
    agg["source_ref"] = "epa_fueleconomy:vehicles.csv:agg:" + agg["make"] + ":" + agg["model"] + ":" + agg["powertrain"]
    write_csv(src.out_dir / "epa_models_2024plus.csv", agg.sort_values(["make", "model"]).to_dict("records"))
    (src.out_dir / "epa_models_2023plus.csv").unlink(missing_ok=True)

    # Segment anchors: median efficiency/range per EPA class x powertrain (2024+)
    recent = models[models["year"] >= 2024]
    anchors = []
    for (cls, pt), g in recent.groupby(["epa_class", "powertrain"]):
        anchors.append({
            "epa_class": cls, "powertrain": pt, "n_models": len(g),
            "median_comb_mpg_or_mpge": median(g["comb_mpg_or_mpge"].tolist()),
            "median_ev_range_mi": median(g["ev_range_mi"].replace(0, None).tolist()) if pt in ("EV", "PHEV") else None,
            "median_kwh_per_100mi": median(g["kwh_per_100mi"].replace(0, None).tolist()) if pt == "EV" else None,
            "median_annual_fuel_cost_usd": median(g["annual_fuel_cost_usd"].tolist()),
            "top_makes": ", ".join(g["make"].value_counts().head(5).index.tolist()),
            "source_ref": "epa_fueleconomy:vehicles.csv:agg:2024plus",
        })
    return anchors


register(Source(
    id="epa_fueleconomy", market="us",
    name="EPA / DOE fueleconomy.gov vehicle database",
    publisher="U.S. EPA & DOE",
    urls={"vehicles.csv.zip": "https://www.fueleconomy.gov/feg/epadata/vehicles.csv.zip"},
    license="U.S. Government work, public domain",
    fields_used="year, make, model, VClass, atvType, comb08, range, combE, co2TailpipeGpm, fuelCost08",
    derive=derive_fueleconomy,
    notes="Segment anchors (median MPG/MPGe, EV range, fuel cost) per EPA class x powertrain; 2023+ model list for competitor sets.",
))


def derive_afdc_ev_reg(src: Source, raw: dict) -> list[dict]:
    """AFDC EV registration counts by state (xlsx) -> csv."""
    import openpyxl
    p = src.raw_dir / "ev_registration_counts_by_state.xlsx"
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        # find header row containing 'State'
        hdr_i = next((i for i, r in enumerate(rows) if r and any(str(c).strip().lower() == "state" for c in r if c)), None)
        if hdr_i is None:
            continue
        hdr = [str(c).strip() if c is not None else "" for c in rows[hdr_i]]
        si = hdr.index("State")
        for r in rows[hdr_i + 1:]:
            if not r or r[si] is None or str(r[si]).strip() == "" or str(r[si]).lower().startswith(("total", "note", "source")):
                continue
            rec = {"state": str(r[si]).strip()}
            for j, h in enumerate(hdr):
                if j != si and h:
                    v = r[j]
                    rec[re.sub(r"\W+", "_", h.lower()).strip("_")] = v if isinstance(v, (int, float)) else num(v)
            rec["sheet"] = ws.title
            rec["source_ref"] = f"afdc_ev_registrations:{ws.title}:{rec['state']}"
            out.append(rec)
        break  # first sheet with a State column is the data sheet
    return out


register(Source(
    id="afdc_ev_registrations", market="us",
    name="AFDC Electric Vehicle Registrations by State",
    publisher="U.S. DOE Alternative Fuels Data Center",
    urls={"ev_registration_counts_by_state.xlsx":
          "https://afdc.energy.gov/files/u/data/data_source/10962/10962-ev-registration-counts-by-state_9-06-24.xlsx"},
    license="U.S. Government work, public domain (data derived from Experian; counts rounded)",
    fields_used="State, EV registration count (latest year)",
    derive=derive_afdc_ev_reg,
    notes="Used as the US 'market registrations' proxy for EV share by state (PMA proxy for A6 and EV-readiness view).",
))


def derive_afdc_vehicle_reg(src: Source, raw: dict) -> list[dict]:
    """AFDC light-duty registrations by state and fuel type (HTML table) -> csv."""
    from bs4 import BeautifulSoup
    html = (src.raw_dir / "vehicle_registration.html").read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="vehicle_registration")
    if table is None:
        return []
    headers = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    keys = [re.sub(r"\W+", "_", h.lower()).strip("_") for h in headers]
    out = []
    for tr in table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) != len(keys) or not cells[0]:
            continue
        rec = {"state": cells[0]}
        for k, v in zip(keys[1:], cells[1:]):
            rec[k] = num(v.replace(",", "")) if v else None
        rec["source_ref"] = f"afdc_vehicle_registration:{rec['state']}"
        out.append(rec)
    return out


register(Source(
    id="afdc_vehicle_registration", market="us",
    name="AFDC Vehicle Registration Counts by State (all fuel types)",
    publisher="U.S. DOE Alternative Fuels Data Center",
    urls={"vehicle_registration.html": "https://afdc.energy.gov/vehicle-registration"},
    license="U.S. Government work, public domain",
    fields_used="State x fuel type (EV, PHEV, HEV, Gasoline, Diesel, ...) registration counts",
    derive=derive_afdc_vehicle_reg,
    notes="Total light-duty fleet by state; basis for state market size and powertrain mix in RDR proxies.",
))


def derive_afdc_charging(src: Source, raw: dict) -> list[dict]:
    """AFDC 'EV Charging Ports by State' widget -> embedded JSON array of {name, public, private, total}."""
    h = (src.raw_dir / "ev_charging_ports_by_state.html").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'"data"\s*:\s*(\[\{"name".*?\}\])', h, re.S)
    if not m:
        return []
    rows = json.loads(m.group(1))
    return [{"state": r["name"], "public_ports": r.get("public"), "private_ports": r.get("private"),
             "total_ports": r.get("total"), "source_ref": f"afdc_charging_ports:{r['name']}"} for r in rows]


register(Source(
    id="afdc_charging_ports", market="us",
    name="AFDC Electric Vehicle Charging Ports by State",
    publisher="U.S. DOE Alternative Fuels Data Center (Station Locator)",
    urls={"ev_charging_ports_by_state.html": "https://afdc.energy.gov/data/widgets/10366"},
    license="U.S. Government work, public domain",
    fields_used="State, public ports, private ports, total ports",
    derive=derive_afdc_charging,
    notes="Charging infrastructure per state for EV readiness and TCO messaging. Replaces the NREL Station Locator API, which is not reachable from the build network (developer.nrel.gov DNS blocked); the widget is a snapshot of the same data.",
))


def derive_eia_gasoline(src: Source, raw: dict) -> list[dict]:
    """EIA weekly retail gasoline prices (xls) -> latest price by area."""
    import pandas as pd
    p = src.raw_dir / "PET_PRI_GND_DCUS_NUS_W.xls"
    xl = pd.ExcelFile(p)
    out = []
    for sheet in xl.sheet_names:
        if not sheet.lower().startswith("data"):
            continue
        df = xl.parse(sheet, header=2)
        date_col = df.columns[0]
        df = df.dropna(subset=[date_col])
        latest = df.iloc[-1]
        for col in df.columns[1:]:
            if pd.isna(latest[col]):
                continue
            out.append({
                "series": str(col).strip(),
                "sheet": sheet,
                "date": str(pd.to_datetime(latest[date_col]).date()),
                "usd_per_gallon": round(float(latest[col]), 3),
                "source_ref": f"eia_gasoline_weekly:{sheet}:{col}",
            })
    return out


register(Source(
    id="eia_gasoline_weekly", market="us",
    name="EIA Weekly Retail Gasoline and Diesel Prices",
    publisher="U.S. Energy Information Administration",
    urls={"PET_PRI_GND_DCUS_NUS_W.xls": "https://www.eia.gov/dnav/pet/xls/PET_PRI_GND_DCUS_NUS_W.xls"},
    license="U.S. Government work, public domain",
    fields_used="Latest weekly regular/premium/diesel price, U.S. and PADD regions",
    derive=derive_eia_gasoline,
    notes="Default fuel_price for US TCO (MarketConfig.fuel_price_default) and regional overrides.",
))


US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}


def derive_eia_electricity(src: Source, raw: dict) -> list[dict]:
    """EIA Electric Power Monthly Table 5.6.A -> latest-month residential price by state (cents/kWh)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup((src.raw_dir / "epm_table_5_6_a.html").read_text(encoding="utf-8", errors="replace"), "lxml")
    label = next((str(x) for x in soup.find_all(string=lambda x: x and "by State," in x)), "")
    period = re.sub(r".*by State,\s*", "", label).split(" and ")[0].strip() if label else ""
    table = next(t for t in soup.find_all("table") if "Alabama" in t.get_text())
    out = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not cells or cells[0] not in US_STATES:
            continue
        try:
            cur, prev = float(cells[1].replace(",", "")), float(cells[2].replace(",", ""))
        except (ValueError, IndexError):
            continue
        st = US_STATES[cells[0]]
        out.append({"state": st, "state_name": cells[0], "period": period,
                    "residential_cents_per_kwh": cur, "residential_cents_per_kwh_prior_year": prev,
                    "usd_per_kwh": round(cur / 100, 4),
                    "source_ref": f"eia_electricity_state:{period}:{st}"})
    return out


register(Source(
    id="eia_electricity_state", market="us",
    name="EIA Electric Power Monthly Table 5.6.A - Average Price of Electricity to Ultimate Customers by End-Use Sector, by State",
    publisher="U.S. Energy Information Administration",
    urls={"epm_table_5_6_a.html": "https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a"},
    license="U.S. Government work, public domain",
    fields_used="State, residential price latest month and same month prior year (cents/kWh)",
    derive=derive_eia_electricity,
    notes="Per-state energy_price for EV TCO (OEM_StateRules.energy_price). Replaces the legacy avgprice_annual.xlsx, which stops at 2020.",
))


def derive_irs_30d(src: Source, raw: dict) -> list[dict]:
    """fueleconomy.gov tax REST service -> one row per eligible vehicle (2023-and-after 30D program)."""
    d = json.loads((src.raw_dir / "incentives_2023_and_after.json").read_text())
    out = []
    for m in d.get("taxCreditManufacturer", []):
        lst = m.get("taxCreditOnwardList") or {}
        items = lst.get("taxCreditOnward", [])
        if isinstance(items, dict):
            items = [items]
        for it in items:
            out.append({
                "manufacturer": m.get("manufacturer"),
                "make": it.get("irsMake"), "model": it.get("irsModel"), "base_model": it.get("baseModel"),
                "model_year": it.get("modelYear") or it.get("year"),
                "powertrain": it.get("atvType"),
                "credit_amount_usd": num(it.get("creditAmount")),
                "msrp_limit_usd": num(it.get("MSRPLimit")),
                "assembly_country": it.get("assemblyCountry"),
                "meets_assembly_rule": it.get("meetsAssemblyRule"),
                "start_date": (it.get("startDate") or "")[:10],
                "end_date": (it.get("endDate") or "")[:10],
                "source_ref": f"irs_30d_list:{it.get('onwardTaxCreditID')}:{it.get('irsMake')}:{it.get('irsModel')}",
            })
    return out


register(Source(
    id="irs_30d_list", market="us",
    name="Federal Clean Vehicle Credit (IRC 30D) eligible vehicles, fueleconomy.gov tax data service",
    publisher="U.S. DOE / IRS via fueleconomy.gov REST (ws/rest/tax/incentives-2023-and-after)",
    urls={"incentives_2023_and_after.json": "https://www.fueleconomy.gov/ws/rest/tax/incentives-2023-and-after"},
    license="U.S. Government work, public domain",
    fields_used="irsMake, irsModel, atvType, creditAmount, MSRPLimit, assemblyCountry, startDate/endDate",
    derive=derive_irs_30d,
    notes="Historical eligibility list. IMPORTANT: 30D was terminated for vehicles acquired after 2025-09-30; seed models it as an expired incentive with valid_to. Verify current status before demo.",
))


def derive_census_places(src: Source, raw: dict) -> list[dict]:
    """Census place gazetteer -> top places by land area per state (synthetic dealer locations)."""
    import pandas as pd
    z = zipfile.ZipFile(src.raw_dir / "2023_Gaz_place_national.zip")
    name = [n for n in z.namelist() if n.endswith(".txt")][0]
    df = pd.read_csv(z.open(name), sep="\t", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df["ALAND_SQMI"] = pd.to_numeric(df["ALAND_SQMI"], errors="coerce")
    df = df[~df["NAME"].str.contains("CDP", na=False)]
    df = df.sort_values(["USPS", "ALAND_SQMI"], ascending=[True, False]).groupby("USPS").head(12)
    out = []
    for _, r in df.iterrows():
        out.append({
            "state": r["USPS"], "geoid": r["GEOID"], "place": re.sub(r"\s+(city|town|village|borough|municipality)$", "", r["NAME"]),
            "lat": round(float(r["INTPTLAT"]), 5), "lon": round(float(r["INTPTLONG"]), 5),
            "land_sqmi": round(float(r["ALAND_SQMI"]), 1) if pd.notna(r["ALAND_SQMI"]) else None,
            "source_ref": f"census_gazetteer_places:2023:{r['GEOID']}",
        })
    return out


register(Source(
    id="census_gazetteer_places", market="us",
    name="Census 2023 Gazetteer Files - Places (national)",
    publisher="U.S. Census Bureau",
    urls={"2023_Gaz_place_national.zip": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip"},
    license="U.S. Government work, public domain",
    fields_used="USPS state, place name, lat/lon, land area",
    derive=derive_census_places,
    notes="Largest incorporated places per state as synthetic dealer / customer locations (metro placement).",
))


# ─── Europe (Germany) sources ────────────────────────────────────────────────

def derive_kba_fz10(src: Source, raw: dict) -> list[dict]:
    """KBA FZ 10.1 Neuzulassungen nach Marken und Modellreihen -> brand/model month + YTD + powertrain splits."""
    import openpyxl
    p = src.raw_dir / "fz10_latest.xlsx"
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb[[s for s in wb.sheetnames if s.startswith("FZ 10")][0]]
    rows = list(ws.iter_rows(values_only=True))
    title = next((str(c) for r in rows[:8] for c in r if c and "Neuzulassungen von Personenkraftwagen" in str(c) and " im " in str(c)), "")
    period = title.split(" im ")[-1].strip() if " im " in title else ""
    # Header row is the one containing 'Marke'; it may start in column 1 (column 0 is a spacer).
    hdr_i, off = next((i, r.index("Marke")) for i, r in enumerate(rows) if r and "Marke" in r)
    rows = [tuple(r[off:]) for r in rows]          # normalise so 'Marke' is column 0
    grp = [c for c in rows[hdr_i - 1]] if hdr_i > 0 else []
    hdr = rows[hdr_i]
    # Build column map: (group, subheader) -> index. Group labels are forward-filled.
    cols = []
    cur = None
    for j, h in enumerate(hdr):
        g = grp[j] if j < len(grp) else None
        if g:
            cur = str(g).replace("\n", " ").strip()
        cols.append((cur, str(h).replace("\n", " ").strip() if h else ""))

    def slug(s: str) -> str:
        s = s.lower()
        s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
        return re.sub(r"[^a-z0-9]+", "_", s).strip("_")

    out = []
    brand = None
    for r in rows[hdr_i + 1:]:
        if not r or all(c is None for c in r):
            continue
        marke, modell = r[0], r[1]
        if marke and str(marke).strip().upper().endswith("ZUSAMMEN"):
            continue
        if marke and str(marke).strip().upper() in ("INSGESAMT", "SONSTIGE", "ZUSAMMEN"):
            continue
        if marke:
            brand = str(marke).strip()
        if modell is None or str(modell).strip() == "":
            continue
        rec = {"brand": brand, "model": str(modell).strip(), "period": period}
        for j, (g, h) in enumerate(cols[2:], start=2):
            if not g:
                continue
            key = slug(f"{g} {h}")
            rec[key] = num(r[j]) if j < len(r) else None
        rec["source_ref"] = f"kba_fz10:{period}:{brand}:{rec['model']}"
        out.append(rec)
    return out


register(Source(
    id="kba_fz10", market="de",
    name="KBA FZ 10 - Neuzulassungen von Personenkraftwagen nach Marken und Modellreihen (monatlich)",
    publisher="Kraftfahrt-Bundesamt (KBA)",
    urls={"fz10_latest.xlsx": "https://www.kba.de/SharedDocs/Downloads/DE/Statistik/Fahrzeuge/FZ10/fz10_2025_12.xlsx?__blob=publicationFile"},
    license="Datenlizenz Deutschland - Namensnennung - Version 2.0 (dl-de/by-2-0); attribution: Kraftfahrt-Bundesamt",
    fields_used="Marke, Modellreihe, Monat, Jan-Dez YTD, Anteil %, splits by Diesel / Hybrid / Plug-in / Elektro",
    derive=derive_kba_fz10,
    notes="German registrations by brand and model with powertrain splits; RDR and PMA proxy for the de market and A6.",
))


EEA_TABLE = "[CO2Emission].[latest].[co2cars]"


def _eea_query(ms_filter: str) -> str:
    return (
        "SELECT Mk, Cn, Ft, Year, COUNT(*) AS n, "
        "AVG([Ewltp (g/km)]) AS co2_wltp_g_km, AVG([Erwltp (g/km)]) AS co2_reduction_g_km, "
        "AVG([Z (Wh/km)]) AS wh_per_km, AVG(Fc) AS fuel_l_100km, AVG([Ep (KW)]) AS power_kw, "
        "AVG([M (kg)]) AS mass_kg, AVG([Ec (cm3)]) AS displ_cm3 "
        f"FROM {EEA_TABLE} WHERE {ms_filter} Ct='M1' AND Year = (SELECT MAX(Year) FROM {EEA_TABLE}) "
        "GROUP BY Mk, Cn, Ft, Year ORDER BY n DESC"
    )


def derive_eea(src: Source, raw: dict) -> list[dict]:
    p = src.raw_dir / "co2cars_agg.json"
    rows = json.loads(p.read_text())
    out = []
    for r in rows:
        if not r.get("Mk") or not r.get("Cn"):
            continue
        rec = {
            "make": str(r["Mk"]).strip(), "model": str(r["Cn"]).strip(), "fuel": r.get("Ft"), "year": r.get("Year"),
            "registrations": r.get("n"),
            "co2_wltp_g_km": round(r["co2_wltp_g_km"], 1) if r.get("co2_wltp_g_km") is not None else None,
            "wh_per_km": round(r["wh_per_km"], 1) if r.get("wh_per_km") is not None else None,
            "fuel_l_100km": round(r["fuel_l_100km"], 2) if r.get("fuel_l_100km") is not None else None,
            "power_kw": round(r["power_kw"], 0) if r.get("power_kw") is not None else None,
            "mass_kg": round(r["mass_kg"], 0) if r.get("mass_kg") is not None else None,
            "displ_cm3": round(r["displ_cm3"], 0) if r.get("displ_cm3") is not None else None,
            "source_ref": f"{src.id}:{r.get('Year')}:{r['Mk']}:{r['Cn']}:{r.get('Ft')}",
        }
        out.append(rec)
    return out[:2500]


register(Source(
    id="eea_co2_de", market="de",
    name="EEA Monitoring of CO2 emissions from passenger cars (Reg. (EU) 2019/631) - Germany aggregate by make/model/fuel",
    publisher="European Environment Agency (Discodata SQL API)",
    urls={"co2cars_agg.json": "sql:" + _eea_query("MS='DE' AND ")},
    license="EEA standard re-use policy (free re-use with attribution), Directive 2003/98/EC",
    fields_used="Mk, Cn, Ft, Year, count, avg Ewltp (WLTP CO2 g/km), Z (Wh/km), Fc (l/100km), Ep (kW), M (kg), Ec (cm3)",
    derive=derive_eea,
    notes="Per-registration records aggregated server-side to make/model/fuel for the latest final year in the 'latest' table (2022 at retrieval; provisional 2023/2024 are separate EEA releases). Used for spec anchors (consumption, CO2, power, mass); current volumes come from KBA. BEV electric range is not in the public per-vehicle table; range anchors use manufacturer WLTP figures curated in de/curated_ev_range.json.",
))

register(Source(
    id="eea_co2_eu", market="eu",
    name="EEA Monitoring of CO2 emissions from passenger cars - EU-wide aggregate by make/model/fuel",
    publisher="European Environment Agency (Discodata SQL API)",
    urls={"co2cars_agg.json": "sql:" + _eea_query("")},
    license="EEA standard re-use policy (free re-use with attribution)",
    fields_used="as eea_co2_de, all member states",
    derive=derive_eea,
    notes="EU-wide segment competitors and volumes for the Europe market pack.",
))


def derive_bnetza(src: Source, raw: dict) -> list[dict]:
    """Bundesnetzagentur Ladesaeulenregister -> charging infrastructure by Bundesland."""
    p = src.raw_dir / "Ladesaeulenregister.csv"
    text = p.read_bytes().decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    hdr_i = next(i for i, l in enumerate(lines) if l.startswith("Ladeeinrichtungs-ID"))
    reader = csv.DictReader(io.StringIO("\n".join(lines[hdr_i:])), delimiter=";")
    agg: dict[str, dict] = {}
    for r in reader:
        bl = (r.get("Bundesland") or "").strip()
        if not bl:
            continue
        a = agg.setdefault(bl, {"bundesland": bl, "stations": 0, "charge_points": 0, "fast_stations_ge_50kw": 0, "hpc_stations_ge_150kw": 0, "total_kw": 0.0})
        a["stations"] += 1
        a["charge_points"] += int(num(r.get("Anzahl Ladepunkte")) or 0)
        kw = num(r.get("Nennleistung Ladeeinrichtung [kW]")) or 0
        a["total_kw"] += kw
        if kw >= 50:
            a["fast_stations_ge_50kw"] += 1
        if kw >= 150:
            a["hpc_stations_ge_150kw"] += 1
    out = []
    for bl, a in sorted(agg.items()):
        a["total_kw"] = round(a["total_kw"])
        a["source_ref"] = f"bnetza_ladesaeulen:{bl}"
        out.append(a)
    return out


register(Source(
    id="bnetza_ladesaeulen", market="de",
    name="Bundesnetzagentur Ladesaeulenregister (public charging infrastructure register)",
    publisher="Bundesnetzagentur",
    urls={"Ladesaeulenregister.csv": "https://data.bundesnetzagentur.de/Bundesnetzagentur/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenregister_BNetzA_2026-09-01.csv"},
    license="Datenlizenz Deutschland - Namensnennung - Version 2.0 (dl-de/by-2-0)",
    fields_used="Bundesland, Anzahl Ladepunkte, Nennleistung Ladeeinrichtung [kW]",
    derive=derive_bnetza,
    notes="Charging stations, charge points, fast/HPC counts per Bundesland for EV readiness and TCO charging-availability messaging. URL is dated; update on refresh.",
))


def derive_eurostat_elec(src: Source, raw: dict) -> list[dict]:
    """Eurostat nrg_pc_204 household electricity prices (JSON-stat) -> latest period per country."""
    d = json.loads((src.raw_dir / "nrg_pc_204.json").read_text())
    dims = d["id"]
    sizes = d["size"]
    cats = {dim: d["dimension"][dim]["category"] for dim in dims}
    idx_to_label = {dim: {v: k for k, v in cats[dim]["index"].items()} for dim in dims}
    values = d["value"]

    def unravel(flat: int) -> dict:
        coords = {}
        for dim, size in zip(reversed(dims), reversed(sizes)):
            coords[dim] = flat % size
            flat //= size
        return coords

    latest: dict[str, tuple[str, float]] = {}
    for k, v in values.items():
        c = unravel(int(k))
        geo = idx_to_label["geo"][c["geo"]]
        t = idx_to_label["time"][c["time"]]
        if geo not in latest or t > latest[geo][0]:
            latest[geo] = (t, v)
    out = []
    for geo, (t, v) in sorted(latest.items()):
        out.append({"geo": geo, "period": t, "eur_per_kwh": round(float(v), 4),
                    "band": "DC (2,500-5,000 kWh)", "taxes": "all taxes and levies included",
                    "source_ref": f"eurostat_nrg_pc_204:{geo}:{t}"})
    return out


register(Source(
    id="eurostat_electricity", market="eu",
    name="Eurostat nrg_pc_204 - Electricity prices for household consumers (band DC, all taxes)",
    publisher="Eurostat",
    urls={"nrg_pc_204.json": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204"
                             "?format=JSON&lang=EN&siec=E7000&nrg_cons=KWH2500-4999&unit=KWH&tax=I_TAX&currency=EUR"
                             "&geo=DE&geo=FR&geo=IT&geo=ES&geo=NL&geo=AT&geo=BE&geo=PL&geo=SE&geo=EU27_2020"},
    license="Eurostat free re-use policy (CC BY 4.0 equivalent, attribution required)",
    fields_used="geo, time (semester), EUR/kWh incl. taxes, band DC",
    derive=derive_eurostat_elec,
    notes="energy_price_default for the de market and per-country overrides.",
))


def derive_oil_bulletin(src: Source, raw: dict) -> list[dict]:
    """EC Weekly Oil Bulletin price history -> latest Euro-super 95 and diesel per country (EUR/L incl. taxes).

    Sheet 'Prices with taxes': row 0 = column codes (CTR, EU_price_with_tax_euro95, ...), rows 1-2 = labels/units,
    data rows = [Date, CTR, euro95 per 1000 l, diesel per 1000 l, ...]. Rows are per date per country code.
    """
    import openpyxl
    p = src.raw_dir / "Oil_Bulletin_Prices_History.xlsx"
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb["Prices with taxes"]
    rows = ws.iter_rows(values_only=True)
    codes = [str(c).strip() if c is not None else "" for c in next(rows)]
    next(rows); next(rows)  # labels, units
    ci = {c: i for i, c in enumerate(codes)}
    date_i = next(i for i, c in enumerate(codes) if "inclusive of duties" in c.lower() or c.lower() == "date")
    ctr_i, e95_i, dsl_i = ci["CTR"], ci["EU_price_with_tax_euro95"], ci["EU_price_with_tax_diesel"]
    latest: dict[str, dict] = {}
    for r in rows:
        if not r or r[date_i] is None or r[ctr_i] is None:
            continue
        ctr = str(r[ctr_i]).strip().rstrip("_")
        d = r[date_i]
        ds = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
        if ctr in latest and latest[ctr]["date"] >= ds:
            continue
        e95, dsl = r[e95_i], r[dsl_i]
        latest[ctr] = {
            "country": ctr, "date": ds,
            "euro95_eur_per_l": round(float(e95) / 1000, 3) if isinstance(e95, (int, float)) else None,
            "diesel_eur_per_l": round(float(dsl) / 1000, 3) if isinstance(dsl, (int, float)) else None,
            "unit": "EUR per litre (converted from EUR/1000 l), duties and taxes included",
            "source_ref": f"ec_oil_bulletin:with_taxes:{ctr}:{ds}",
        }
    out = [latest[k] for k in sorted(latest)]

    # Latest weekly bulletin, per member state (Sheet1: row0 labels, row1 date+units, rows2+ = country, e95, diesel, ...)
    pw = src.raw_dir / "Weekly_prices_with_taxes.xlsx"
    if pw.exists():
        ws2 = openpyxl.load_workbook(pw, read_only=True, data_only=True).worksheets[0]
        r2 = list(ws2.iter_rows(values_only=True))
        d = r2[1][0]
        ds = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
        for r in r2[2:]:
            if not r or not isinstance(r[0], str) or not r[0].strip():
                continue
            e95, dsl = r[1], r[2]
            if not isinstance(e95, (int, float)):
                continue
            out.append({
                "country": r[0].strip(), "date": ds,
                "euro95_eur_per_l": round(float(e95) / 1000, 3),
                "diesel_eur_per_l": round(float(dsl) / 1000, 3) if isinstance(dsl, (int, float)) else None,
                "unit": "EUR per litre (converted from EUR/1000 l), duties and taxes included",
                "source_ref": f"ec_oil_bulletin:weekly_with_taxes:{r[0].strip()}:{ds}",
            })
    return out


register(Source(
    id="ec_oil_bulletin", market="eu",
    name="European Commission Weekly Oil Bulletin - EU price history + latest weekly prices per member state",
    publisher="European Commission, DG ENER",
    urls={"Oil_Bulletin_Prices_History.xlsx": "https://energy.ec.europa.eu/document/download/906e60ca-8b6a-44e7-8589-652854d2fd3f_en?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx",
          "Weekly_prices_with_taxes.xlsx": "https://energy.ec.europa.eu/document/download/264c2d0f-f161-4ea3-a777-78faae59bea0_en?filename=Weekly%20Oil%20Bulletin%20Weekly%20prices%20with%20Taxes%20-%202024-02-19.xlsx"},
    license="EU re-use policy (Decision 2011/833/EU), attribution required",
    fields_used="Country, date, Euro-super 95 and automotive gas oil (diesel) prices with taxes",
    derive=derive_oil_bulletin,
    notes="fuel_price_default for the de market TCO; per-country overrides.",
))


# ─── Runner ──────────────────────────────────────────────────────────────────

def fetch(src: Source) -> None:
    src.status["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    src.status["files"] = {}
    for fname, url in src.urls.items():
        dest = src.raw_dir / fname
        try:
            if url.startswith("sql:"):
                info = discodata_sql(url[4:], dest)
            else:
                info = download(url, dest)
            info["ok"] = True
            log(f"fetched {src.id}/{fname} ({info['bytes']:,} bytes)")
        except Exception as e:  # noqa: BLE001
            info = {"url": url, "ok": False, "error": f"{type(e).__name__}: {e}"}
            log(f"FAILED {src.id}/{fname}: {info['error']}")
        src.status["files"][fname] = info


def derive(src: Source) -> None:
    if not src.derive:
        return
    try:
        rows = src.derive(src, src.status)
        out = src.out_dir / f"{src.id}.csv"
        n = write_csv(out, rows)
        src.status["derived"] = {"ok": True, "path": str(out.relative_to(ROOT)), "rows": n,
                                 "bytes": out.stat().st_size}
        log(f"derived {out.relative_to(ROOT)} ({n} rows, {out.stat().st_size:,} bytes)")
    except Exception as e:  # noqa: BLE001
        src.status["derived"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        log(f"DERIVE FAILED {src.id}: {type(e).__name__}: {e}")


CURATED_SECTION = """
## Curated rule sets (hand-structured, no open structured feed exists)

These files live in `sample-data/open/curated/` and are maintained by hand from official pages. Every record carries
`source_urls` / `source_ref` and a `verify` flag; records with `verify: true` must be re-checked against the source
before a customer demo. Amounts and dates were confirmed on the curation date shown in each file's `_meta`.

| File | Market | Content | Primary sources |
|---|---|---|---|
| `curated/us/incentives.json` | us | Federal (30D expired 2025-09-30; 25E expired; car-loan interest deduction 2025-2028 active), state (CO, NY, MA, IL active; CA CVRP expired), utility template, synthetic OEM special | irs.gov clean-vehicle pages and OBBB FAQ; Federal Register 2026-18219; afdc.energy.gov/laws/state; state program pages |
| `curated/us/state_rules.csv` | us | 51 states: general sales-tax %, vehicle-tax note, avg local %, EV annual fee, doc-fee cap | State DOR / DMV pages (verify=true on all rows) |
| `curated/de/incentives.json` | de | Umweltbonus (expired 2023-12-17), E-Auto-Förderung 2026 (active, up to EUR 6,000, apps since 2026-05-19), Kfz-Steuer BEV exemptions (2025 rule + announced 2026-2030 extension), 0.25% BiK, Sonder-AfA, THG-Quote, wallbox template, synthetic OEM special | bafa.de Richtlinie 19.05.2026 (BAnz AT 19.06.2026 B3); alternative-fuels-observatory.ec.europa.eu Germany; umweltbundesamt.de |
| `curated/de/tax_rules.json` | de | 19% VAT, Kfz-Steuer displacement + CO2 bands and BEV weight-based rate, company-car BiK, finance products, 16 Bundeslaender | gesetze-im-internet.de/kraftstg; BMF; EAFO |
| `curated/de/ev_range_anchors.json` | de | WLTP range and battery anchors for 3 EV segments from manufacturer spec pages | Manufacturer configurators (verify=true) |
| `curated/lineup.json` | us, de | Synthetic brands **Meridian Motors** and **Nordwerk Automobile**: 7 models each, competitor sets with public base prices, price/spec anchor rules, trims | Manufacturer US/DE price pages (verify=true) |

## Known gaps and how the seed handles them

| Gap | Reason | Handling |
|---|---|---|
| NREL Alternative Fuel Station Locator API | `developer.nrel.gov` not resolvable from the build network | Replaced by the AFDC "EV Charging Ports by State" widget snapshot (`us/afdc_charging_ports.csv`) |
| EEA per-vehicle data is final only through 2022 in the `latest` table | Provisional 2023/2024 are separate EEA releases | EEA used for spec anchors (consumption, CO2, power, mass); current volumes come from KBA FZ 10 (Dec 2025) |
| BEV electric range not in the EEA public table | Column not published per vehicle | `curated/de/ev_range_anchors.json` from manufacturer specs |
| Competitor base prices | Not in any open dataset | Carried in `curated/lineup.json` with source URLs and verify=true |
| US state RDR-style registrations by make/model | Proprietary (Experian / S&P Polk) | AFDC state totals + EPA model list + KBA-style shares scaled to state fleet size; A6 `marketintel` connector stays a stub |
| German registrations by Bundesland x model | KBA FZ 10 is national by model; FZ 1/FZ 27 give Bundesland totals | Seed distributes national model volumes by Bundesland population (`tax_rules.json`); FZ 27 can be added later |
| Weekly Oil Bulletin per-country file has a stale filename (2024-02-19) | EC serves the current week under the old document id | Date is read from the sheet (2026-09-07 at retrieval), not the filename |
| CSI / customer-satisfaction scores | Survey data (J.D. Power, ADAC) is not open | Synthetic, seeded from published segment averages as priors |
| Incentive amounts for the 2026 German grant by income band | Guideline PDF not machine-readable from the BAFA landing page | Three-band model with editable amounts in Journey Ops; verify=true |

## Refresh procedure

```bash
python3 scripts/seed/fetch_open_data.py            # re-download everything and regenerate snapshots + this file
python3 scripts/seed/fetch_open_data.py --only kba_fz10 bnetza_ladesaeulen   # monthly sources
```

Dated URLs to bump on refresh: KBA `fz10_YYYY_MM.xlsx`, Bundesnetzagentur `Ladesaeulenregister_BNetzA_YYYY-MM-DD.csv`.
Raw downloads stay in `sample-data/open/raw/` (git-ignored, ~70 MB); only the derived snapshots and curated files are committed.
"""


def render_sources_md(man: dict) -> str:
    lines = [
        "# Open Data Sources for the Retail Ops Pilot Seed",
        "",
        "Generated by `scripts/seed/fetch_open_data.py` from `manifest.json`; do not edit the tables by hand.",
        f"Last generated: {man.get('generated_at', '')}. Spec reference: `docs/specs/intelligent-sales-service-orchestration.md` §2.5.1.",
        "",
        "Every derived row carries a `source_ref` that resolves to a source id below plus the raw key (year, make, model, state, ...).",
        "",
    ]
    for market, title in (("us", "United States (`us`, Meridian Motors)"), ("de", "Germany (`de`, Nordwerk Automobile)"), ("eu", "Europe-wide (`eu`, shared by the `de` pack)")):
        rows = [(sid, s) for sid, s in man["sources"].items() if s["market"] == market]
        if not rows:
            continue
        lines += [f"## {title}", "", "| Source id | Dataset | Publisher | License | Retrieved (UTC) | Raw files | Derived snapshot | Fields used | Notes |", "|---|---|---|---|---|---|---|---|---|"]
        for sid, s in rows:
            st = s.get("status", {})
            files = st.get("files", {})
            raw_desc = "<br/>".join(
                f"`{f}` ({i.get('bytes', 0):,} B, sha256 `{i.get('sha256', '')[:12]}…`)" if i.get("ok") else f"`{f}` FAILED"
                for f, i in files.items())
            urls = "<br/>".join(f"[{f}]({u})" if not u.startswith("sql:") else f"`{f}` (Discodata SQL)" for f, u in s["urls"].items())
            d = st.get("derived", {})
            derived = f"`{d.get('path', '')}` ({d.get('rows', 0)} rows)" if d.get("ok") else "FAILED"
            lines.append(f"| `{sid}` | {s['name']}<br/>{urls} | {s['publisher']} | {s['license']} | {st.get('fetched_at', '')} | {raw_desc} | {derived} | {s['fields_used']} | {s.get('notes', '')} |")
        lines.append("")
    lines.append(CURATED_SECTION.strip())
    lines.append("")
    return "\n".join(lines)


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"sources": {}}


def save_manifest(man: dict) -> None:
    man["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    MANIFEST.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="source ids to process")
    ap.add_argument("--derive-only", action="store_true", help="skip downloads, derive from existing raw files")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sources-md-only", action="store_true", help="regenerate SOURCES.md from the existing manifest and exit")
    args = ap.parse_args()

    if args.sources_md_only:
        (OPEN / "SOURCES.md").write_text(render_sources_md(load_manifest()))
        print(f"Wrote {(OPEN / 'SOURCES.md').relative_to(ROOT)}")
        return 0

    if args.list:
        for s in SOURCES:
            print(f"{s.id:26s} {s.market:6s} {s.name}")
        return 0

    selected = [s for s in SOURCES if not args.only or s.id in args.only]
    man = load_manifest()
    print(f"Open-data fetch: {len(selected)} sources -> {OPEN.relative_to(ROOT)}")
    for s in selected:
        print(f"\n[{s.id}] {s.name}")
        prev = man["sources"].get(s.id, {})
        if args.derive_only:
            s.status = prev.get("status", {}) or {}
            s.status.setdefault("fetched_at", prev.get("status", {}).get("fetched_at"))
        else:
            fetch(s)
        derive(s)
        man["sources"][s.id] = {
            "market": s.market, "name": s.name, "publisher": s.publisher, "license": s.license,
            "fields_used": s.fields_used, "notes": s.notes, "urls": s.urls, "status": s.status,
        }
        save_manifest(man)

    (OPEN / "SOURCES.md").write_text(render_sources_md(man))
    ok = sum(1 for s in selected if s.status.get("derived", {}).get("ok"))
    print(f"\nDone: {ok}/{len(selected)} sources derived. Manifest: {MANIFEST.relative_to(ROOT)}; SOURCES.md regenerated.")
    return 0 if ok == len(selected) else 1


if __name__ == "__main__":
    sys.exit(main())
