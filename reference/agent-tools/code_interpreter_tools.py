"""
AgentCore Code Interpreter tools (Requirements 2.1, 4.2, 4.7, 6.2).

All financial calculations — equity position, cost trajectory, ROI analysis,
and keep-vs-upgrade comparisons — are executed inside the AgentCore Code
Interpreter sandboxed environment for accuracy and auditability.
"""
from __future__ import annotations

import json
import textwrap
import uuid
from typing import Any

import boto3
from strands import tool

from ..config import config
from .. import observability


# ── Code Interpreter client ───────────────────────────────────────────────────

def _ci_client() -> Any:
    return boto3.client("bedrock-agentcore", region_name=config.aws_region)


def _execute_code(code: str, session_id: str | None = None) -> dict[str, Any]:
    """
    Execute Python code in the AgentCore Code Interpreter sandbox.
    Returns {'output': str, 'error': str | None, 'status': str}.
    Falls back to local execution if Code Interpreter is not configured.
    """
    request_id = str(uuid.uuid4())

    # If code interpreter is "local", execute locally (simple calculations only)
    if config.code_interpreter_id == "local":
        try:
            # Safe local execution for simple calculations
            import math
            safe_globals = {"math": math, "abs": abs, "round": round, "min": min, "max": max}
            local_scope: dict[str, Any] = {}
            exec(code, safe_globals, local_scope)
            # Look for a result variable or print output
            output = local_scope.get("result", "")
            if not output:
                output = str(local_scope)
            return {"output": str(output), "error": None, "status": "LOCAL", "request_id": request_id}
        except Exception as exc:
            observability.log_error("CODE_INTERPRETER_ERROR", "N/A", "execute_code_local", str(exc))
            return {"output": "", "error": str(exc), "status": "ERROR", "request_id": request_id}

    try:
        client = _ci_client()
        kwargs: dict[str, Any] = {
            "codeInterpreterIdentifier": config.code_interpreter_id,
            "codeInput": {"code": code, "language": "python"},
        }
        if session_id:
            kwargs["sessionId"] = session_id
        response = client.invoke_code_interpreter(**kwargs)
        output = response.get("output", {}).get("text", "")
        return {"output": output, "error": None, "status": "SUCCESS", "request_id": request_id}
    except Exception as exc:
        observability.log_error("CODE_INTERPRETER_ERROR", "N/A", "execute_code", str(exc))
        return {"output": "", "error": str(exc), "status": "ERROR", "request_id": request_id}


