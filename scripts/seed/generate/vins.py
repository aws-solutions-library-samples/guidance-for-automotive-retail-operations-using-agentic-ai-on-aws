"""Synthetic VINs that look like VINs.

`7M7VIN-US-00190` read as a stub on every screen that showed it. A VIN is 17 characters: WMI (3),
VDS (5), check digit (1, ISO 3779), model-year code (1), plant (1), serial (6). These keep the
fictional WMIs (`7M7` Meridian, `7N7` Nordwerk — no real manufacturer), derive the VDS and plant
from a hash of the generator's own counter id (so the same seed yields the same VIN, NFR1), compute
the real check digit, and put the counter in the serial so a row is still traceable to its id.
No I, O or Q anywhere, as in a real VIN.
"""
from __future__ import annotations

import hashlib

_ALNUM = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"          # VIN alphabet (no I, O, Q)
_TRANS = {c: v for c, v in zip("ABCDEFGHJKLMNPRSTUVWXYZ", [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9])}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_YEAR_CODES = {2019: "K", 2020: "L", 2021: "M", 2022: "N", 2023: "P", 2024: "R", 2025: "S", 2026: "T", 2027: "V"}
WMI = {"us": "7M7", "de": "7N7"}


def check_digit(vin16: str) -> str:
    """ISO 3779 check digit for a VIN with position 9 ignored."""
    total = 0
    for i, ch in enumerate(vin16):
        if i == 8:
            continue
        total += (_TRANS[ch] if ch.isalpha() else int(ch)) * _WEIGHTS[i]
    r = total % 11
    return "X" if r == 10 else str(r)


def synthetic_vin(counter_id: str, market: str, year: int | None = None) -> str:
    """'VIN-US-00190' → '7M7HK3TN5P2000190' (deterministic; serial = the counter)."""
    h = hashlib.sha1(counter_id.encode()).hexdigest()
    vds = "".join(_ALNUM[int(h[i:i + 2], 16) % len(_ALNUM)] for i in range(0, 10, 2))
    plant = _ALNUM[int(h[10:12], 16) % 23]                     # a letter
    digits = "".join(c for c in counter_id if c.isdigit())[-6:].rjust(6, "0")
    yc = _YEAR_CODES.get(int(year) if year else 2025, "S")
    body = f"{WMI.get(market, '7M7')}{vds}0{yc}{plant}{digits}"
    return body[:8] + check_digit(body) + body[9:]


def looks_like_vin(vin: str) -> bool:
    return (len(vin) == 17 and all(c in _ALNUM for c in vin) and vin[8] == check_digit(vin))
