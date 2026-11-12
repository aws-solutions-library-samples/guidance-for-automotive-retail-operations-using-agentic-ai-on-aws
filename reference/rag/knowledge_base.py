"""RAG Knowledge Base — ChromaDB vector store with all sales domain data.

Seeds a local ChromaDB instance with dummy data across 6 collections:
  - customers          (customer 360 profiles)
  - objection_strategies (proven response strategies)
  - competitor_intel    (vehicle comparisons)
  - segment_patterns    (historical objection analytics)
  - vehicle_inventory   (current vehicle specs & pricing)
  - sentiment_lexicon   (sentiment analysis reference)

Uses ChromaDB's default local embedding (MiniLM-L6-v2 via ONNX) — no API keys.
"""

import chromadb

_client: chromadb.ClientAPI | None = None


def get_knowledge_base() -> chromadb.ClientAPI:
    """Return the singleton ChromaDB client, seeding on first call."""
    global _client
    if _client is None:
        _client = chromadb.Client()  # in-memory, no persistence needed
        seed_knowledge_base(_client)
    return _client


def seed_knowledge_base(client: chromadb.ClientAPI) -> None:
    """Populate all collections with dummy data."""
    _seed_customers(client)
    _seed_objection_strategies(client)
    _seed_competitor_intel(client)
    _seed_segment_patterns(client)
    _seed_vehicle_inventory(client)


# ── Customers ────────────────────────────────────────────────────────────────

def _seed_customers(client: chromadb.ClientAPI) -> None:
    col = client.get_or_create_collection("customers")
    documents = [
        (
            "Customer CUST-1001 Alex Johnson is a Premium Loyalist with budget "
            "$45,000-$60,000. Currently drives a 2020 Accord EX-L with lease ending "
            "2026-07-15. Equity position +$2,300. Satisfaction score 82. Medium price "
            "sensitivity. Prefers email contact. Past objections: Price, Feature. "
            "Lifetime value $127,000 across 3 previous purchases. Interested in "
            "2026 Accord Hybrid Touring and 2026 CR-V Hybrid."
        ),
        (
            "Customer CUST-1002 Maria Garcia is a First-Time Buyer with budget "
            "$25,000-$35,000. No current vehicle. High price sensitivity. Prefers "
            "text contact. Past objections: Price, Trust. No previous purchases. "
            "Interested in 2026 Civic Sport and 2026 HR-V EX."
        ),
        (
            "Customer CUST-1003 Robert Chen is a Conquest Opportunity with budget "
            "$50,000-$70,000. Currently drives a 2023 Toyota RAV4 Prime with lease "
            "ending 2026-09-01. Equity position -$1,200. Satisfaction score 65. Low "
            "price sensitivity. Prefers phone contact. Past objections: Competitor, "
            "Feature. No previous purchases with us. Interested in 2026 Pilot "
            "TrailSport and 2026 Passport."
        ),
        (
            "Customer CUST-1004 Sarah Williams is a Premium Loyalist with budget "
            "$55,000-$75,000. Currently drives a 2021 Pilot Touring. Lease ending "
            "2026-11-01. Equity position +$4,100. Satisfaction score 91. Low price "
            "sensitivity. Prefers phone contact. Past objections: Timing. Lifetime "
            "value $198,000 across 4 previous purchases. Interested in 2026 Pilot "
            "TrailSport and 2026 Passport TrailSport."
        ),
        (
            "Customer CUST-1005 James Park is a First-Time Buyer with budget "
            "$20,000-$28,000. No current vehicle. Very high price sensitivity. "
            "Prefers text contact. Past objections: Price, Price, Timing. No "
            "previous purchases. Interested in 2026 Civic LX and 2026 HR-V Sport."
        ),
    ]
    metadatas = [
        {"customer_id": "CUST-1001", "segment": "Premium Loyalist", "price_sensitivity": "medium"},
        {"customer_id": "CUST-1002", "segment": "First-Time Buyer", "price_sensitivity": "high"},
        {"customer_id": "CUST-1003", "segment": "Conquest Opportunity", "price_sensitivity": "low"},
        {"customer_id": "CUST-1004", "segment": "Premium Loyalist", "price_sensitivity": "low"},
        {"customer_id": "CUST-1005", "segment": "First-Time Buyer", "price_sensitivity": "very_high"},
    ]
    col.add(
        documents=documents,
        metadatas=metadatas,
        ids=[m["customer_id"] for m in metadatas],
    )


