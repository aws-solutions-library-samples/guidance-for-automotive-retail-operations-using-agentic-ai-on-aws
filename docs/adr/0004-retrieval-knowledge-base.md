# ADR 0004 — Retrieval through a Bedrock Knowledge Base over the corpus in S3

**Status:** Accepted (2026-10-08). Deployed as `retail-ops-dev-quick-index` (538 documents).

## Context

The agents ground answers in a corpus of 538 synthetic documents (CRM notes,
templates, transcripts, objection strategies, bulletins, prior plans, product
guides, repair procedures, TSBs). Until this decision, retrieval was a keyword
scan over DynamoDB rows — deterministic and cited, but weak: it matched on
token overlap and returned truncated text. The same documents were already
exported to S3 (`quick-index/<kind>/<market>/<doc>.md`) for the AWS Quick
knowledge bases, each with a `source_ref` front-matter line.

## Decision

Build an **Amazon Bedrock Knowledge Base** over that S3 prefix and make it the
retrieval engine whenever the application runs in AWS mode, keeping the keyword
index as the automatic fallback. One corpus therefore serves both the agents and
the AWS Quick side.

* Embeddings: Amazon Titan Text Embeddings v2 (1024 dimensions).
* Vector store: **S3 Vectors** — a vector bucket and index with no always-on
  cluster, sized for a corpus of hundreds to tens of thousands of documents.
* Chunking: fixed size, 400 tokens, 15 % overlap (the documents are short).
* Metadata: one `<doc>.md.metadata.json` sidecar per document with `kind`,
  `market` and `collection_id`, derived from the S3 key, so `Retrieve` can
  filter by collection and by market.
* Citations: each chunk's `source_ref:` line is parsed back out so a passage
  cites the original record, not just an S3 URI.

## Options considered

1. **Keep the keyword index.** No new services, but low recall and no semantic
   matching — the weakest link in answer quality.
2. **OpenSearch Serverless as the vector store.** Standard and powerful, but an
   always-on collection is the wrong cost shape for a reference build.
3. **AWS Quick Index as the retrieval API.** Quick Index is consumed through a
   Quick user session, not a service principal; the agents are a service.
4. **Bedrock Knowledge Base on S3 Vectors** *(chosen)*. Managed ingestion and
   retrieval, metadata filtering, pay-per-use storage, and the corpus is already
   in S3.

## Implementation

```
app/infra/deploy_knowledge_base.py   role, S3 Vectors bucket + index, KB, data source, --sidecars, --ingest, --query
app/infra/knowledge-base-config.json {knowledge_base_id, data_source_id, vector_bucket, index, corpus_uri}
app/services/platform/knowledge.py   BedrockKnowledgeBase (Retrieve + filters + source_ref parse),
                                     _FallbackKnowledgeBase, get_knowledge_base()
app/infra/iam_roles.py               bedrock:Retrieve on knowledge-base/*
app/infra/deploy_quick_index.py      the corpus export the KB indexes (deploy.sh stage "corpus")
tests/app/test_guardrails_and_knowledge.py
```

Selection rule: `get_knowledge_base()` returns the Bedrock implementation when
`INTEGRATION_MODE=aws` **and** a knowledge base id is configured
(`RETAIL_OPS_KNOWLEDGE_BASE_ID` or the config file); otherwise the local index. The
Bedrock path falls back to the local index on any error or empty result and
reports which engine answered (`engine: bedrock_kb | local_keyword (...)`).

Callers: the A3 assistant (one-shot and agent), the A6 workspace `ask`, the
A7 finding explanation, and `POST /knowledge/retrieve` (optionally synthesised
into an answer by `generate.ground`).

## Consequences

* Answers cite real documents by `source_ref`; the A3 agent's `search_knowledge`
  tool filters by the caller's market.
* Re-indexing after a corpus change is `deploy_knowledge_base.py --apply
  --sidecars --ingest` (a few minutes for 538 documents).
* The grounding filter in ADR 0003 scores answers against these passages;
  short passages can score low, which is why grounding-only interventions fall
  back to the passages themselves.
* Cost: Titan embeddings at ingestion (cents for this corpus), S3 Vectors
  storage and queries pay-per-use, no idle cluster.

## Revisit when

* The corpus grows past what fixed-size chunking serves well (consider semantic
  chunking, or a per-kind data source).
* Sub-second retrieval latency becomes a requirement (consider OpenSearch).
