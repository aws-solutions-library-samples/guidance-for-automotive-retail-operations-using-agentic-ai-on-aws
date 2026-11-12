#!/usr/bin/env python3
"""Rename the product from IRPO to ISSO in prose, keeping the technical namespace.

Rules (deliberately narrow):
  - "Intelligent Retail & Purchase Orchestration" -> "Intelligent Sales & Service Orchestration"
    (also the "and" and ASCII-ampersand spellings)
  - standalone "IRPO" (word boundary, NOT followed by "_") -> "ISSO"
  - KEEP: IRPO_<Table>, irpo-<infra/bucket/bus/scheduler names>, irpo (lowercase, e.g. Athena db, paths),
    the words inside historical decision-log rows that quote the old name in a dated entry are also
    renamed (the log entry text explains the rename).
  - file renames: docs/specs/irpo-architecture.md -> isso-architecture.md,
    irpo-synthetic-data-plan.md -> isso-synthetic-data-plan.md,
    intelligent-retail-purchase-orchestration.md -> intelligent-sales-service-orchestration.md,
    presentation IRPO_Executive_Overview.pptx -> ISSO_Executive_Overview.pptx
    and all references to those paths.

Usage: python3 scripts/rename_product.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTS = {".md", ".py", ".json", ".sh", ".txt"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "eval/results"}
SKIP_FILES = {"scripts/rename_product.py"}

NAME_OLD = re.compile(r"Intelligent Retail (?:&|&amp;|and) Purchase Orchestration")
NAME_NEW = "Intelligent Sales & Service Orchestration"
ACR = re.compile(r"\bIRPO\b(?!_)")

FILE_RENAMES = {
    "docs/specs/intelligent-retail-purchase-orchestration.md": "docs/specs/intelligent-sales-service-orchestration.md",
    "docs/specs/irpo-architecture.md": "docs/specs/isso-architecture.md",
    "docs/specs/irpo-synthetic-data-plan.md": "docs/specs/isso-synthetic-data-plan.md",
    "docs/presentation/IRPO_Executive_Overview.pptx": "docs/presentation/ISSO_Executive_Overview.pptx",
}
PATH_REFS = {
    "intelligent-retail-purchase-orchestration.md": "intelligent-sales-service-orchestration.md",
    "irpo-architecture.md": "isso-architecture.md",
    "irpo-synthetic-data-plan.md": "isso-synthetic-data-plan.md",
    "IRPO_Executive_Overview.pptx": "ISSO_Executive_Overview.pptx",
}


def iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in EXTS:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES or any(rel.startswith(s) or f"/{s}/" in f"/{rel}" for s in SKIP_DIRS):
            continue
        yield p


def transform(text: str) -> tuple[str, int]:
    n = 0
    text, k = NAME_OLD.subn(NAME_NEW, text); n += k
    text, k = ACR.subn("ISSO", text); n += k
    for old, new in PATH_REFS.items():
        text, k = re.subn(re.escape(old), new, text); n += k
    return text, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    total = 0
    for p in iter_files():
        old = p.read_text(encoding="utf-8", errors="surrogateescape")
        new, n = transform(old)
        if n:
            total += n
            print(f"{n:4d}  {p.relative_to(ROOT)}")
            if not args.dry_run:
                p.write_text(new, encoding="utf-8", errors="surrogateescape")
    for old, new in FILE_RENAMES.items():
        src, dst = ROOT / old, ROOT / new
        if src.exists():
            print(f"mv    {old} -> {new}")
            if not args.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
    print(f"\n{total} replacements{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