# ── Objection Strategies ─────────────────────────────────────────────────────

def _seed_objection_strategies(client: chromadb.ClientAPI) -> None:
    col = client.get_or_create_collection("objection_strategies")
    documents = [
        # Price strategies
        (
            "Strategy: Value Reframe for Price objections. Success rate 72%. "
            "Best for Premium Loyalist and Conquest Opportunity segments. "
            "Approach: Shift focus from sticker price to total cost of ownership. "
            "Highlight fuel savings over 5 years, higher resale value, and lower "
            "insurance costs. Template: 'I understand the price is a key factor. "
            "Let's look at the total cost of ownership — with fuel savings over "
            "5 years and our consistently higher resale value, the real cost "
            "difference is much smaller than the sticker suggests.' "
            "Supporting data: 5-year cost of ownership comparison, resale value "
            "projections, fuel savings calculator."
        ),
        (
            "Strategy: Payment Focus for Price objections. Success rate 68%. "
            "Best for First-Time Buyer and Premium Loyalist segments. "
            "Approach: Redirect from total price to monthly payment that fits "
            "their budget. Factor in equity position and current incentives. "
            "Template: 'Rather than the total price, let's focus on what fits "
            "your monthly budget. With current incentives and your equity, we "
            "can structure payments that work for you.' "
            "Supporting data: Current incentive programs, financing rate options, "
            "equity position calculation."
        ),
        (
            "Strategy: Incentive Reveal for Price objections. Success rate 65%. "
            "Best for First-Time Buyer segment. "
            "Approach: Present available loyalty/conquest bonuses and special "
            "financing rates that reduce effective price. "
            "Template: 'Great news — we have a loyalty bonus that applies to "
            "your situation, plus special APR financing. That brings the "
            "effective price down significantly.' "
            "Supporting data: Available manufacturer incentives, dealer-level "
            "discounts, special financing programs."
        ),
        # Feature strategies
        (
            "Strategy: Feature Bridge for Feature objections. Success rate 70%. "
            "Best for Premium Loyalist and Conquest Opportunity segments. "
            "Approach: Acknowledge the missing feature, then bridge to an "
            "alternative feature that addresses the same need, plus highlight "
            "unique features competitors lack. "
            "Template: 'I hear you on that feature. While this trim doesn't "
            "include it, the alternative feature actually addresses the same "
            "need. Plus, you get unique features that competitors don't offer.' "
            "Supporting data: Feature comparison matrix, customer satisfaction "
            "data for alternative features."
        ),
        (
            "Strategy: Trim Upgrade for Feature objections. Success rate 62%. "
            "Best for Premium Loyalist segment. "
            "Approach: Show the next trim level that includes the desired "
            "feature and present the monthly payment difference. "
            "Template: 'The higher trim includes that feature and more. The "
            "price difference is only a small amount per month — would you "
            "like to see how that fits your budget?' "
            "Supporting data: Trim-level feature breakdown, monthly payment "
            "delta calculation."
        ),
        # Timing strategies
        (
            "Strategy: Urgency with Value for Timing objections. Success rate 58%. "
            "Best for Premium Loyalist and First-Time Buyer segments. "
            "Approach: Respect their timeline but create awareness of expiring "
            "incentives and limited inventory. "
            "Template: 'I completely respect that. Just so you know, the current "
            "promotion ends soon, and inventory for this configuration is limited. "
            "I'd hate for you to miss out on the savings.' "
            "Supporting data: Current promotion calendar, inventory availability "
            "data, historical price trend."
        ),
        (
            "Strategy: Low-Pressure Next Step for Timing objections. Success rate 55%. "
            "Best for First-Time Buyer segment. "
            "Approach: Remove all pressure, offer to prepare information for "
            "when they're ready, and set a follow-up trigger. "
            "Template: 'No pressure at all. How about I put together a "
            "personalized quote so you have all the numbers when you're ready? "
            "I can also set a reminder to check in later.' "
            "Supporting data: Personalized quote builder, follow-up scheduling."
        ),
        # Trust strategies
        (
            "Strategy: Social Proof for Trust objections. Success rate 67%. "
            "Best for First-Time Buyer and Conquest Opportunity segments. "
            "Approach: Counter trust concerns with third-party reliability "
            "ratings, owner satisfaction data, and real customer testimonials. "
            "Template: 'That's a fair concern. Our model has a top reliability "
            "rating from J.D. Power, and 92% of owners say they'd buy again. "
            "I can share reviews from customers in similar situations.' "
            "Supporting data: J.D. Power reliability ratings, Consumer Reports "
            "scores, owner satisfaction surveys."
        ),
        (
            "Strategy: Warranty Assurance for Trust objections. Success rate 63%. "
            "Best for Premium Loyalist and First-Time Buyer segments. "
            "Approach: Emphasize comprehensive warranty coverage and extended "
            "protection options to provide peace of mind. "
            "Template: 'I understand wanting peace of mind. Our warranty covers "
            "bumper-to-bumper for 3 years/36K miles and powertrain for 5 years/"
            "60K miles. We also offer extended protection plans.' "
            "Supporting data: Warranty coverage details, extended protection "
            "plan options, service department ratings."
        ),
        # Competitor strategies
        (
            "Strategy: Acknowledge and Differentiate for Competitor objections. "
            "Success rate 71%. Best for Conquest Opportunity and Premium Loyalist "
            "segments. Approach: Respect the competitor, then highlight specific "
            "differentiators and superior resale value. "
            "Template: 'The competitor model is a solid vehicle — good choice to "
            "consider. Where our model really stands out is in specific areas "
            "like resale value, which is significantly higher at 3 years.' "
            "Supporting data: Head-to-head comparison sheet, resale value "
            "projections, third-party review scores."
        ),
        (
            "Strategy: Total Value Comparison for Competitor objections. "
            "Success rate 64%. Best for First-Time Buyer and Conquest Opportunity "
            "segments. Approach: Compare 5-year total cost of ownership including "
            "insurance, fuel, maintenance, and depreciation. "
            "Template: 'When you factor in lower insurance costs, better fuel "
            "economy, and higher trade-in value, the 5-year total cost is "
            "actually less than the competitor.' "
            "Supporting data: 5-year TCO comparison, insurance cost estimates, "
            "fuel cost projections."
        ),
    ]
    metadatas = [
        {"objection_type": "Price", "strategy": "Value Reframe", "success_rate": 0.72},
        {"objection_type": "Price", "strategy": "Payment Focus", "success_rate": 0.68},
        {"objection_type": "Price", "strategy": "Incentive Reveal", "success_rate": 0.65},
        {"objection_type": "Feature", "strategy": "Feature Bridge", "success_rate": 0.70},
        {"objection_type": "Feature", "strategy": "Trim Upgrade", "success_rate": 0.62},
        {"objection_type": "Timing", "strategy": "Urgency with Value", "success_rate": 0.58},
        {"objection_type": "Timing", "strategy": "Low-Pressure Next Step", "success_rate": 0.55},
        {"objection_type": "Trust", "strategy": "Social Proof", "success_rate": 0.67},
        {"objection_type": "Trust", "strategy": "Warranty Assurance", "success_rate": 0.63},
        {"objection_type": "Competitor", "strategy": "Acknowledge and Differentiate", "success_rate": 0.71},
        {"objection_type": "Competitor", "strategy": "Total Value Comparison", "success_rate": 0.64},
    ]
    col.add(
        documents=documents,
        metadatas=metadatas,
        ids=[f"strat-{m['objection_type'].lower()}-{i}" for i, m in enumerate(metadatas)],
    )


