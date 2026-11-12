# deploy/ — deployment scripts

Everything needed to stand the stack up, prove it works, and take it down.
The full guide with prerequisites, stage descriptions and troubleshooting is
[`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md); the reasoning is
[ADR 0007](../docs/adr/0007-deployment-approach.md).

| Script | Purpose | Safe default |
|---|---|---|
| `guidance-deploy.sh` | **One click, no input** (the AWS Guidance one-click deployment, Linux / CodeBuild): installs what the machine lacks, clones the repository, creates `.venv`, enables Bedrock model access with `enable-bedrock.py`, installs the console dependencies, generates the seed password, runs `bootstrap.sh --yes --skip quick` under a generated environment name, runs `verify.sh`, switches the development token off, and prints the console URL, the API base and the environment name. It follows "Deploy the Guidance" in the root [README](../README.md) step by step | deploys; nothing to confirm |
| `guidance-validation/` | CodeBuild kit for that script: `buildspec.yml`, and `run-validation.sh` (`run` / `status` / `cleanup`) for a pre-publication run from a git bundle. See its [README](guidance-validation/README.md) | `cleanup` lists only without `--yes` |
| `bootstrap.sh` | **Fresh account, one command.** Preflight (tools, credentials), shows the target account, publishes the optional JWT layer, runs `deploy.sh --apply`, loads extra markets, runs the smoke suite | dry run; `--yes` to deploy |
| `deploy.sh` | **The ordered stages.** 17 idempotent stages in dependency order; `--only`/`--skip` to pick; the agent-bar schedules ship enabled, `--enable-jobs` also turns on autonomy-promotion | dry run; `--apply` to change |
| `verify.sh` | **Prove it.** Smoke, acceptance, full write loop, demo workflow 7 end to end (one buyer, web lead to delivery, under each persona), agent eval through the API, guardrail calibration, Knowledge Base query, status of the six agent runtimes + schedules, a ping to each agent runtime | read-only except the self-cleaning loop and workflow 7 |
| `destroy.sh` | **Tear down** every resource the solution created for one environment (`--env <name>`, default `RETAIL_OPS_ENV` or `dev`) and naming stem (`--stem`, default `retail-ops`), dependents first | dry run; `--confirm --account <id>`; tables and the lake bucket only with `--include-data` |
| `config.env.example` | Optional environment settings (region, SSO provider, SSO Administrator allowlist, WAF allowlist, models) | copy to `config.env`, which is git-ignored |

The stages themselves are Python deployers in [`app/infra/`](../app/infra/)
(one per stage, boto3, dry-run by default, each with `--apply` / `--delete` and
usually `--status`). `deploy.sh` only orders them and passes flags through; you
can run any deployer alone.

## Environment name

Every resource is named from one environment name: `retail-ops-<env>-…`,
`retail_ops_<env>_…` and `app-bus-<env>` ([`app/infra/naming.py`](../app/infra/naming.py)).
Pass `--env <name>` to `bootstrap.sh`, `deploy.sh`, `verify.sh` and
`destroy.sh`, or export `RETAIL_OPS_ENV`; without either it is `dev`, which keeps
the names of the first deployments. Names are 2-8 lowercase letters and digits
without `aws`, `amazon` or `cognito`. The generated state files are per
environment too: `app/infra/<name>-config.json` for `dev`,
`app/infra/<name>-config.<env>.json` otherwise. The AWS Quick ids carry no
environment name, so one account holds one Quick deployment.

## Python

The scripts run the deployers with `$PYTHON`, else the active virtual
environment, else `./.venv`, else `python3`; it must be Python 3.12 or newer
with `boto3` (`python3.12 -m venv .venv && .venv/bin/pip install -r requirements-agentcore.txt`).
They run unattended (no prompts, no pager) on Linux and macOS, and stop at the
first failing stage with its name.

## Typical sessions

```bash
# one click (Linux; a fresh account; no input)
deploy/guidance-deploy.sh

# first time in an account
deploy/bootstrap.sh                      # dry run: tools + which account
deploy/bootstrap.sh --yes --enable-jobs  # deploy everything, autonomy-promotion on too
deploy/verify.sh                         # expect VERIFY: ALL PASS

# after a code change
deploy/deploy.sh --apply --only l4             # API / service code
deploy/deploy.sh --apply --only agentcore,l4,l6   # agent code (one runtime per agent) + re-wire
deploy/deploy.sh --apply --only console        # UI
deploy/deploy.sh --apply --only sso-admins     # after editing RETAIL_OPS_SSO_ADMINS in config.env
deploy/verify.sh --quick

# quiet every writer job until the next deploy (which re-enables the jobs the
# agent bar shows as scheduled) / also turn on autonomy-promotion
python3 -m app.infra.deploy_l6 --apply --disable-writers
python3 -m app.infra.deploy_l6 --apply --enable-all
# run a job now, for both markets (waits for the result)
python3 -m app.infra.deploy_l6 --run-once store-digest

# roll one agent back to in-process calls (merge-safe; survives redeploys)
python3 -m app.infra.deploy_l4 --disable-agents A2 --apply   # service Lambda
python3 -m app.infra.deploy_l6 --disable-agents A4 --apply   # that agent's job Lambdas
python3 -m app.infra.deploy_l4 --enable-agents all --apply   # undo

# switch the plain development tokens off (only Cognito sign-in), or back on
python3 -m app.infra.deploy_l4 --dev-token off --apply
python3 -m app.infra.deploy_l4 --dev-token on --apply

# a second, separately named environment in the same account
deploy/bootstrap.sh --yes --env g4f2a91 --skip quick
deploy/verify.sh --env g4f2a91

# remove everything (review the dry run first)
deploy/destroy.sh
deploy/destroy.sh --confirm --account <account-id> --include-data --include-quick-users
deploy/destroy.sh --env g4f2a91 --confirm --account <account-id> --include-data
```

## Stage order (what `deploy.sh` runs)

`cognito → data → sso-admins → events → lake → corpus → guardrails → kb → l4 → approvals →
l6 → alarms → agentcore (one runtime per agent, A2 last; then l4 and l6 again to
wire the runtime ARNs) → gateway → quick → console → waf`

Each deployer writes the handles later stages need to `app/infra/<name>-config.json`
(`<name>-config.<env>.json` for an environment other than `dev`).

`sso-admins` gives the Administrator role (`platform_admin`: every staff module,
tenant-wide) to the enterprise-IdP aliases in `RETAIL_OPS_SSO_ADMINS` by writing
their `PLAT_Users` rows, and removes rows for aliases no longer listed. It runs
after `data` because `cognito` runs before `PLAT_Users` exists. Any other SSO
sign-in is refused; see `docs/DEPLOYMENT.md` §8.2.

## What is not scripted

The `quick` stage deploys the AWS Quick content that has an API (datasets, topic,
knowledge bases, space, Field Sales chat agent, eight Flows, Campaign ROI dashboard,
and their sharing). Five steps have none and are done by hand: the MCP connector
`Retail Ops OEM Read Gateway`, a re-run of `--only quick` to link it, one test chat,
one Flow test run, and the District Roll-up schedule. The ordered list is the
"Console checklist" in `docs/QUICK_BUILD.md`. `destroy.sh` removes the Quick content
too (stage `quick_content`, dependents first).

## Conventions

* Every script is a dry run unless told otherwise, prints the account it is
  acting on, and is safe to re-run.
* The resource prefix comes from the environment name (`retail-ops-<env>-`, see
  above); the region `us-east-1` is a constant in the deployers.
* CI (`.gitlab-ci.yml`) runs the offline checks only (tests, eval, console
  lint); deployment is a developer or pipeline step with credentials.
