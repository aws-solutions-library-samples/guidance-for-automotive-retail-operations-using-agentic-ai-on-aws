# ADR 0007 — Idempotent Python deployers, dry-run first; direct code deployment for the agent runtime

**Status:** Accepted (2026-10-08).

## Context

The reference build needs to be stood up in a fresh AWS account by someone who
is not its author, torn down cleanly, and re-deployed many times a day during
development. The specification sketches a `deploy.sh` with stage flags and
Docker images for the agents. The team wanted no CloudFormation/CDK toolchain
and no container pipeline for a reference build.

## Decision

* **One Python deployer per stage**, written with boto3, **idempotent** (re-run
  updates in place), **dry-run by default** (`--apply` to change, `--delete` to
  tear down). Each writes a small JSON file under `app/infra/` with the handles
  later stages need (`cognito-config.json`, `gateway-config.json`,
  `guardrail-config.json`, `knowledge-base-config.json`,
  `agentcore-config.json`, `quick-config.json`, `console-config.json`).
* **`deploy/deploy.sh` orders the stages** and passes `--apply` through;
  `--only`/`--skip` select stages; every job the agent panel shows as scheduled
  ships enabled, and `--enable-jobs` also enables `autonomy-promotion`. `deploy/bootstrap.sh` wraps it for a brand-new account (preflight,
  identity confirmation, verify). `deploy/destroy.sh` reverses it.
* **Least-privilege IAM per Lambda** from one module (`iam_roles.py`): explicit
  table ARNs, only the actions used, and the Bedrock permissions each function
  needs (`InvokeModel`, `ApplyGuardrail` on the guardrail and the cross-region
  guardrail profile, `Retrieve`, `InvokeAgentRuntime`).
* **Agent runtimes by direct code deployment**, not a container: one runtime
  per custom agent (ADR 0009), created by the `agentcore` stage from the agent
  registry, A2 last. Each agent's own code plus vendored dependencies is zipped
  to S3 and runs on the managed Python 3.12 runtime. Dependencies are resolved as **Linux arm64 wheels**
  (`aarch64-manylinux2014`, binary-only) because the runtime validates every
  native binary's architecture; bytecode is stripped from the bundle.
* **Static console** (Next.js export) to a private S3 bucket behind CloudFront
  with Origin Access Control and a WAF web ACL; the build bakes in the current
  API base and Cognito client ids.

## Options considered

* **CloudFormation / CDK.** Standard, but adds a synthesis toolchain and makes
  the "discover what exists and reuse it" behaviour harder; the deployers are
  ~20 small scripts an engineer can read top to bottom.
* **Container image for the runtime.** Needed only if the bundle exceeds 250 MB
  or needs system packages; the Strands bundle is ~30 MB zipped.
* **Shared IAM role.** Simpler, but the spec requires least privilege and the
  acceptance suite checks for wildcard DynamoDB resources.

## Implementation

```
deploy/deploy.sh           ordered stages: cognito data events lake corpus guardrails kb l4 approvals l6 alarms agentcore gateway quick console waf
deploy/bootstrap.sh       fresh-account wrapper (preflight, --yes gate, JWT layer, deploy, verify)
deploy/verify.sh           post-deploy proof: smoke, acceptance, loop, agent eval, calibration, runtime
deploy/destroy.sh          reverse order; data only with --include-data
deploy/config.env.example  optional settings (region, IdP, WAF allowlist, models)
app/infra/*.py             one deployer per stage; iam_roles.py for roles; publish_jwt_layer.py optional
docs/DEPLOYMENT.md         the step-by-step guide
```

## Consequences

* Any stage can be re-run alone (`--only l4,console` after a code change).
* The REST API id and CloudFront URL are stable across redeploys; the agent
  runtime ARNs, guardrail version and knowledge base id flow to the Lambdas on the
  next `l4`/`l6`/`agentcore` run, which is why `deploy.sh` re-runs `l4` and `l6`
  after `agentcore` on `--apply`.
* `agentcore-config.json` uses schema version 2: an `agents` map with one entry
  per runtime (name, ARN, code hash), plus a `legacy` block while an old
  single runtime (`retail_ops_agent`) exists; the development account has none. `deploy_l4` and `deploy_l6` turn it into the
  `RETAIL_OPS_AGENT_RUNTIMES` map; a version 1 file is still read as A3's ARN.
* `deploy_agentcore --delete` and `--delete-legacy` list what they would remove
  and change nothing without `--apply`; with `--apply` they also need
  `--account` equal to the caller's account.
* Rollback for the agent runtimes is a Lambda setting, not a redeploy:
  `deploy_l4 --disable-agents <codes|all> --apply` (service Lambda) and
  `deploy_l6 --disable-agents <codes|all> --apply` (job Lambdas) make calls run
  in-process; `--enable-agents` reverses it. Both deployers preserve the
  setting across later runs.
* Deployment order matters where a stage reads another's config file; the
  guide lists it. Running a stage before its prerequisite is harmless (the
  deployer reports what is missing) but the wiring will be incomplete until
  both have run.
* No `demo`/`prod` environments exist yet; the `retail-ops-dev-` prefix is a
  constant in the deployers. Parameterising it is the first step to a second
  environment.

## Lessons recorded

* An AgentCore direct-code update fails silently (`UPDATE_FAILED`, no logs) when
  the zip contains x86_64 binaries; resolve wheels for `aarch64-manylinux2014`.
* STANDARD-tier guardrails evaluate through a regional guardrail profile; grant
  `bedrock:ApplyGuardrail` on `guardrail-profile/*` in every region.
* After an `l4 --apply`, API Gateway can return `AuthorizerConfigurationException`
  for a few seconds while the authorizer re-wires; retry before debugging.

## Revisit when

* A second environment or region is needed (parameterise the prefix and
  region; consider generating CloudFormation from the deployers).
* The runtime bundle approaches 250 MB (switch to a container image).
