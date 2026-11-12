"""
Market data tools for the Vehicle Recommendation Agent.

Returns realistic mock market data for demo purposes.
In production, these would use AgentCore Browser to scrape live data.
"""
from __future__ import annotations

from strands import tool


@tool
def get_vehicle_reviews(make: str, model: str, year: int) -> dict:
    """Retrieve consumer reviews and ratings for a vehicle.

    Returns review scores, common praise/complaints, and expert ratings.

    Args:
        make: Vehicle manufacturer (e.g. 'Halden').
        model: Vehicle model (e.g. 'Alder').
        year: Model year.
    """
    # Realistic review data for common models
    reviews_db = {
        "honda": {"rating": 4.5, "expert_rating": 8.7, "pros": ["Reliability", "Fuel efficiency", "Resale value"], "cons": ["Road noise", "Infotainment learning curve"]},
        "toyota": {"rating": 4.6, "expert_rating": 8.8, "pros": ["Dependability", "Safety features", "Low maintenance"], "cons": ["Conservative styling", "Price premium"]},
        "ford": {"rating": 4.2, "expert_rating": 8.1, "pros": ["Towing capability", "Technology", "Ride comfort"], "cons": ["Fuel economy", "Depreciation"]},
        "chevrolet": {"rating": 4.1, "expert_rating": 8.0, "pros": ["Value pricing", "Features for price", "EV options"], "cons": ["Interior quality", "Resale value"]},
        "hyundai": {"rating": 4.4, "expert_rating": 8.5, "pros": ["Warranty coverage", "Feature-rich", "Modern design"], "cons": ["Brand perception", "Turbo lag"]},
        "subaru": {"rating": 4.3, "expert_rating": 8.3, "pros": ["AWD standard", "Safety", "Durability"], "cons": ["Engine noise", "Infotainment"]},
        "mazda": {"rating": 4.4, "expert_rating": 8.6, "pros": ["Driving dynamics", "Interior quality", "Design"], "cons": ["Cargo space", "Rear seat room"]},
        "bmw": {"rating": 4.3, "expert_rating": 8.9, "pros": ["Performance", "Luxury interior", "Technology"], "cons": ["Maintenance cost", "Price"]},
        "nissan": {"rating": 4.0, "expert_rating": 7.8, "pros": ["Comfortable ride", "Spacious", "Pricing"], "cons": ["CVT feel", "Dated tech"]},
        "kia": {"rating": 4.3, "expert_rating": 8.4, "pros": ["Value", "Warranty", "Design improvement"], "cons": ["Resale value", "Brand perception"]},
    }

    data = reviews_db.get(make.lower(), {"rating": 4.0, "expert_rating": 8.0, "pros": ["Solid option"], "cons": ["Limited data"]})
    return {
        "make": make,
        "model": model,
        "year": year,
        "consumer_rating": data["rating"],
        "expert_rating": data["expert_rating"],
        "pros": data["pros"],
        "cons": data["cons"],
        "review_count": 150,
        "source": "aggregated_reviews",
        "data_quality": "MOCK_DATA",
    }


@tool
def get_current_market_price(make: str, model: str, year: int, trim: str) -> dict:
    """Retrieve current market pricing for a vehicle.

    Returns MSRP, invoice price, and average transaction price.

    Args:
        make: Vehicle manufacturer.
        model: Vehicle model.
        year: Model year.
        trim: Trim level.
    """
    # Return reasonable market data based on typical pricing
    return {
        "make": make,
        "model": model,
        "year": year,
        "trim": trim,
        "msrp": "See inventory listing",
        "avg_transaction_price": "Typically 3-5% below MSRP",
        "invoice_price": "Typically 5-8% below MSRP",
        "market_trend": "stable",
        "days_supply": 45,
        "source": "market_estimate",
        "data_quality": "MOCK_DATA",
    }


@tool
def get_competitor_offers(segment: str, zip_code: str) -> dict:
    """Retrieve competitor offers in the same vehicle segment.

    Used to ensure recommendations are competitively positioned.

    Args:
        segment: Vehicle segment (e.g. 'compact_suv', 'midsize_sedan').
        zip_code: Customer zip code for local deals.
    """
    segment_deals = {
        "suv": [
            {"competitor": "Kestrel Trek 4", "deal": "$1,000 off MSRP + 3.9% APR", "segment": "compact_suv"},
            {"competitor": "Solari Dune", "deal": "$2,000 Spring Event Cash", "segment": "compact_suv"},
            {"competitor": "Larkspur Fenland", "deal": "$2,500 inventory reduction", "segment": "compact_suv"},
        ],
        "sedan": [
            {"competitor": "Kestrel Crescent", "deal": "$1,500 Spring Sales Event", "segment": "midsize_sedan"},
            {"competitor": "Halden Cadence", "deal": "1.9% APR for 60 months", "segment": "midsize_sedan"},
            {"competitor": "Solari Solace", "deal": "$2,000 off + 0% for 48 months", "segment": "midsize_sedan"},
        ],
        "truck": [
            {"competitor": "Larkspur F-100", "deal": "$3,000 off MSRP", "segment": "full_size_truck"},
            {"competitor": "Brightwater Silvercrest", "deal": "$4,000 total value", "segment": "full_size_truck"},
        ],
        "ev": [
            {"competitor": "Ampera Spark S", "deal": "Starting at $38,990", "segment": "ev"},
            {"competitor": "Brightwater Tide EV", "deal": "$7,500 federal tax credit", "segment": "ev"},
        ],
    }

    segment_lower = segment.lower().replace(" ", "_")
    deals = []
    for key, offers in segment_deals.items():
        if key in segment_lower:
            deals = offers
            break
    if not deals:
        deals = segment_deals.get("suv", [])

    return {
        "segment": segment,
        "zip_code": zip_code,
        "competitor_deals": deals,
        "count": len(deals),
        "source": "market_estimate",
        "data_quality": "MOCK_DATA",
    }
