"""Acceptance #9 (Python side): no Indian-market literals in agent/service code."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN = re.compile(r"₹|\blakh\b|\bcrore\b", re.IGNORECASE)


def test_no_inr_literals_in_services():
    hits = []
    for p in (ROOT / "app" / "services").rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _FORBIDDEN.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "\n".join(hits)