# ── Competitor Intelligence ──────────────────────────────────────────────────

def _seed_competitor_intel(client: chromadb.ClientAPI) -> None:
    col = client.get_or_create_collection("competitor_intel")
    documents = [
        (
            "Vehicle comparison: 2026 Accord Hybrid Touring ($38,500) vs "
            "2026 Toyota Camry Hybrid XSE ($37,200). Our advantages: More "
            "standard horsepower (225 vs 204 HP), larger trunk (16.7 vs 15.1 "
            "cu ft), standard wireless CarPlay/Android Auto, better predicted "
            "reliability per J.D. Power. Their advantages: Slightly lower MSRP "
            "(-$1,300), available AWD option."
        ),
        (
            "Vehicle comparison: 2026 Accord Hybrid Touring ($38,500) vs "
            "2026 Hyundai Sonata Hybrid Limited ($36,800). Our advantages: "
            "Higher resale value (52% vs 45% at 3 years), more refined ride "
            "quality, longer powertrain warranty track record. Their advantages: "
            "Lower MSRP (-$1,700), larger infotainment screen (12.3 vs 9 in), "
            "10-year/100K powertrain warranty."
        ),
        (
            "Vehicle comparison: 2026 Civic Sport ($28,500) vs 2026 Toyota "
            "Corolla SE ($27,400). Our advantages: More interior space (97.5 "
            "vs 88.6 cu ft), better driving dynamics, higher safety ratings "
            "(IIHS TSP+). Their advantages: Lower MSRP (-$1,100), better fuel "
            "economy (35 vs 33 combined MPG)."
        ),
        (
            "Vehicle comparison: 2026 Pilot TrailSport ($52,500) vs 2026 Toyota "
            "Grand Highlander Hybrid ($50,800). Our advantages: True off-road "
            "capability (TrailSport package), more cargo space (101 vs 97.5 cu "
            "ft), standard i-VTM4 AWD system. Their advantages: Better fuel "
            "economy (hybrid), lower MSRP (-$1,700)."
        ),
        (
            "Vehicle comparison: 2026 CR-V Hybrid ($36,200) vs 2026 Toyota "
            "RAV4 Hybrid ($35,400). Our advantages: More refined interior, "
            "better noise insulation, larger rear seat legroom (41.0 vs 37.8 in), "
            "standard 9-inch touchscreen. Their advantages: Lower MSRP (-$800), "
            "slightly better fuel economy (41 vs 40 combined MPG), available "
            "plug-in hybrid variant."
        ),
        (
            "Vehicle comparison: 2026 HR-V EX ($28,900) vs 2026 Toyota Corolla "
            "Cross LE ($27,600). Our advantages: More cargo space (24.4 vs 22.2 "
            "cu ft), better standard tech features, sportier driving feel. "
            "Their advantages: Lower MSRP (-$1,300), better fuel economy, "
            "available AWD on base trim."
        ),
    ]
    metadatas = [
        {"our_vehicle": "2026 Accord Hybrid Touring", "competitor": "2026 Toyota Camry Hybrid XSE"},
        {"our_vehicle": "2026 Accord Hybrid Touring", "competitor": "2026 Hyundai Sonata Hybrid Limited"},
        {"our_vehicle": "2026 Civic Sport", "competitor": "2026 Toyota Corolla SE"},
        {"our_vehicle": "2026 Pilot TrailSport", "competitor": "2026 Toyota Grand Highlander Hybrid"},
        {"our_vehicle": "2026 CR-V Hybrid", "competitor": "2026 Toyota RAV4 Hybrid"},
        {"our_vehicle": "2026 HR-V EX", "competitor": "2026 Toyota Corolla Cross LE"},
    ]
    col.add(
        documents=documents,
        metadatas=metadatas,
        ids=[f"comp-{i}" for i in range(len(documents))],
    )


