"""Run configuration: deterministic RNG, SEED_NOW, volumes per market."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OPEN = ROOT / "sample-data" / "open"
OUT_ROOT = ROOT / "sample-data" / "synthetic"

STAGES = ["DISCOVER", "CONFIGURE", "QUALIFY", "FINANCE", "TRADE_IN", "AGREE", "DELIVER", "OWN"]
STAGE_MIX = {"DISCOVER": 0.30, "CONFIGURE": 0.22, "QUALIFY": 0.18, "FINANCE": 0.09,
             "TRADE_IN": 0.06, "AGREE": 0.05, "DELIVER": 0.04, "OWN": 0.06}

# Required context per stage (validate_handoff); journeys flagged demo_missing_context may violate these.
REQUIRED_CONTEXT = {
    "DISCOVER": [], "CONFIGURE": ["shortlist"], "QUALIFY": ["shortlist", "saved_build"],
    "FINANCE": ["shortlist", "saved_build", "budget", "timeline"],
    "TRADE_IN": ["shortlist", "saved_build", "budget", "timeline"],
    "AGREE": ["shortlist", "saved_build", "budget", "timeline", "finance_offer"],
    "DELIVER": ["shortlist", "saved_build", "budget", "timeline", "finance_offer", "deal"],
    "OWN": ["shortlist", "saved_build", "budget", "timeline", "finance_offer", "deal", "delivered"],
}


@dataclass
class Volumes:
    customers: int = 600
    journeys_active: int = 320
    journeys_closed: int = 180
    inventory_per_dealer: int = 100
    leads: int = 250
    campaigns: int = 6
    touchpoints: int = 4000
    outcomes: int = 900
    notes: int = 1500
    personalize_interactions: int = 60000
    bulk_messages: int = 300
    cold_cohort: int = 40


@dataclass
class RunConfig:
    market: str
    seed: int = 42
    now: datetime = field(default_factory=lambda: datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc))
    out: str = "local"
    volumes: Volumes = field(default_factory=Volumes)
    with_quick_artifacts: bool = False

    @property
    def rng(self) -> random.Random:
        # Cache the seeded RNG so every access returns the SAME instance (NFR1/BR2.1).
        # A fresh Random per access would reset the stream and break determinism for
        # any component that reads cfg.rng more than once. G binds it once, but this
        # guards later Units that may read it directly.
        cached = getattr(self, "_rng", None)
        if cached is None:
            cached = random.Random(f"{self.seed}:{self.market}")
            object.__setattr__(self, "_rng", cached)
        return cached

    def out_dir(self) -> Path:
        return OUT_ROOT / self.market

    def ago(self, days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
        return self.now - timedelta(days=days, hours=hours, minutes=minutes)

    def ahead(self, days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
        return self.now + timedelta(days=days, hours=hours, minutes=minutes)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def day(dt: datetime) -> str:
    return dt.date().isoformat()
