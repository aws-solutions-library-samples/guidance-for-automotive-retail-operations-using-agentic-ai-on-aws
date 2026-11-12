"""Per-market configuration: locale, formatting, name/address pools, district geography, MarketConfig row."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import anchors as A


@dataclass
class Dealer:
    code: str
    name: str
    city: str
    region: str          # US state code / DE Bundesland code
    lat: float
    lon: float
    condition: str       # storyline condition key
    tier: str = "B"


@dataclass
class Market:
    id: str
    locale: str
    currency: str
    currency_symbol: str
    distance_unit: str
    brand: str
    brand_short: str
    captive_finance: str
    tagline: str
    region_dim: str
    tz: str
    country: str
    phone_fmt: str
    email_domain_pool: list[str]
    first_names: list[str]
    last_names: list[str]
    streets: list[str]
    district_name: str
    dealers: list[Dealer]
    bulk_regions: list[str]
    annual_distance: int
    fuel_price: float
    energy_price: float
    fuel_unit: str
    efficiency_unit_ice: str
    efficiency_unit_ev: str
    finance_products: list[dict]
    credit_tiers: list[dict] = field(default_factory=list)

    def money(self, v: float) -> str:
        if self.id == "us":
            return f"${v:,.0f}"
        s = f"{v:,.0f}".replace(",", ".")
        return f"{s} €"

    def phone(self, rng: random.Random) -> str:
        if self.id == "us":
            return f"({rng.choice([404, 470, 678, 706, 762, 912, 229, 478])}) 555-{rng.randint(100, 999):03d}{rng.randint(0, 9)}"
        return f"+49 {rng.choice([711, 721, 761, 731, 7531, 7131, 7071, 7121])} 555{rng.randint(1000, 9999)}"

    def email(self, first: str, last: str, rng: random.Random) -> str:
        f = first.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        l = last.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        return f"{f}.{l}{rng.choice(['', str(rng.randint(2, 99))])}@{rng.choice(self.email_domain_pool)}"

    def address(self, rng: random.Random, city: str, region: str) -> dict:
        n = rng.randint(12, 4890)
        st = rng.choice(self.streets)
        if self.id == "us":
            return {"street": f"{n} {st}", "city": city, "state": region, "zip5": f"{rng.randint(30000, 31999)}", "country": "US"}
        return {"street": f"{st} {n % 180 + 1}", "city": city, "bundesland": region, "plz": f"{rng.randint(70000, 79999)}", "country": "DE"}


# ─── Dealer geographies (real cities, fictional dealers) ─────────────────────

US_DEALERS = [
    Dealer("D-101", "Decatur Meridian", "Decatur", "GA", 33.7748, -84.2963, "csi_decline_lead_sla"),
    Dealer("D-102", "Columbus Meridian", "Columbus", "GA", 32.4610, -84.9877, "high_days_supply_ev_aging"),
    Dealer("D-103", "Marietta Meridian", "Marietta", "GA", 33.9526, -84.5499, "volume_down_conquest_pressure"),
    Dealer("D-104", "Athens Meridian", "Athens", "GA", 33.9519, -83.3576, "top_performer", "A"),
    Dealer("D-105", "Macon Meridian", "Macon", "GA", 32.8407, -83.6324, "ev_cert_incomplete_noncompliant"),
    Dealer("D-106", "Rome Meridian", "Rome", "GA", 34.2570, -85.1647, "compliance_conditional"),
    Dealer("D-107", "Savannah Meridian", "Savannah", "GA", 32.0809, -81.0912, "strong_weekend_weak_monday"),
    Dealer("D-108", "Augusta Meridian", "Augusta", "GA", 33.4735, -82.0105, "normal"),
    Dealer("D-109", "Albany Meridian", "Albany", "GA", 31.5785, -84.1557, "normal", "C"),
    Dealer("D-110", "Valdosta Meridian", "Valdosta", "GA", 30.8327, -83.2785, "normal", "C"),
    Dealer("D-111", "Gainesville Meridian", "Gainesville", "GA", 34.2979, -83.8241, "normal"),
]

DE_DEALERS = [
    Dealer("D-201", "Autohaus Nordwerk Reutlingen", "Reutlingen", "BW", 48.4914, 9.2043, "csi_decline_lead_sla"),
    Dealer("D-202", "Nordwerk Zentrum Karlsruhe", "Karlsruhe", "BW", 49.0069, 8.4037, "high_days_supply_ev_aging"),
    Dealer("D-203", "Nordwerk Freiburg", "Freiburg im Breisgau", "BW", 47.9990, 7.8421, "volume_down_conquest_pressure"),
    Dealer("D-204", "Nordwerk Ulm", "Ulm", "BW", 48.4011, 9.9876, "top_performer", "A"),
    Dealer("D-205", "Nordwerk Konstanz", "Konstanz", "BW", 47.6603, 9.1758, "ev_cert_incomplete_noncompliant"),
    Dealer("D-206", "Nordwerk Heilbronn", "Heilbronn", "BW", 49.1427, 9.2109, "compliance_conditional"),
    Dealer("D-207", "Nordwerk Tübingen", "Tübingen", "BW", 48.5216, 9.0576, "strong_weekend_weak_monday"),
    Dealer("D-208", "Nordwerk Stuttgart-Vaihingen", "Stuttgart", "BW", 48.7758, 9.1829, "normal", "A"),
    Dealer("D-209", "Nordwerk Pforzheim", "Pforzheim", "BW", 48.8922, 8.6946, "normal", "C"),
    Dealer("D-210", "Nordwerk Esslingen", "Esslingen am Neckar", "BW", 48.7406, 9.3108, "normal"),
    Dealer("D-211", "Nordwerk Ludwigsburg", "Ludwigsburg", "BW", 48.8974, 9.1916, "normal", "C"),
]

US_FIRST = ["James", "Maria", "Robert", "Jennifer", "Michael", "Linda", "David", "Elizabeth", "William", "Susan", "Joseph",
            "Jessica", "Daniel", "Sarah", "Matthew", "Karen", "Anthony", "Nancy", "Mark", "Lisa", "Andre", "Keisha", "Jamal",
            "Aisha", "Carlos", "Sofia", "Luis", "Camila", "Wei", "Mei", "Raj", "Priya", "Omar", "Fatima", "Ethan", "Olivia",
            "Noah", "Ava", "Liam", "Emma", "Tyler", "Brianna", "Kevin", "Ashley", "Brandon", "Megan", "Derek", "Monica"]
US_LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez",
           "Lopez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris",
           "Clark", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Green", "Baker", "Adams",
           "Nelson", "Carter", "Mitchell", "Roberts", "Turner", "Phillips", "Campbell", "Parker", "Evans", "Edwards", "Collins",
           "Nguyen", "Patel", "Kim", "Chen", "Okafor", "Washington"]
US_STREETS = ["Peachtree Way", "Magnolia Court", "Oakridge Drive", "Riverbend Lane", "Cedar Hollow Road", "Willow Creek Blvd",
              "Highland Park Ave", "Sycamore Trail", "Meadowbrook Circle", "Foxglove Terrace", "Laurel Ridge Pkwy", "Pine Hollow Ct"]

DE_FIRST = ["Lukas", "Anna", "Jonas", "Lea", "Maximilian", "Lena", "Felix", "Sophie", "Paul", "Marie", "Leon", "Hannah", "Tim",
            "Laura", "Jan", "Julia", "Niklas", "Sarah", "Moritz", "Lisa", "Tobias", "Katharina", "Stefan", "Nina", "Andreas",
            "Melanie", "Thomas", "Sabine", "Michael", "Claudia", "Markus", "Petra", "Christian", "Nicole", "Daniel", "Anja",
            "Mehmet", "Ayşe", "Ali", "Elif", "Dominik", "Vanessa", "Sebastian", "Kerstin", "Florian", "Jana", "Matthias", "Silke"]
DE_LAST = ["Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Schulz", "Hoffmann", "Schäfer",
           "Koch", "Bauer", "Richter", "Klein", "Wolf", "Schröder", "Neumann", "Schwarz", "Zimmermann", "Braun", "Krüger",
           "Hofmann", "Hartmann", "Lange", "Schmitt", "Werner", "Krause", "Meier", "Lehmann", "Huber", "Mayer", "Herrmann",
           "Köhler", "Walter", "König", "Kaiser", "Fuchs", "Peters", "Lang", "Scholz", "Möller", "Weiß", "Jung", "Hahn",
           "Yilmaz", "Kaya", "Demir", "Özdemir", "Albrecht", "Winter"]
DE_STREETS = ["Lindenstraße", "Bahnhofstraße", "Gartenweg", "Schulstraße", "Am Mühlbach", "Rosenweg", "Bergstraße", "Kirchgasse",
              "Neckarstraße", "Birkenallee", "Hauptstraße", "Wiesenweg"]

US_FINANCE = [
    {"code": "RETAIL", "name": "Retail installment", "terms_months": [36, 48, 60, 72]},
    {"code": "LEASE", "name": "Lease", "terms_months": [24, 36, 39]},
    {"code": "BALLOON", "name": "Balloon", "terms_months": [48, 60]},
]
US_TIERS = [{"tier": "Super Prime", "fico_min": 781, "apr": 4.9}, {"tier": "Prime", "fico_min": 661, "apr": 6.4},
            {"tier": "Near Prime", "fico_min": 601, "apr": 9.8}, {"tier": "Subprime", "fico_min": 501, "apr": 14.5}]
DE_TIERS = [{"tier": "Sehr gut", "score_min": 97, "apr": 3.49}, {"tier": "Gut", "score_min": 93, "apr": 4.99},
            {"tier": "Befriedigend", "score_min": 90, "apr": 6.99}, {"tier": "Ausreichend", "score_min": 80, "apr": 9.49}]


def build(market_id: str) -> Market:
    lu = A.lineup()["brands"][market_id]
    if market_id == "us":
        return Market(
            id="us", locale="en-US", currency="USD", currency_symbol="$", distance_unit="mi",
            brand=lu["brand"], brand_short="Meridian", captive_finance=lu["captive_finance"], tagline=lu["tagline"],
            region_dim="state", tz="America/New_York", country="US", phone_fmt="(xxx) xxx-xxxx",
            email_domain_pool=["example.com", "example.net", "mail.example.org"],
            first_names=US_FIRST, last_names=US_LAST, streets=US_STREETS,
            district_name="District 7 – Southeast", dealers=US_DEALERS,
            bulk_regions=["GA"] * 6 + ["FL", "SC", "AL", "TN", "NC"],
            annual_distance=13500, fuel_price=A.eia_gas(), energy_price=float(A.eia_electricity()["GA"]["usd_per_kwh"]),
            fuel_unit="gal", efficiency_unit_ice="mpg", efficiency_unit_ev="mpge",
            finance_products=US_FINANCE, credit_tiers=US_TIERS,
        )
    ob = A.oil_bulletin("Germany")
    return Market(
        id="de", locale="de-DE", currency="EUR", currency_symbol="€", distance_unit="km",
        brand=lu["brand"], brand_short="Nordwerk", captive_finance=lu["captive_finance"], tagline=lu["tagline"],
        region_dim="bundesland", tz="Europe/Berlin", country="DE", phone_fmt="+49 xxx xxxxxxx",
        email_domain_pool=["example.de", "beispiel.example", "mail.example.org"],
        first_names=DE_FIRST, last_names=DE_LAST, streets=DE_STREETS,
        district_name="Bezirk Süd-West", dealers=DE_DEALERS,
        bulk_regions=["BW"] * 6 + ["BY", "HE", "RP", "NW", "BE"],
        annual_distance=14000, fuel_price=ob["euro95"], energy_price=A.eurostat_elec("DE"),
        fuel_unit="l", efficiency_unit_ice="l/100km", efficiency_unit_ev="kWh/100km",
        finance_products=A.de_tax_rules()["finance_products"], credit_tiers=DE_TIERS,
    )


def market_config_row(m: Market) -> dict:
    """OEM_Markets row."""
    base = {
        "market_id": m.id, "locale": m.locale, "currency": m.currency, "currency_symbol": m.currency_symbol,
        "distance_unit": m.distance_unit, "fuel_unit": m.fuel_unit,
        "efficiency_unit_ice": m.efficiency_unit_ice, "efficiency_unit_ev": m.efficiency_unit_ev,
        "annual_distance_default": m.annual_distance, "fuel_price_default": round(m.fuel_price, 3),
        "energy_price_default": round(m.energy_price, 4), "region_dimension": m.region_dim, "timezone_default": m.tz,
        "brand": {"name": m.brand, "short": m.brand_short, "tagline": m.tagline, "captive_finance": m.captive_finance,
                  "colors": A.lineup()["brands"][m.id]["colors"]},
        "finance_products": m.finance_products, "credit_tiers": m.credit_tiers,
        "phone_format": m.phone_fmt, "country": m.country,
    }
    if m.id == "us":
        base.update({"tax_model": "state_sales_tax", "list_price_includes_tax": False,
                     "incentive_tiers": ["federal", "state", "utility", "oem"],
                     "regulatory_disclosures": ["TILA/Reg Z payment disclosure", "State doc-fee disclosure"],
                     "source_refs": ["eia_gasoline_weekly", "eia_electricity_state:GA", "curated:us/state_rules.csv"]})
    else:
        tr = A.de_tax_rules()
        base.update({"tax_model": "vat_included_kfz_steuer", "list_price_includes_tax": True, "vat_pct": tr["pricing"]["vat_pct"],
                     "kfz_steuer": tr["kfz_steuer"], "company_car": tr["company_car"],
                     "incentive_tiers": ["federal", "tax", "regional", "utility", "oem"],
                     "regulatory_disclosures": ["PAngV Effektivzins", "Verbraucherkreditrichtlinie Widerrufsrecht"],
                     "source_refs": ["ec_oil_bulletin:Germany", "eurostat_nrg_pc_204:DE", "curated:de/tax_rules.json"]})
    return base


def region_rules(m: Market) -> list[dict]:
    """OEM_StateRules rows (US: 51 states; DE: 16 Bundesländer)."""
    rows = []
    if m.id == "us":
        elec, chg, ev, fleet = A.eia_electricity(), A.afdc_charging(), A.afdc_ev_reg(), A.afdc_fleet()
        name_to_code = {r["state_name"]: r["state"] for r in A.us_state_rules().values()}
        for code, r in A.us_state_rules().items():
            e = elec.get(code, {})
            c = chg.get(r["state_name"], {})
            evr = ev.get(r["state_name"], {})
            fl = fleet.get(r["state_name"], {})
            rows.append({
                "market_id": "us", "region_code": code, "region_name": r["state_name"],
                "sales_tax_pct": A.fnum(r["general_sales_tax_pct"], 0), "avg_local_tax_pct": A.fnum(r["avg_local_sales_tax_pct"], 0),
                "vehicle_tax_note": r["vehicle_sales_tax_note"], "ev_annual_fee": A.fnum(r["ev_annual_fee_usd"], 0),
                "doc_fee_cap": A.fnum(r["doc_fee_cap_usd"]), "energy_price": A.fnum(e.get("usd_per_kwh"), m.energy_price),
                "energy_price_period": e.get("period"), "fuel_price": round(m.fuel_price, 3),
                "public_charging_ports": int(A.fnum(c.get("public_ports"), 0) or 0),
                "ev_registrations": int(A.fnum(evr.get("registration_count"), 0) or 0),
                "light_duty_fleet": int(sum(A.fnum(fl.get(k), 0) or 0 for k in fl if k not in ("state", "source_ref"))),
                "source_refs": [r["source_ref"], e.get("source_ref"), c.get("source_ref"), evr.get("source_ref")],
                "verify": True,
            })
        return rows
    tr = A.de_tax_rules()
    bn = A.bnetza()
    ob = A.oil_bulletin("Germany")
    for b in tr["bundeslaender"]:
        c = bn.get(b["name"], {})
        rows.append({
            "market_id": "de", "region_code": b["code"], "region_name": b["name"], "capital": b["capital"],
            "population_m": b["population_m"], "vat_pct": tr["pricing"]["vat_pct"],
            "energy_price": round(A.eurostat_elec("DE"), 4), "energy_price_period": "2025-S2",
            "fuel_price": ob["euro95"], "diesel_price": ob["diesel"], "fuel_price_date": ob["date"],
            "charging_stations": int(A.fnum(c.get("stations"), 0) or 0), "charge_points": int(A.fnum(c.get("charge_points"), 0) or 0),
            "hpc_stations_ge_150kw": int(A.fnum(c.get("hpc_stations_ge_150kw"), 0) or 0),
            "source_refs": ["curated:de/tax_rules.json", c.get("source_ref"), "eurostat_nrg_pc_204:DE", "ec_oil_bulletin:Germany"],
            "verify": True,
        })
    return rows