def _parse_output(result: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON from Code Interpreter stdout; return raw output on parse failure."""
    if result["status"] not in ("SUCCESS", "LOCAL") or not result["output"]:
        return result
    try:
        return json.loads(result["output"])
    except json.JSONDecodeError:
        return {"raw_output": result["output"]}


# ── Financial Calculation Tools ───────────────────────────────────────────────

@tool
def calculate_equity_position(
    current_market_value: float,
    remaining_lease_payoff: float,
    customer_id: str = "N/A",
) -> dict:
    """
    Calculate the equity position for a lease expiration prospect using the
    AgentCore Code Interpreter (Req 2.1).

    Args:
        current_market_value: Current market value of the vehicle in dollars.
        remaining_lease_payoff: Total remaining lease payoff amount in dollars.
        customer_id: Customer identifier for observability tracing.

    Returns:
        Dictionary with 'equity_amount', 'equity_percentage', 'position',
        'market_value', and 'remaining_payoff'.
    """
    with observability.trace_span("code_interpreter.calculate_equity_position", customer_id):
        code = textwrap.dedent(f"""
            import json
            market_value = {current_market_value}
            remaining_payoff = {remaining_lease_payoff}
            equity = market_value - remaining_payoff
            equity_pct = (equity / market_value * 100) if market_value > 0 else 0.0
            result = {{
                "equity_amount": round(equity, 2),
                "equity_percentage": round(equity_pct, 2),
                "position": "positive" if equity >= 0 else "negative",
                "market_value": market_value,
                "remaining_payoff": remaining_payoff,
            }}
            print(json.dumps(result))
        """)
        return _parse_output(_execute_code(code))


@tool
def compute_cost_trajectory(
    service_history_json: str,
    vehicle_age_years: int,
    current_annual_cost: float,
    customer_id: str = "N/A",
) -> dict:
    """
    Compute a Cost Trajectory forecasting repair costs over 12 and 24 months
    using the AgentCore Code Interpreter (Req 4.2, 6.2).

    Args:
        service_history_json: JSON string list of service records with 'cost', 'service_date',
                              and 'diagnostic_codes' fields.
        vehicle_age_years: Age of the vehicle in years.
        current_annual_cost: Total out-of-pocket service cost in the past 12 months.
        customer_id: Customer identifier for observability tracing.

    Returns:
        Dictionary with 'months_12_cost', 'months_24_cost', 'months_36_cost',
        'annual_increase_rate', 'predicted_next_failure', 'predicted_next_failure_cost'.
    """
    with observability.trace_span("code_interpreter.compute_cost_trajectory", customer_id):
        # Sanitize the JSON string for embedding in code
        safe_json = service_history_json.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
        code = textwrap.dedent(f"""
            import json, statistics

            service_history = json.loads("{safe_json}")
            vehicle_age = {vehicle_age_years}
            current_annual = {current_annual_cost}

            # Age-based escalation: older vehicles cost ~15% more per year
            base_escalation = 0.12 + (vehicle_age * 0.01)
            months_12 = round(current_annual * (1 + base_escalation), 2)
            months_24 = round(months_12 * (1 + base_escalation), 2)
            months_36 = round(months_24 * (1 + base_escalation), 2)

            # Predict next failure from diagnostic codes
            all_codes = []
            for record in service_history:
                all_codes.extend(record.get("diagnostic_codes", []))

            code_freq = {{}}
            for c in all_codes:
                code_freq[c] = code_freq.get(c, 0) + 1
            top_code = max(code_freq, key=code_freq.get) if code_freq else None

            # Rough cost estimate for recurring codes
            high_cost_codes = {{"P0300": 1200, "P0420": 900, "C0040": 800, "U0100": 600}}
            next_failure_cost = high_cost_codes.get(top_code, 500) if top_code else None

            result = {{
                "months_12_cost": months_12,
                "months_24_cost": months_24,
                "months_36_cost": months_36,
                "annual_increase_rate": round(base_escalation, 3),
                "predicted_next_failure": top_code,
                "predicted_next_failure_cost": next_failure_cost,
                "predicted_next_failure_months": 4 if top_code else None,
            }}
            print(json.dumps(result))
        """)
        return _parse_output(_execute_code(code))


@tool
def generate_roi_analysis(
    current_annual_repair_cost: float,
    projected_12m_repair_cost: float,
    projected_24m_repair_cost: float,
    new_vehicle_monthly_payment: float,
    trade_in_value: float,
    customer_id: str = "N/A",
) -> dict:
    """
    Generate an ROI analysis comparing keeping the current vehicle vs upgrading
    using the AgentCore Code Interpreter (Req 4.7).

    Args:
        current_annual_repair_cost: Current 12-month out-of-pocket repair cost.
        projected_12m_repair_cost: Projected repair cost over next 12 months.
        projected_24m_repair_cost: Projected repair cost over next 24 months.
        new_vehicle_monthly_payment: Estimated monthly payment for a replacement vehicle.
        trade_in_value: Estimated trade-in value of the current vehicle.
        customer_id: Customer identifier for observability tracing.

    Returns:
        Dictionary with 'keep_12m_cost', 'upgrade_12m_cost', 'keep_24m_cost',
        'upgrade_24m_cost', 'breakeven_months', and 'recommendation'.
    """
    with observability.trace_span("code_interpreter.generate_roi_analysis", customer_id):
        code = textwrap.dedent(f"""
            import json

            keep_12 = {projected_12m_repair_cost}
            keep_24 = {projected_24m_repair_cost}
            monthly_payment = {new_vehicle_monthly_payment}
            trade_in = {trade_in_value}

            # Upgrade cost = monthly payments minus trade-in offset spread over period
            upgrade_12 = (monthly_payment * 12) - trade_in
            upgrade_24 = (monthly_payment * 24) - trade_in

            # Breakeven: month where cumulative upgrade cost < cumulative keep cost
            breakeven = None
            cum_keep = 0
            cum_upgrade = -trade_in
            monthly_repair = {current_annual_repair_cost} / 12
            for m in range(1, 49):
                cum_keep += monthly_repair * (1 + 0.01 * m)  # escalating repairs
                cum_upgrade += monthly_payment
                if cum_upgrade <= cum_keep and breakeven is None:
                    breakeven = m

            result = {{
                "keep_12m_cost": round(keep_12, 2),
                "keep_24m_cost": round(keep_24, 2),
                "upgrade_12m_cost": round(upgrade_12, 2),
                "upgrade_24m_cost": round(upgrade_24, 2),
                "breakeven_months": breakeven,
                "net_24m_savings_from_upgrade": round(keep_24 - upgrade_24, 2),
                "recommendation": "UPGRADE" if upgrade_24 < keep_24 else "KEEP",
            }}
            print(json.dumps(result))
        """)
        return _parse_output(_execute_code(code))


@tool
def compute_keep_vs_upgrade_costs(
    vehicle_age_years: int,
    vehicle_mileage: int,
    annual_maintenance_cost: float,
    vehicle_depreciation_rate: float,
    new_vehicle_monthly_payment: float,
    trade_in_value: float,
    customer_id: str = "N/A",
) -> dict:
    """
    Generate a comprehensive keep-vs-upgrade cost comparison over 12, 24, and 36 months
    for aging vehicle prospects using the AgentCore Code Interpreter (Req 6.2).

    Args:
        vehicle_age_years: Current age of the vehicle in years.
        vehicle_mileage: Current mileage.
        annual_maintenance_cost: Current annual maintenance and repair cost.
        vehicle_depreciation_rate: Annual depreciation rate as a decimal (e.g. 0.15 = 15%).
        new_vehicle_monthly_payment: Estimated new vehicle monthly payment.
        trade_in_value: Current trade-in value estimate.
        customer_id: Customer identifier for observability tracing.

    Returns:
        Dictionary with costs at 12, 24, and 36 months for both keep and upgrade scenarios.
    """
    with observability.trace_span("code_interpreter.compute_keep_vs_upgrade_costs", customer_id):
        code = textwrap.dedent(f"""
            import json

            age = {vehicle_age_years}
            mileage = {vehicle_mileage}
            annual_maint = {annual_maintenance_cost}
            depr_rate = {vehicle_depreciation_rate}
            monthly_pmt = {new_vehicle_monthly_payment}
            trade_in = {trade_in_value}

            # Keep scenario: escalating maintenance + depreciation loss
            def keep_cost(months):
                escalation = 1 + (age * 0.015)
                monthly_maint = (annual_maint * escalation) / 12
                # Accelerating costs for older high-mileage vehicles
                if age >= 10 or mileage >= 150000:
                    monthly_maint *= 1.25
                return round(monthly_maint * months, 2)

            # Upgrade scenario: payments minus trade-in
            def upgrade_cost(months):
                return round((monthly_pmt * months) - trade_in, 2)

            result = {{
                "keep_12m": keep_cost(12),
                "keep_24m": keep_cost(24),
                "keep_36m": keep_cost(36),
                "upgrade_12m": upgrade_cost(12),
                "upgrade_24m": upgrade_cost(24),
                "upgrade_36m": upgrade_cost(36),
                "savings_12m": round(keep_cost(12) - upgrade_cost(12), 2),
                "savings_24m": round(keep_cost(24) - upgrade_cost(24), 2),
                "savings_36m": round(keep_cost(36) - upgrade_cost(36), 2),
                "upgrade_recommended": upgrade_cost(24) < keep_cost(24),
            }}
            print(json.dumps(result))
        """)
        return _parse_output(_execute_code(code))


@tool
def calculate_upgrade_readiness_score(
    service_visit_count: int,
    avg_repair_cost: float,
    annual_out_of_pocket: float,
    vehicle_health_score: float,
    customer_satisfaction_score: float,
    vehicle_age_years: int = 0,
    vehicle_mileage: int = 0,
    warranty_expired: bool = False,
    customer_id: str = "N/A",
) -> dict:
    """
    Calculate an Upgrade Readiness Score from 0 to 100 using the
    AgentCore Code Interpreter (Req 4.1, 6.1).

    Args:
        service_visit_count: Number of service visits in the past 12 months.
        avg_repair_cost: Average repair cost per service visit.
        annual_out_of_pocket: Total out-of-pocket service cost in past 12 months.
        vehicle_health_score: Vehicle health score 0–100 (100 = perfect).
        customer_satisfaction_score: Customer satisfaction score 0–10.
        vehicle_age_years: Vehicle age in years (for aging vehicle scoring).
        vehicle_mileage: Current mileage (for aging vehicle scoring).
        warranty_expired: Whether the factory warranty has expired.
        customer_id: Customer identifier for observability tracing.

    Returns:
        Dictionary with 'score' (0–100) and component breakdown.
    """
    with observability.trace_span("code_interpreter.calculate_upgrade_readiness_score", customer_id):
        code = textwrap.dedent(f"""
            import json

            visits = {service_visit_count}
            avg_cost = {avg_repair_cost}
            annual_oop = {annual_out_of_pocket}
            health = {vehicle_health_score}        # 0-100, higher = better
            satisfaction = {customer_satisfaction_score}  # 0-10, higher = happier
            age = {vehicle_age_years}
            mileage = {vehicle_mileage}
            warranty_expired = {warranty_expired}

            # Component scores (each 0–25)
            service_freq_score = min(25, (visits / 4) * 25)
            cost_burden_score = min(25, (annual_oop / 1500) * 25)
            vehicle_health_inv = min(25, ((100 - health) / 100) * 25)
            satisfaction_inv = min(25, ((10 - satisfaction) / 10) * 25)

            base_score = service_freq_score + cost_burden_score + vehicle_health_inv + satisfaction_inv

            # Bonus for aging vehicle factors
            age_bonus = min(10, age * 0.8) if age >= 7 else 0
            mileage_bonus = min(10, (mileage / 100000) * 5) if mileage >= 100000 else 0
            warranty_bonus = 5 if warranty_expired else 0

            total = min(100, round(base_score + age_bonus + mileage_bonus + warranty_bonus, 1))

            result = {{
                "score": total,
                "components": {{
                    "service_frequency": round(service_freq_score, 1),
                    "cost_burden": round(cost_burden_score, 1),
                    "vehicle_health": round(vehicle_health_inv, 1),
                    "satisfaction": round(satisfaction_inv, 1),
                    "age_bonus": round(age_bonus, 1),
                    "mileage_bonus": round(mileage_bonus, 1),
                    "warranty_bonus": warranty_bonus,
                }},
                "tier": "HIGH" if total >= 70 else ("MEDIUM" if total >= 40 else "LOW"),
            }}
            print(json.dumps(result))
        """)
        return _parse_output(_execute_code(code))


@tool
def estimate_trade_in_value(
    make: str,
    model: str,
    year: int,
    mileage: int,
    condition: str,
    market_value: float,
    customer_id: str = "N/A",
) -> dict:
    """
    Estimate the trade-in value for the current vehicle using the
    AgentCore Code Interpreter (Req 4.5, 6.5).

    Args:
        make: Vehicle make (e.g. 'Toyota').
        model: Vehicle model (e.g. 'Camry').
        year: Model year.
        mileage: Current mileage.
        condition: 'Excellent', 'Good', 'Fair', or 'Poor'.
        market_value: Current private-party market value.
        customer_id: Customer identifier for observability tracing.

    Returns:
        Dictionary with 'trade_in_value', 'condition_adjustment', and 'mileage_adjustment'.
    """
    with observability.trace_span("code_interpreter.estimate_trade_in_value", customer_id):
        code = textwrap.dedent(f"""
            import json

            market_value = {market_value}
            mileage = {mileage}
            condition = "{condition}"
            year = {year}
            import datetime
            age = datetime.date.today().year - year

            condition_factor = {{"Excellent": 0.90, "Good": 0.82, "Fair": 0.72, "Poor": 0.60}}.get(condition, 0.75)

            # Mileage penalty: -$0.05 per mile over 12k/year average
            avg_miles = age * 12000
            excess = max(0, mileage - avg_miles)
            mileage_penalty = excess * 0.05

            trade_in = round((market_value * condition_factor) - mileage_penalty, 2)
            trade_in = max(500, trade_in)  # floor

            result = {{
                "trade_in_value": trade_in,
                "market_value": market_value,
                "condition_factor": condition_factor,
                "mileage_penalty": round(mileage_penalty, 2),
                "excess_miles": excess,
            }}
            print(json.dumps(result))
        """)
        return _parse_output(_execute_code(code))
