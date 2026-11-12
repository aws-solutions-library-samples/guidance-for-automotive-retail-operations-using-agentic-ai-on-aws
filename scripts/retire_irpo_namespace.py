#!/usr/bin/env python3
"""Retire the IRPO_ technical namespace, replacing it with the neutral SSO_ family.

The product was renamed IRPO -> Intelligent Sales & Service Orchestration on
2026-09-23, but the technical namespace was deliberately kept as IRPO_. That
decision is now reversed: the namespace is retired everywhere and replaced with
a neutral prefix (neither the old product "IRPO" nor the shorthand "ISSO").

Mappings (order matters — most specific first):
  IRPO_<Table>   -> SSO_<Table>     (DynamoDB table names)
  irpo-<name>    -> sso-<name>       (infra: buckets, buses, schedulers, doc-ids)
  irpo_<field>   -> sso_<field>      (e.g. irpo_id -> sso_id)
  irpo/<path>    -> sso/<path>       (path segments)
  bare "irpo"    -> "sso"            (Athena db, lone lowercase token)

Deliberately NOT touched:
  - The product name "Intelligent Sales & Service Orchestration" (unchanged).
  - The shorthand "ISSO" (unchanged; still internal-only, non-brand).
  - File names isso-*.md (they are the product-renamed spec files, already correct).
  - The .git dir, the .kiro AI-DLC engine, and the aidlc/ workspace tree
    (memory + intent artifacts + DocumentKB copies are handled separately:
    project.md by hand, intent artifacts by hand, DocumentKB by re-running
    scripts/sync_specs_to_documentkb.sh).
  - automotive-agent-skills/TOOLS.md (generated — regenerate via extract_tools.py).

Usage: python3 scripts/retire_irpo_namespace.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTS = {".md", ".py", ".json", ".sh", ".txt", ".ts", ".tsx", ".yaml", ".yml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".kiro", "aidlc", "session-backup"}
SKIP_FILES = {
    "scripts/retire_irpo_namespace.py",
    "scripts/rename_product.py",              # documents the old (?!_) guard; leave as history
    "automotive-agent-skills/TOOLS.md",       # generated; regenerate instead of editing
}

# Ordered longest/most-specific first so a broader rule never eats a narrower one.
SUBS = [
    (re.compile(r"IRPO_"), "SSO_"),
    (re.compile(r"irpo-"), "sso-"),
    (re.compile(r"irpo_"), "sso_"),
    (re.compile(r"irpo/"), "sso/"),
    (re.compile(r"\birpo\b"), "sso"),
]


def iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in EXTS:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES:
            continue
        parts = set(rel.split("/"))
        if parts & SKIP_DIRS:
            continue
        yield p


def transform(text: str) -> tuple[str, int]:
    n = 0
    for pat, repl in SUBS:
        text, k = pat.subn(repl, text)
        n += k
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
    print(f"\n{total} replacements{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
