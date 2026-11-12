"""generate_synthetic.py — CLI + orchestrator for the synthetic-data generator.

ONE deterministic run seeds the shared data layer for a market as infra-free
local artifacts. U1 (skeleton) wires the spine end to end:

    parse CLI -> RunConfig -> construct G (single seeded RNG) -> run the
    skeleton phase (G.gen_org_users) -> export tables + manifest -> validate.

Usage:
    python3 scripts/seed/generate/generate_synthetic.py --market us|de|all \
        [--seed 42] [--now 2026-09-10T09:00:00Z] [--out local]

Exit code = the AND of every selected market's validation verdict (0 = PASS).

The default path performs NO network or AWS calls (NFR3). This file follows the
existing argparse-__main__ idiom (FR1.1). Later Units extend the phase list
(Platform/Commerce/OEM/Corpora/Storylines) and the exporter formats; the spine
here does not change.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Support running as a module (python -m ...) or as a script (python path/to/file).
if __package__ in (None, ""):  # pragma: no cover - script-mode import shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scripts.seed.generate.config import RunConfig
    from scripts.seed.generate import market as market_mod
    # Import the phase modules for their side-effect register(...) calls.
    from scripts.seed.generate import core as _core  # noqa: F401
    from scripts.seed.generate import platform as _platform  # noqa: F401
    from scripts.seed.generate import commerce as _commerce  # noqa: F401
    from scripts.seed.generate import oem as _oem  # noqa: F401
    from scripts.seed.generate.core import G
    from scripts.seed.generate import phase_registry
    from scripts.seed.generate.exporters import export_all
    from scripts.seed.generate.validate import validate_market
else:
    from .config import RunConfig
    from . import market as market_mod
    # Import the phase modules for their side-effect register(...) calls.
    from . import core as _core  # noqa: F401
    from . import platform as _platform  # noqa: F401
    from . import commerce as _commerce  # noqa: F401
    from . import oem as _oem  # noqa: F401
    from .core import G
    from . import phase_registry
    from .exporters import export_all
    from .validate import validate_market

MARKETS = ("us", "de")

# Ordered phase list: each entry is a callable taking the G instance (BR5.5).
# A bound G method (G.gen_org_users) qualifies because it is a one-argument function
# of the instance. Dependency order:
#   foundation -> Platform(config) -> Commerce -> OEM -> Corpora -> Storylines -> Platform(late)
#
# The phase list is resolved from phase_registry (U4 refactor): each generator
# module registers its phases at import time via phase_registry.register(order, fn),
# and this orchestrator builds PHASES from phase_registry.ordered_phases(). Adding
# a phase in a later unit is a register(...) call in that unit's module — this file
# does not change again. The imports above trigger those register(...) side effects.
PHASES = phase_registry.ordered_phases()


def _parse_now(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def run_market(cfg: RunConfig) -> bool:
    """Generate -> export -> validate one market. Returns True on validation PASS."""
    market = market_mod.build(cfg.market)
    g = G(cfg, market)  # constructs the single seeded RNG (cfg.rng) once — BR2.1

    # Phase runner: invoke each phase callable in dependency order (BR2.3, BR5.5).
    for phase in PHASES:
        phase(g)

    # Export the accumulator to local artifacts; manifest written last (BR3.2).
    # Pass g.ix so U7 can serialize the Quick Index corpus (g.ix["corpus"]).
    export_all(cfg, g.t, g.ix)

    # Validate the exported set — the blocking gate (FR5.1).
    report = validate_market(cfg.out_dir())
    if report.passed:
        table_count = len(g.t)
        row_count = sum(len(v) for v in g.t.values())
        print(
            f"[{cfg.market}] PASS — {table_count} tables, {row_count} rows -> {cfg.out_dir()}"
        )
    else:
        print(f"[{cfg.market}] FAIL — {len(report.failures)} validation issue(s):")
        for f in report.failures:
            print(f"    - {f}")
    return report.passed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=("us", "de", "all"), default="all")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--now", type=str, default=None, help="ISO-8601 SEED_NOW override (default: config SEED_NOW)")
    ap.add_argument("--out", choices=("local",), default="local", help="infra-free local output (default)")
    args = ap.parse_args(argv)

    markets = list(MARKETS) if args.market == "all" else [args.market]
    now = _parse_now(args.now) if args.now else None

    all_passed = True
    for market_id in markets:
        # Each market is an independent deterministic run into its own dir (BR4.2).
        kwargs = {"market": market_id, "seed": args.seed, "out": args.out}
        if now is not None:
            kwargs["now"] = now
        cfg = RunConfig(**kwargs)
        passed = run_market(cfg)
        all_passed = all_passed and passed

    # Process exit = AND of every selected market's verdict (BR4.1).
    return 0 if all_passed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