# ── Segment Patterns ─────────────────────────────────────────────────────────

def _seed_segment_patterns(client: chromadb.ClientAPI) -> None:
    col = client.get_or_create_collection("segment_patterns")
    documents = [
        (
            "Segment: Premium Loyalist. Top objections: Price (38%, 74% resolution), "
            "Feature (27%, 81% resolution), Timing (20%, 62% resolution), "
            "Competitor (10%, 69% resolution), Trust (5%, 88% resolution). "
            "Most effective strategies: Value Reframe for Price (74% success), "
            "Feature Bridge for Feature (81% success), Urgency with Value for "
            "Timing (62% success). Average objections before close: 2.3. "
            "Common patterns: Price then Feature then Close, Competitor then "
            "Price then Close."
        ),
        (
            "Segment: First-Time Buyer. Top objections: Price (45%, 61% resolution), "
            "Trust (22%, 70% resolution), Timing (18%, 52% resolution), "
            "Feature (10%, 75% resolution), Competitor (5%, 58% resolution). "
            "Most effective strategies: Payment Focus for Price (68% success), "
            "Social Proof for Trust (70% success), Low-Pressure Next Step for "
            "Timing (55% success). Average objections before close: 3.1. "
            "Common patterns: Price then Trust then Timing then Close, "
            "Price then Price then Trust then Close."
        ),
        (
            "Segment: Conquest Opportunity. Top objections: Competitor (42%, "
            "65% resolution), Feature (25%, 72% resolution), Price (20%, "
            "68% resolution), Trust (8%, 60% resolution), Timing (5%, "
            "50% resolution). Most effective strategies: Acknowledge and "
            "Differentiate for Competitor (71% success), Feature Bridge for "
            "Feature (72% success), Value Reframe for Price (68% success). "
            "Average objections before close: 2.7. Common patterns: Competitor "
            "then Feature then Price then Close, Competitor then Competitor "
            "then Close."
        ),
    ]
    metadatas = [
        {"segment": "Premium Loyalist", "avg_objections_before_close": 2.3},
        {"segment": "First-Time Buyer", "avg_objections_before_close": 3.1},
        {"segment": "Conquest Opportunity", "avg_objections_before_close": 2.7},
    ]
    col.add(
        documents=documents,
        metadatas=metadatas,
        ids=[f"seg-{m['segment'].lower().replace(' ', '-')}" for m in metadatas],
    )


