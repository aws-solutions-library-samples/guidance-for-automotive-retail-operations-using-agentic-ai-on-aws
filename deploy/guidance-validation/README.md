# Guidance validation in AWS CodeBuild

The AWS Guidance team validates published sample code by running its one-click
deployment script in a fresh AWS account, in AWS CodeBuild. This folder runs
[`deploy/guidance-deploy.sh`](../guidance-deploy.sh) the same way:

| | |
|---|---|
| Environment type | Linux container, **privileged** mode on |
| Image | `aws/codebuild/amazonlinux-x86_64-standard:5.0` (Amazon Linux 2023, x86_64) |
| Compute | `BUILD_GENERAL1_MEDIUM` or larger |
| Timeout | 120 minutes (a project setting) |
| Buildspec | [`buildspec.yml`](buildspec.yml): selects Python 3.12 and Node.js 20, then runs `deploy/guidance-deploy.sh` |
| Service role | CodeBuild trust, with the AWS managed `AdministratorAccess` policy (below) |

The build prints `GUIDANCE_ENV_NAME=<env>` at the start and at the end (also on
failure). Keep it: the stack is torn down by that name.

**Why AdministratorAccess.** The deployment creates IAM roles and policies and
resources in about twenty services (Lambda, DynamoDB, API Gateway, Cognito,
Bedrock, Bedrock AgentCore, S3 Vectors, S3, CloudFront, WAF, EventBridge,
Scheduler, Step Functions, SQS, Firehose, Glue, Athena, Secrets Manager,
CloudWatch, AWS Marketplace subscriptions for the models). A scoped policy that
covers all of it, including `iam:CreateRole` and `iam:PutRolePolicy`, is
administrator access in practice, so the kit uses `AdministratorAccess` openly.
Use a dedicated validation account and delete the role afterwards.

## Pre-publication mode (from a local clone)

Before the public GitHub repository exists, `run-validation.sh` runs the same
build from a snapshot of the committed `HEAD`. Run it from a clone of this
repository with credentials for the validation account (for example
`AWS_PROFILE`); it refuses to run against any other account.

```bash
deploy/guidance-validation/run-validation.sh run --account <account-id>
```

It:

1. checks that `aws sts get-caller-identity` returns `<account-id>`;
2. creates the bucket `guidance-val-<account-id>-<yyyymmdd>` (public access
   blocked, bucket-owner-enforced, SSE-S3 encryption, a policy that denies
   non-TLS requests);
3. uploads `source.zip`: a `git bundle` of the current branch at `HEAD`, plus
   `deploy/guidance-deploy.sh` and this `buildspec.yml` as committed (uncommitted
   changes are not included; the script says so);
4. creates or updates the service role `guidance-validation-retail-ops-role`
   (trust `codebuild.amazonaws.com`, limited to this account, with
   `AdministratorAccess`);
5. creates or updates the project `guidance-validation-retail-ops` with the
   settings above, source = that zip in S3, and the environment variables
   `REPO_URL=repo.bundle` (the buildspec resolves it inside the source
   directory) and `REPO_BRANCH=<current branch>`;
6. starts the build and prints the build id, the log group
   (`/aws/codebuild/guidance-validation-retail-ops`) and the log stream.

```bash
aws logs tail /aws/codebuild/guidance-validation-retail-ops --log-stream-names <stream> --follow
deploy/guidance-validation/run-validation.sh status --account <account-id> --build <build-id>
```

`status` shows the build status, any phase that did not succeed, and the
environment name the build deployed, with its teardown command.

## Published mode (from GitHub)

Once the repository is public, the project can use it directly as the source;
the script then clones the default `REPO_URL` itself. Replace the placeholders:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws iam create-role --role-name guidance-validation-retail-ops-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"'"$ACCOUNT_ID"'"}}}]}'
aws iam attach-role-policy --role-name guidance-validation-retail-ops-role \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
aws codebuild create-project --name guidance-validation-retail-ops \
  --source type=GITHUB,location=https://github.com/aws-solutions-library-samples/guidance-for-automotive-retail-operations-using-agentic-ai-on-aws.git,buildspec=deploy/guidance-validation/buildspec.yml \
  --artifacts type=NO_ARTIFACTS \
  --environment type=LINUX_CONTAINER,image=aws/codebuild/amazonlinux-x86_64-standard:5.0,computeType=BUILD_GENERAL1_MEDIUM,privilegedMode=true \
  --service-role "arn:aws:iam::${ACCOUNT_ID}:role/guidance-validation-retail-ops-role" \
  --timeout-in-minutes 120 \
  --logs-config 'cloudWatchLogs={status=ENABLED,groupName=/aws/codebuild/guidance-validation-retail-ops}'
aws codebuild start-build --project-name guidance-validation-retail-ops --query build.id --output text
```

## Clean up

First remove the deployed stack, from a clone with `.venv` set up (README
"Deploy the Guidance", steps 2-3) and the same credentials. Review the dry run,
then confirm:

```bash
deploy/destroy.sh --env <env>
deploy/destroy.sh --env <env> --confirm --account <account-id> --include-data
```

Then the CodeBuild side (project, service role, snapshot buckets). Without
`--yes` it only lists what it would delete, and it prints the teardown command
for every environment name it finds in the build logs:

```bash
deploy/guidance-validation/run-validation.sh cleanup --account <account-id>
deploy/guidance-validation/run-validation.sh cleanup --account <account-id> --yes
```

The log group `/aws/codebuild/guidance-validation-retail-ops` is kept as build
evidence; delete it with `aws logs delete-log-group` when it is no longer
needed. In published mode, delete the project and role with
`aws codebuild delete-project` and `aws iam detach-role-policy` /
`aws iam delete-role`.
