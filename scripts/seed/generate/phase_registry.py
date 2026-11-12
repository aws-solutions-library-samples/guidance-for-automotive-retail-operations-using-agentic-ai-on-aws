"""phase_registry.py (U4) — decoupled phase registration for the orchestrator.

Before U4, every unit had to edit the U1-owned ``generate_synthetic.py`` PHASES
literal to insert its generator callables, which re-triggered the skeleton and
prior-unit checkpoints on every unit. This registry lets each generator module
register its phases at import time, so ``generate_synthetic.py`` builds the phase
list from the registry and never changes again.

A phase is a one-argument callable ``f(g)`` taking the ``G`` instance (BR5.5).
Registration carries an integer ``order`` used as the sole sort key; ties keep
insertion order (Python's sort is stable), so the resolved sequence is
deterministic. Order bands (convention, not enforced):

    0   foundation        (U1: gen_org_users)
    10  platform-config   (U2: gen_platform_config)
    20  commerce          (U3: sales spine + gen_commerce)
    30  OEM               (U4: oem phases)
    40  corpora           (U5)
    50  storylines        (U6)
    90  platform-late     (U2: gen_platform_late — LAST, needs entities)

The migration in U4 moves the existing U1/U2/U3 entries into ``register(...)``
calls in their own modules at these exact orders, so the resolved list is the
same phases in the same order the old literal produced (a regression test in
``tests/test_u4_oem.py`` pins this).
"""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .core import G

Phase = Callable[["G"], object]

# Order bands (see module docstring). Exposed as named constants so callers do
# not sprinkle magic numbers; a unit registers at its band.
BAND_FOUNDATION = 0
BAND_PLATFORM_CONFIG = 10
BAND_COMMERCE = 20
BAND_OEM = 30
BAND_CORPORA = 40
BAND_STORYLINES = 50
BAND_PLATFORM_LATE = 90

# (order, insertion_seq, fn). insertion_seq breaks order ties deterministically
# without relying solely on sort stability across refactors.
_PHASES: list[tuple[int, int, Phase]] = []
_seq = 0


def register(order: int, fn: Phase) -> Phase:
    """Register a phase callable at the given order band. Returns fn (usable as
    a decorator or a plain call). Idempotent per (order, fn): registering the
    same callable at the same order twice records it once, so importing a module
    more than once does not duplicate its phases."""
    global _seq
    for o, _, existing in _PHASES:
        if existing is fn and o == order:
            return fn
    _PHASES.append((order, _seq, fn))
    _seq += 1
    return fn


# Generator modules whose import side-effect is a `register(...)` call. Discovery
# imports each once so the orchestrator never needs a per-unit import line. New
# units append their module name here (a data edit to this list — never a change
# to the U1-owned orchestrator). Order does not matter: `ordered_phases()` sorts.
_GENERATOR_MODULES = (
    "core",           # U1 foundation + U3 sales spine
    "platform",       # U2 config + late
    "commerce",       # U3 gen_commerce
    "oem",            # U4
    "corpora",        # U5
    "storylines_gen", # U6 storylines weaving (band 50)
    "demo_pack",      # scenario-ready subjects for the end-to-end demo (band 99, last)
)

_discovered = False


def discover() -> None:
    """Import every generator module once for its register(...) side effects.

    Idempotent. Import errors for a not-yet-created module are ignored so the
    registry works while later units are still being built."""
    global _discovered
    if _discovered:
        return
    import importlib
    for name in _GENERATOR_MODULES:
        try:
            importlib.import_module(f"{__package__}.{name}")
        except ModuleNotFoundError:  # pragma: no cover - module not built yet
            continue
    _discovered = True


def ordered_phases() -> tuple[Phase, ...]:
    """The registered phases, sorted by (order, insertion_seq).

    Triggers module discovery first so callers need not import each generator
    module explicitly."""
    discover()
    return tuple(fn for _, _, fn in sorted(_PHASES, key=lambda t: (t[0], t[1])))


def clear() -> None:
    """Drop all registrations. For tests that build a pristine registry."""
    global _seq, _discovered
    _PHASES.clear()
    _seq = 0
    _discovered = False
