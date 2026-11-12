"""§2.5 market formatters + TCO (acceptance #9, #10) for both seed markets."""
from __future__ import annotations

import pytest

from app.services.common import market


@pytest.fixture
def us(fake_ddb):
    return market.MarketConfig.load("us")


@pytest.fixture
def de(fake_ddb):
    return market.MarketConfig.load("de")


def test_requires_explicit_market(fake_ddb):
    with pytest.raises(market.MissingMarketConfig):
        market.MarketConfig.load(None)
    with pytest.raises(market.MissingMarketConfig):
        market.MarketConfig.load("in")
    with pytest.raises(market.MissingMarketConfig):
        market.fmt_money(None, 1)  # type: ignore[arg-type]


def test_money_and_units(us, de):
    assert market.fmt_money(us, 32450) == "$32,450"
    assert market.fmt_money(us, -1200.5, 2) == "-$1,200.50"
    assert market.fmt_money(de, 32450) == "32.450 €"
    assert market.fmt_distance(us, 13500) == "13,500 mi"
    assert market.fmt_distance(de, 14000) == "14.000 km"
    assert market.fmt_efficiency(us, 31, "ice") == "31 mpg"
    assert market.fmt_efficiency(us, 112, "ev") == "112 mpge"
    assert market.fmt_efficiency(de, 6.4, "ice") == "6,4 l/100km"
    assert market.fmt_efficiency(de, 16.8, "bev") == "16,8 kWh/100km"


def test_tco_us_and_de_audit_trail(us, de):
    out = market.tco(us, [
        {"vehicle_id": "ICE", "price": 32000, "efficiency": 30, "powertrain": "ice"},
        {"vehicle_id": "EV", "price": 41000, "efficiency": 110, "powertrain": "ev", "incentives": 0},
    ])
    ice, ev = out["vehicles"]
    expected_ice = 32000 + 5 * round(13500 / 30 * us.fuel_price_default, 2)
    assert ice["total"] == pytest.approx(expected_ice)
    assert ice["inputs"]["energy"]["formula"].startswith("13500.0 / 30.0")
    assert ev["inputs"]["energy"]["efficiency_unit"] == "mpge"
    assert out["currency"] == "USD" and ice["total_display"].startswith("$")

    out_de = market.tco(de, [{"vehicle_id": "BEV", "price": 45000, "efficiency": 17, "powertrain": "bev"}])
    bev = out_de["vehicles"][0]
    assert bev["inputs"]["energy"]["annual_cost"] == pytest.approx(
        round(14000 / 100 * 17 * de.energy_price_default, 2))
    assert bev["total_display"].endswith("€")
