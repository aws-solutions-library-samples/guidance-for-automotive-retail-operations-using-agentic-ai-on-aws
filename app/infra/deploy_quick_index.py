#!/usr/bin/env python3
"""Export the Quick Index corpus to S3 for AWS Quick knowledge bases
(docs/QUICK_BUILD.md step 2a).

The 538 `PLAT_KnowledgeCollections` rows carry only pointers (`body_ref =
corpus:<doc_id>`); the body text lives in the generator's local corpus under
`sample-data/synthetic/<market>/quick_index/KC-<MARKET>-<kind>.ndjson` (each line
has title + body + source_ref). This exporter rebuilds each document and writes
it in the exact layout the checklist's knowledge bases expect:

    s3://<lake-bucket>/quick-index/<kind>/<market_id>/<doc_id>.md

with the checklist's front-matter:

    # <title>
    source_ref: <source_ref>
    collection_id: <collection_id>
    market: <market_id>

    <body>

PII masking: synthetic bodies use reserved-domain emails and fictional numbers by
design (plan §1.7), but as a defensive measure this masks any email and phone-
shaped strings on export so the C3/C4 redaction path is exercised.

Usage:
  python3 -m app.infra.deploy_quick_index            # dry-run (counts)
  python3 -m app.infra.deploy_quick_index --apply    # write 538 .md to S3
  python3 -m app.infra.deploy_quick_index --status   # count objects under quick-index/
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
LAKE_CONFIG = naming.config_path("lake")
SYN_DIR = ROOT / "sample-data" / "synthetic"
MARKETS = ("us", "de")
S3_PREFIX = "quick-index"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")


def _bucket() -> str:
    if LAKE_CONFIG.exists():
        b = json.loads(LAKE_CONFIG.read_text()).get("bucket")
        if b:
            return b
    import boto3
    acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    return naming.name(f"event-lake-{acct}")


def _mask(text: str) -> str:
    """Defensive PII masking of email + phone-shaped strings (keeps names, which
    are synthetic by design)."""
    text = _EMAIL.sub("[email-redacted]", text)
    text = _PHONE.sub("[phone-redacted]", text)
    return text


def _docs() -> list[dict]:
    """Every corpus doc across markets/kinds from the local generator export."""
    out: list[dict] = []
    for market in MARKETS:
        qdir = SYN_DIR / market / "quick_index"
        if not qdir.is_dir():
            continue
        for f in sorted(qdir.glob("KC-*.ndjson")):
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("doc_id") and d.get("body") is not None:
                    out.append(d)
    return out


def _markdown(d: dict) -> str:
    body = _mask(str(d.get("body") or ""))
    return (f"# {d.get('title') or d['doc_id']}\n"
            f"source_ref: {d.get('source_ref') or ''}\n"
            f"collection_id: {d.get('collection_id') or ''}\n"
            f"market: {d.get('market_id') or ''}\n\n"
            f"{body}\n")


def _key(d: dict) -> str:
    return f"{S3_PREFIX}/{d['kind']}/{d['market_id']}/{d['doc_id']}.md"


def run(apply: bool) -> dict[str, int]:
    docs = _docs()
    by_kind: dict[str, int] = {}
    for d in docs:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
    print(f"  corpus docs found: {len(docs)}")
    for k in sorted(by_kind):
        print(f"    {k}: {by_kind[k]}")
    if apply:
        import boto3
        s3 = boto3.client("s3", region_name=REGION)
        bucket = _bucket()
        n = 0
        for d in docs:
            s3.put_object(Bucket=bucket, Key=_key(d),
                          Body=_markdown(d).encode("utf-8"),
                          ContentType="text/markdown")
            n += 1
        print(f"  [s3] wrote {n} markdown docs under s3://{bucket}/{S3_PREFIX}/")
    else:
        print(f"  [dry-run] would write {len(docs)} docs under s3://{_bucket()}/{S3_PREFIX}/")
    return by_kind


def status() -> None:
    import boto3
    s3 = boto3.client("s3", region_name=REGION)
    bucket = _bucket()
    paginator = s3.get_paginator("list_objects_v2")
    counts: dict[str, int] = {}
    total = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{S3_PREFIX}/"):
        for o in page.get("Contents", []):
            if o["Key"].endswith(".md"):
                kind = o["Key"].split("/")[1] if len(o["Key"].split("/")) > 1 else "?"
                counts[kind] = counts.get(kind, 0) + 1
                total += 1
    print(f"s3://{bucket}/{S3_PREFIX}/ : {total} markdown docs")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export Quick Index corpus to S3 for Quick knowledge bases")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(); return
    if not SYN_DIR.exists():
        raise SystemExit(f"ERROR: {SYN_DIR} not found — generate the synthetic corpus first.")
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] export Quick Index corpus to S3")
    by_kind = run(args.apply)
    print(f"[{mode}] done. {sum(by_kind.values())} docs across {len(by_kind)} kinds.")


if __name__ == "__main__":
    main()
