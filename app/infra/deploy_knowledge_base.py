#!/usr/bin/env python3
"""Amazon Bedrock Knowledge Base over the knowledge corpus (spec §2.4 AR-1, §3).

The 538 corpus documents already live in S3 as Markdown with a `source_ref`
front-matter line (deploy_quick_index.py →
s3://retail-ops-<env>-event-lake-<account>/quick-index/<kind>/<market>/<doc>.md). Until
this deployer, A3 / A6 / A7 grounding used a keyword scan over DynamoDB
(platform/knowledge.LocalKnowledgeBase). This stands up the managed retrieval
the spec names, so `knowledge.retrieve()` returns semantically ranked, cited
passages when INTEGRATION_MODE=aws:

  1. S3 Vectors bucket  retail-ops-<env>-kb-vectors-<account>  + index  retail-ops-quick-index
     (1024-dim cosine, Titan Text Embeddings v2). S3 Vectors is the low-cost
     vector store (no always-on cluster), right-sized for a 538-document corpus.
  2. IAM role  retail-ops-<env>-kb-role  (trust bedrock.amazonaws.com; read the corpus
     prefix, invoke the embedding model, read/write the vector index).
  3. Knowledge base  retail-ops-<env>-quick-index  (VECTOR, storage S3_VECTORS).
  4. Data source  quick-index  over the corpus prefix with fixed-size chunking.
     `--sidecars` writes one `<doc>.md.metadata.json` per document so retrieval
     can filter by `kind` / `market` / `collection_id` (derived from the key).
  5. `--ingest` starts an ingestion job and waits for it.

Writes app/infra/knowledge-base-config.json {knowledge_base_id, knowledge_base_arn,
data_source_id, vector_bucket, index, corpus_uri}; deploy_l4 / deploy_l6 /
deploy_agentcore carry `RETAIL_OPS_KNOWLEDGE_BASE_ID` onto the compute, and
platform/knowledge.get_knowledge_base() selects the Bedrock implementation.

Dry-run by default; --apply to create/update; --delete to tear down;
--query "<text>" to run a Retrieve against the deployed KB.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
KB_NAME = naming.name("quick-index")
DS_NAME = "quick-index"
ROLE = naming.name("kb-role")
INDEX = "retail-ops-quick-index"       # inside the env-named vector bucket
CORPUS_PREFIX = "quick-index/"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
EMBED_DIM = 1024
CONFIG_PATH = naming.config_path("knowledge-base")


def _lake_bucket(account: str) -> str:
    return naming.name(f"event-lake-{account}")


def _vec_bucket(account: str) -> str:
    return naming.name(f"kb-vectors-{account}")


def _role_policy(account: str) -> dict:
    lake = _lake_bucket(account)
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "Corpus", "Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
         "Resource": [f"arn:aws:s3:::{lake}", f"arn:aws:s3:::{lake}/*"],
         "Condition": {"StringEquals": {"aws:ResourceAccount": account}}},
        {"Sid": "Embed", "Effect": "Allow", "Action": ["bedrock:InvokeModel"],
         "Resource": [f"arn:aws:bedrock:{REGION}::foundation-model/{EMBED_MODEL}"]},
        {"Sid": "Vectors", "Effect": "Allow",
         "Action": ["s3vectors:GetIndex", "s3vectors:QueryVectors", "s3vectors:PutVectors",
                    "s3vectors:GetVectors", "s3vectors:DeleteVectors", "s3vectors:ListVectors"],
         "Resource": [f"arn:aws:s3vectors:{REGION}:{account}:bucket/{_vec_bucket(account)}/index/{INDEX}"]},
    ]}


def ensure_role(iam, account: str, apply: bool) -> str:
    arn = f"arn:aws:iam::{account}:role/{ROLE}"
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "bedrock.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account},
                      "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{REGION}:{account}:knowledge-base/*"}}}]}
    try:
        iam.get_role(RoleName=ROLE); exists = True
    except iam.exceptions.NoSuchEntityException:
        exists = False
    if not apply:
        print(f"  [role] {'exists' if exists else 'would create'} {ROLE}"); return arn
    if not exists:
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                        Description="Retail Ops Bedrock Knowledge Base service role")
        print(f"  [role] created {ROLE}")
    else:
        iam.update_assume_role_policy(RoleName=ROLE, PolicyDocument=json.dumps(trust))
    iam.put_role_policy(RoleName=ROLE, PolicyName="retail-ops-kb-inline", PolicyDocument=json.dumps(_role_policy(account)))
    if not exists:
        time.sleep(10)
    return arn


def ensure_vectors(s3v, account: str, apply: bool) -> tuple[str, str]:
    vb = _vec_bucket(account)
    vb_arn = f"arn:aws:s3vectors:{REGION}:{account}:bucket/{vb}"
    idx_arn = f"{vb_arn}/index/{INDEX}"
    if not apply:
        print(f"  [s3vectors] would ensure bucket {vb} + index {INDEX} ({EMBED_DIM}-dim cosine)"); return vb_arn, idx_arn
    try:
        s3v.get_vector_bucket(vectorBucketName=vb)
    except s3v.exceptions.NotFoundException:
        s3v.create_vector_bucket(vectorBucketName=vb); print(f"  [s3vectors] created bucket {vb}")
    try:
        s3v.get_index(vectorBucketName=vb, indexName=INDEX)
    except s3v.exceptions.NotFoundException:
        s3v.create_index(vectorBucketName=vb, indexName=INDEX, dataType="float32", dimension=EMBED_DIM,
                         distanceMetric="cosine",
                         # Bedrock stores the chunk text under AMAZON_BEDROCK_TEXT; it must be
                         # non-filterable (size). Our kind/market/collection_id stay filterable.
                         metadataConfiguration={"nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT"]})
        print(f"  [s3vectors] created index {INDEX}")
    return vb_arn, idx_arn


def write_sidecars(s3, account: str, apply: bool) -> int:
    """One `<doc>.md.metadata.json` per corpus document (kind/market/collection_id
    from the key) so Retrieve can filter by collection."""
    lake = _lake_bucket(account)
    paginator = s3.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=lake, Prefix=CORPUS_PREFIX):
        for o in page.get("Contents", []):
            key = o["Key"]
            if not key.endswith(".md"):
                continue
            parts = key[len(CORPUS_PREFIX):].split("/")
            if len(parts) < 3:
                continue
            kind, market = parts[0], parts[1]
            meta = {"metadataAttributes": {"kind": kind, "market": market,
                                           "collection_id": f"KC-{market.upper()}-{kind}"}}
            if apply:
                s3.put_object(Bucket=lake, Key=key + ".metadata.json", Body=json.dumps(meta).encode(),
                              ContentType="application/json")
            n += 1
    print(f"  [sidecars] {'wrote' if apply else 'would write'} {n} metadata files")
    return n


def _find_kb(ba) -> dict | None:
    token = None
    while True:
        kw = {"nextToken": token} if token else {}
        page = ba.list_knowledge_bases(**kw)
        for kb in page.get("knowledgeBaseSummaries", []):
            if kb["name"] == KB_NAME:
                return kb
        token = page.get("nextToken")
        if not token:
            return None


def ensure_kb(ba, account: str, role_arn: str, vb_arn: str, idx_arn: str, apply: bool) -> dict:
    existing = _find_kb(ba) if apply else None
    cfg = {
        "name": KB_NAME,
        "description": "Intelligent Sales & Service Orchestration knowledge corpus: CRM notes, templates, "
                       "transcripts, objection strategies, bulletins, prior plans, product guides, "
                       "repair procedures, TSBs (synthetic, cited).",
        "roleArn": role_arn,
        "knowledgeBaseConfiguration": {"type": "VECTOR", "vectorKnowledgeBaseConfiguration": {
            "embeddingModelArn": f"arn:aws:bedrock:{REGION}::foundation-model/{EMBED_MODEL}",
            "embeddingModelConfiguration": {"bedrockEmbeddingModelConfiguration": {
                "dimensions": EMBED_DIM, "embeddingDataType": "FLOAT32"}}}},
        "storageConfiguration": {"type": "S3_VECTORS", "s3VectorsConfiguration": {
            "vectorBucketArn": vb_arn, "indexArn": idx_arn}},
    }
    if not apply:
        print(f"  [kb] would ensure {KB_NAME} (S3_VECTORS, {EMBED_MODEL})"); return {"knowledgeBaseId": "(pending)"}
    if existing:
        kb = ba.update_knowledge_base(knowledgeBaseId=existing["knowledgeBaseId"], **cfg)["knowledgeBase"]
        print(f"  [kb] updated {KB_NAME} ({kb['knowledgeBaseId']})")
    else:
        kb = ba.create_knowledge_base(**cfg)["knowledgeBase"]
        print(f"  [kb] created {KB_NAME} ({kb['knowledgeBaseId']})")
    for _ in range(60):
        st = ba.get_knowledge_base(knowledgeBaseId=kb["knowledgeBaseId"])["knowledgeBase"]
        if st["status"] in ("ACTIVE",):
            return st
        if st["status"] in ("FAILED",):
            raise RuntimeError(f"knowledge base failed: {st.get('failureReasons')}")
        time.sleep(5)
    return kb


def ensure_data_source(ba, kb_id: str, account: str, apply: bool) -> str:
    lake = _lake_bucket(account)
    cfg = {
        "name": DS_NAME,
        "description": "quick-index/ corpus (Markdown with source_ref front-matter + metadata sidecars)",
        "dataSourceConfiguration": {"type": "S3", "s3Configuration": {
            "bucketArn": f"arn:aws:s3:::{lake}", "inclusionPrefixes": [CORPUS_PREFIX],
            "bucketOwnerAccountId": account}},
        "vectorIngestionConfiguration": {"chunkingConfiguration": {
            "chunkingStrategy": "FIXED_SIZE",
            "fixedSizeChunkingConfiguration": {"maxTokens": 400, "overlapPercentage": 15}}},
        "dataDeletionPolicy": "DELETE",
    }
    if not apply:
        print(f"  [datasource] would ensure {DS_NAME} over s3://{lake}/{CORPUS_PREFIX}"); return "(pending)"
    existing = next((d for d in ba.list_data_sources(knowledgeBaseId=kb_id).get("dataSourceSummaries", [])
                     if d["name"] == DS_NAME), None)
    if existing:
        ds = ba.update_data_source(knowledgeBaseId=kb_id, dataSourceId=existing["dataSourceId"], **cfg)["dataSource"]
        print(f"  [datasource] updated {DS_NAME} ({ds['dataSourceId']})")
    else:
        ds = ba.create_data_source(knowledgeBaseId=kb_id, **cfg)["dataSource"]
        print(f"  [datasource] created {DS_NAME} ({ds['dataSourceId']})")
    return ds["dataSourceId"]


def ingest(ba, kb_id: str, ds_id: str, wait: bool = True) -> dict:
    job = ba.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id,
                                 description="deploy_knowledge_base.py")["ingestionJob"]
    print(f"  [ingest] started {job['ingestionJobId']}")
    if not wait:
        return job
    for _ in range(120):
        job = ba.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id,
                                   ingestionJobId=job["ingestionJobId"])["ingestionJob"]
        if job["status"] in ("COMPLETE", "FAILED", "STOPPED"):
            break
        time.sleep(10)
    stats = job.get("statistics", {})
    print(f"  [ingest] {job['status']}: scanned={stats.get('numberOfDocumentsScanned')} "
          f"indexed={stats.get('numberOfNewDocumentsIndexed')} modified={stats.get('numberOfModifiedDocumentsIndexed')} "
          f"failed={stats.get('numberOfDocumentsFailed')}")
    if job.get("failureReasons"):
        print("  [ingest] failures:", job["failureReasons"][:3])
    return job


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the Bedrock Knowledge Base over the corpus.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--sidecars", action="store_true", help="(re)write per-document metadata sidecars")
    ap.add_argument("--ingest", action="store_true", help="start an ingestion job and wait")
    ap.add_argument("--query", metavar="TEXT", help="Retrieve TEXT against the deployed KB")
    ap.add_argument("--kind", help="with --query: filter by corpus kind (e.g. crm_notes)")
    args = ap.parse_args(argv)

    import boto3
    s = boto3.Session(region_name=REGION)
    account = s.client("sts").get_caller_identity()["Account"]
    iam, s3, s3v, ba = s.client("iam"), s.client("s3"), s.client("s3vectors"), s.client("bedrock-agent")
    print(f"account={account} region={REGION} kb={KB_NAME}")

    if args.query:
        cfg = load_config()
        if not cfg.get("knowledge_base_id"):
            print("no knowledge-base-config.json — deploy first"); return 2
        rt = s.client("bedrock-agent-runtime")
        vs: dict = {"numberOfResults": 5}
        if args.kind:
            vs["filter"] = {"equals": {"key": "kind", "value": args.kind}}
        out = rt.retrieve(knowledgeBaseId=cfg["knowledge_base_id"], retrievalQuery={"text": args.query},
                          retrievalConfiguration={"vectorSearchConfiguration": vs})
        for r in out.get("retrievalResults", []):
            print(f"- score={r.get('score'):.3f} {r['location'].get('s3Location', {}).get('uri')}")
            print("    " + r["content"]["text"][:200].replace("\n", " | "))
        return 0

    if args.delete:
        kb = _find_kb(ba)
        if kb:
            for d in ba.list_data_sources(knowledgeBaseId=kb["knowledgeBaseId"]).get("dataSourceSummaries", []):
                ba.delete_data_source(knowledgeBaseId=kb["knowledgeBaseId"], dataSourceId=d["dataSourceId"])
            ba.delete_knowledge_base(knowledgeBaseId=kb["knowledgeBaseId"]); print(f"  deleted KB {kb['knowledgeBaseId']}")
        vb = _vec_bucket(account)
        try:
            s3v.delete_index(vectorBucketName=vb, indexName=INDEX); s3v.delete_vector_bucket(vectorBucketName=vb)
            print(f"  deleted s3vectors {vb}/{INDEX}")
        except Exception as e:
            print(f"  s3vectors cleanup: {type(e).__name__}")
        try:
            for p in iam.list_role_policies(RoleName=ROLE)["PolicyNames"]:
                iam.delete_role_policy(RoleName=ROLE, PolicyName=p)
            iam.delete_role(RoleName=ROLE); print(f"  deleted role {ROLE}")
        except iam.exceptions.NoSuchEntityException:
            pass
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] deploying Bedrock Knowledge Base")
    role_arn = ensure_role(iam, account, args.apply)
    vb_arn, idx_arn = ensure_vectors(s3v, account, args.apply)
    if args.sidecars or not args.apply:
        write_sidecars(s3, account, args.apply and args.sidecars)
    kb = ensure_kb(ba, account, role_arn, vb_arn, idx_arn, args.apply)
    kb_id = kb["knowledgeBaseId"]
    ds_id = ensure_data_source(ba, kb_id, account, args.apply)
    if args.apply:
        cfg = {"knowledge_base_id": kb_id, "knowledge_base_arn": kb.get("knowledgeBaseArn"),
               "data_source_id": ds_id, "vector_bucket": _vec_bucket(account), "index": INDEX,
               "corpus_uri": f"s3://{_lake_bucket(account)}/{CORPUS_PREFIX}", "embedding_model": EMBED_MODEL,
               "region": REGION, "name": KB_NAME}
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"  wrote {CONFIG_PATH.relative_to(ROOT)}")
        if args.ingest:
            ingest(ba, kb_id, ds_id)
        else:
            print("  next: --apply --ingest to index the corpus; then redeploy l4 / l6 / agentcore")
    print(f"[{mode}] done." + ("" if args.apply else "  (re-run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