# ── Vehicle Inventory ────────────────────────────────────────────────────────

def _seed_vehicle_inventory(client: chromadb.ClientAPI) -> None:
    col = client.get_or_create_collection("vehicle_inventory")
    documents = [
        (
            "2026 Accord Hybrid Touring. MSRP $38,500. Powertrain: 2.0L hybrid, "
            "225 HP combined. Fuel economy: 48 city / 47 highway / 48 combined MPG. "
            "Key features: Wireless CarPlay/Android Auto, 9-inch touchscreen, "
            "leather seats, heated/ventilated front seats, Bose 12-speaker audio, "
            "Honda Sensing suite, adaptive cruise control, lane keeping assist, "
            "blind spot monitoring. Trunk: 16.7 cu ft. Warranty: 3yr/36K bumper-to-"
            "bumper, 5yr/60K powertrain. Current inventory: 12 units. "
            "Current incentive: $1,500 loyalty bonus + 2.9% APR for 60 months. "
            "Resale value at 3 years: 52%."
        ),
        (
            "2026 Civic Sport. MSRP $28,500. Powertrain: 1.5L turbo, 180 HP. "
            "Fuel economy: 31 city / 38 highway / 33 combined MPG. Key features: "
            "7-inch touchscreen, Apple CarPlay/Android Auto, sport-tuned suspension, "
            "18-inch alloy wheels, Honda Sensing suite, adaptive cruise control, "
            "lane keeping assist. Trunk: 14.8 cu ft. Interior space: 97.5 cu ft. "
            "Warranty: 3yr/36K bumper-to-bumper, 5yr/60K powertrain. "
            "Current inventory: 24 units. Current incentive: $750 first-time "
            "buyer bonus + 3.9% APR for 60 months. IIHS Top Safety Pick+. "
            "Resale value at 3 years: 55%."
        ),
        (
            "2026 CR-V Hybrid. MSRP $36,200. Powertrain: 2.0L hybrid, 204 HP. "
            "Fuel economy: 43 city / 36 highway / 40 combined MPG. Key features: "
            "9-inch touchscreen, wireless CarPlay/Android Auto, heated front seats, "
            "dual-zone climate, Honda Sensing suite, rear seat legroom 41.0 inches. "
            "Cargo: 36.3 cu ft behind rear seats. Warranty: 3yr/36K bumper-to-"
            "bumper, 5yr/60K powertrain. Current inventory: 18 units. "
            "Current incentive: $1,000 loyalty bonus + 3.4% APR for 60 months. "
            "Resale value at 3 years: 54%."
        ),
        (
            "2026 Pilot TrailSport. MSRP $52,500. Powertrain: 3.5L V6, 285 HP. "
            "Fuel economy: 19 city / 26 highway / 22 combined MPG. Key features: "
            "TrailSport exclusive suspension, all-terrain tires, i-VTM4 AWD, "
            "steel skid plates, 9-inch touchscreen, wireless CarPlay/Android Auto, "
            "leather seats, heated/ventilated front seats, 10-speaker audio, "
            "Honda Sensing suite. Cargo: 101 cu ft total. Towing: 5,000 lbs. "
            "Warranty: 3yr/36K bumper-to-bumper, 5yr/60K powertrain. "
            "Current inventory: 8 units. Current incentive: $2,000 conquest "
            "bonus + 2.4% APR for 60 months. Resale value at 3 years: 50%."
        ),
        (
            "2026 HR-V EX. MSRP $28,900. Powertrain: 2.0L, 158 HP. "
            "Fuel economy: 28 city / 34 highway / 30 combined MPG. Key features: "
            "9-inch touchscreen, wireless CarPlay/Android Auto, heated front seats, "
            "Honda Sensing suite, sunroof, blind spot monitoring. "
            "Cargo: 24.4 cu ft behind rear seats. Warranty: 3yr/36K bumper-to-"
            "bumper, 5yr/60K powertrain. Current inventory: 15 units. "
            "Current incentive: $500 bonus + 4.4% APR for 60 months. "
            "Resale value at 3 years: 51%."
        ),
        (
            "2026 Passport TrailSport. MSRP $47,800. Powertrain: 3.5L V6, 285 HP. "
            "Fuel economy: 20 city / 25 highway / 22 combined MPG. Key features: "
            "TrailSport exclusive suspension, all-terrain tires, i-VTM4 AWD, "
            "9-inch touchscreen, wireless CarPlay/Android Auto, leather seats, "
            "heated front seats, Honda Sensing suite. Cargo: 77.9 cu ft total. "
            "Towing: 5,000 lbs. Warranty: 3yr/36K bumper-to-bumper, 5yr/60K "
            "powertrain. Current inventory: 6 units. Current incentive: $1,500 "
            "conquest bonus + 2.9% APR for 60 months. Resale value at 3 years: 49%."
        ),
    ]
    metadatas = [
        {"vehicle": "2026 Accord Hybrid Touring", "msrp": 38500, "inventory": 12, "type": "sedan"},
        {"vehicle": "2026 Civic Sport", "msrp": 28500, "inventory": 24, "type": "sedan"},
        {"vehicle": "2026 CR-V Hybrid", "msrp": 36200, "inventory": 18, "type": "suv"},
        {"vehicle": "2026 Pilot TrailSport", "msrp": 52500, "inventory": 8, "type": "suv"},
        {"vehicle": "2026 HR-V EX", "msrp": 28900, "inventory": 15, "type": "suv"},
        {"vehicle": "2026 Passport TrailSport", "msrp": 47800, "inventory": 6, "type": "suv"},
    ]
    col.add(
        documents=documents,
        metadatas=metadatas,
        ids=[f"veh-{i}" for i in range(len(documents))],
    )
