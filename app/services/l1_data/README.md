# L1 — Data access / stores (real AWS DynamoDB)

The bottom application layer. Loads the synthetic-data artifacts produced by the
generator (`sample-data/synthetic/<market>/`) into **real AWS DynamoDB** tables so
the service layer (L3) can read the data through the same API the live system uses.

This is layer **L1** of the bottom-up build (see the dependency hierarchy):

```
L0  Data artifacts        ← generator output (DONE)
L1  Data access / stores  ← THIS: load artifacts into DynamoDB
L2  Domain model/contracts
L3  Service layer (Lambdas / read models)
...
```

## What it does

`ddb_load.py` reads a market's `manifest.json`, and for each `kind:"table"` artifact:

1. resolves the DynamoDB table name `<env-prefix><Owner>_<Table>` (default prefix `retail-ops-dev-`),
2. picks the partition key by inspecting the rows via the generator's authoritative
   `load_pk()` contract (reused by file-path import — the load contract cannot drift),
3. creates the table on-demand (`PAY_PER_REQUEST`) if absent, waits until ACTIVE,
4. batch-writes every row wrapped by the generator's `to_ddb_item()`.

The plain-JSON row artifacts remain the source of truth; this loader is the
load-time wrap only (contract C3).

## Safety

- **Dry-run by default.** Nothing is created or written without `--apply`.
- **No deletes.** The loader never deletes a table. `--recreate` is offered but
  refuses unless BOTH `--apply` and `--yes-delete` are passed, and even then it
  only touches tables under the given `--env-prefix`.
- **Env prefix** keeps dev tables namespaced so they never collide with anything.
- Intended for a **dev/sandbox account only**. Do not run against production.

## Usage

```bash
# Preview what would be created/written (no AWS writes):
python3 app/services/l1_data/ddb_load.py --market us --region us-east-1

# Actually create tables and load rows:
python3 app/services/l1_data/ddb_load.py --market us --region us-east-1 --apply

# Custom namespace prefix:
python3 app/services/l1_data/ddb_load.py --market us --apply --env-prefix retail-ops-dev-
```

Requires `boto3` and AWS credentials (default profile) with DynamoDB
create/write permissions in the target dev account.
