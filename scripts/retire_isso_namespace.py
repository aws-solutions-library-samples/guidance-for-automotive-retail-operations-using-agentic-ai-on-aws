#!/usr/bin/env python3
"""Retire the "isso" technical namespace, replacing it with "retail-ops".

The product is Automotive Retail Operations powered by Agentic AI (2026-10-19).
"ISSO" was the internal shorthand of the orchestration layer and the stem of
every technical name. This script renames that stem across the repository so a
fresh deployment creates `retail-ops-…` resources. It does NOT touch AWS: the
existing `isso-dev-…` stack keeps running until it is removed with
`deploy/destroy.sh --stem isso …` and recreated with `deploy/bootstrap.sh`.

Mappings (order matters — most specific first):
  X-Isso-Token / x-isso-token -> X-Retail-Ops-Token / x-retail-ops-token
  ISSO_<NAME>                 -> RETAIL_OPS_<NAME>   (environment variables)
  Isso<Word> / isso<Word>     -> RetailOps<Word> / retailOps<Word>  (IAM Sids, metric names)
  isso-<name>                 -> retail-ops-<name>   (AWS names, Quick ids, groups, files)
  isso_<name>                 -> retail_ops_<name>   (Glue db, AgentCore names, identifiers)
  isso.<key>                  -> retail-ops.<key>    (browser storage keys, dev email domain)
  isso/<path>                 -> retail-ops/<path>   (secret paths)
  bare lowercase "isso"       -> "retail-ops"
  ISSO inside ```mermaid```   -> RETAIL_OPS          (node ids cannot contain spaces)
  "an ISSO" / "ISSO-minted"   -> "a Retail Ops" / "solution-minted"
  ISSO (word)                 -> Retail Ops

Deliberately NOT touched:
  - "ISSO_" written as a bare retired prefix (e.g. "`IRPO_` / `ISSO_` / `SSO_`"):
    those sentences record retired identifiers; only ISSO_<NAME> is renamed.
  - scripts/seed/generate/validate.py: its RETIRED_TOKENS list must keep "ISSO_".
  - History: the main spec's decisions-log rows dated before 2026-10-19, its
    Version row and resolved open question 12; the earlier rename scripts.
  - The .git dir, the .kiro engine, and the aidlc/ tree (audit and intent
    records are history; DocumentKB copies are re-synced by
    scripts/sync_specs_to_documentkb.sh; project.md is edited by hand).
  - Gitignored local files (app/infra/*-config.json describe the live isso stack
    and are used by the teardown; docs/presentation/ is out of scope).

Usage: python3 scripts/retire_isso_namespace.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_PREFIXES = (".kiro/", "aidlc/", "docs/presentation/")
SKIP_FILES = {
    "scripts/retire_isso_namespace.py",
    "scripts/retire_irpo_namespace.py",
    "scripts/rename_product.py",
    "AGENTS.md",                                  # AI-DLC managed block
}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pptx", ".zip", ".pdf", ".ico", ".woff", ".woff2"}
FILE_RENAMES = {
    "docs/specs/isso-architecture.md": "docs/specs/retail-ops-architecture.md",
    "docs/specs/isso-quick-project.md": "docs/specs/retail-ops-quick-project.md",
    "docs/specs/isso-synthetic-data-plan.md": "docs/specs/retail-ops-synthetic-data-plan.md",
}
MAIN_SPEC = "docs/specs/intelligent-sales-service-orchestration.md"

SUBS = [
    (re.compile(r"X-Isso-Token"), "X-Retail-Ops-Token"),
    (re.compile(r"x-isso-token"), "x-retail-ops-token"),
    (re.compile(r"ISSO_(?=[A-Z0-9])"), "RETAIL_OPS_"),
    (re.compile(r"Isso(?=[A-Z])"), "RetailOps"),
    (re.compile(r"isso(?=[A-Z])"), "retailOps"),
    (re.compile(r"isso-"), "retail-ops-"),
    (re.compile(r"isso_"), "retail_ops_"),
    (re.compile(r"isso\.(?=[a-z])"), "retail-ops."),
    (re.compile(r"isso/"), "retail-ops/"),
    (re.compile(r"(?<![A-Za-z])isso(?![A-Za-z0-9])"), "retail-ops"),
]
WORD_SUBS = [
    (re.compile(r"\b[Aa]n ISSO\b(?![_-])"), lambda m: ("A" if m.group(0)[0] == "A" else "a") + " Retail Ops"),
    (re.compile(r"\bISSO-minted\b"), "solution-minted"),
    (re.compile(r"\bISSO\b(?![_-])"), "Retail Ops"),
]
MERMAID = re.compile(r"(```mermaid\n)(.*?)(```)", re.S)


def protected_line(rel: str, line: str) -> bool:
    if rel == "scripts/seed/generate/validate.py" and ("RETIRED_TOKENS" in line or "BR1.3" in line):
        return True
    if rel == MAIN_SPEC:
        s = line.lstrip()
        return (s.startswith("| 2026-09") or s.startswith("| Version")
                or s.startswith("12. ~~Package name"))
    return False


def convert(rel: str, text: str) -> str:
    # Mermaid node ids first: "ISSO" there is an identifier, not prose.
    text = MERMAID.sub(lambda m: m.group(1) + re.sub(r"\bISSO\b(?![_-])", "RETAIL_OPS", m.group(2)) + m.group(3), text)
    out = []
    for line in text.split("\n"):
        if protected_line(rel, line):
            out.append(line)
            continue
        for pat, rep in SUBS + WORD_SUBS:
            line = pat.sub(rep, line)
        out.append(line)
    return "\n".join(out)


def tracked_files() -> list[str]:
    res = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [f for f in res.stdout.split("\n") if f]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    changed = 0
    for rel in tracked_files():
        if rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES) or Path(rel).suffix.lower() in BINARY_SUFFIXES:
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            old = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new = convert(rel, old)
        if new != old:
            changed += 1
            print(f"  {'would update' if args.dry_run else 'updated'} {rel}")
            if not args.dry_run:
                p.write_text(new, encoding="utf-8")
    for src, dst in FILE_RENAMES.items():
        if (ROOT / src).exists():
            print(f"  {'would rename' if args.dry_run else 'renamed'} {src} -> {dst}")
            if not args.dry_run:
                subprocess.run(["git", "mv", src, dst], cwd=ROOT, check=True)
    print(f"{'[dry-run] ' if args.dry_run else ''}{changed} file(s) {'to update' if args.dry_run else 'updated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
