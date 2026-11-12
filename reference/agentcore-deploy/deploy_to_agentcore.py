#!/usr/bin/env python3
"""
Deploy the Vehicle Recommendation Agent to Bedrock AgentCore Runtime.

Usage:
    python deploy/deploy_to_agentcore.py [--region us-east-1] [--skip-docker]
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import shutil
from pathlib import Path

import boto3

_DOCKER = shutil.which("docker") or "/Applications/Docker.app/Contents/Resources/bin/docker"
ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT_DIR / "deploy" / "deployed_config.json"

AGENT_NAME = "vehicle_recommendation_agent"
ECR_REPO_NAME = "vehicle-recommendation-agent"
ROLE_NAME = "SalesProspectingAgentRole"  # Shared role with sufficient permissions
CUSTOMERS_TABLE = "SalesAdvisor_Customers_dev"
INVENTORY_TABLE = "SalesAdvisor_Inventory_dev"
RECOMMENDATIONS_TABLE = "SalesAdvisor_Recommendations_dev"
INTERACTIONS_TABLE = "SalesAdvisor_Interactions_dev"
OUTCOMES_TABLE = "SalesAdvisor_Outcomes_dev"


def _client(service, region):
    return boto3.client(service, region_name=region)


def print_step(msg):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def print_ok(msg):
    print(f"  [OK] {msg}")


def get_identity(region):
    sts = _client("sts", region)
    identity = sts.get_caller_identity()
    account_id = identity["Account"]
    print_ok(f"Account: {account_id}, Region: {region}")
    return account_id


def ensure_ecr(region):
    ecr = _client("ecr", region)
    try:
        resp = ecr.create_repository(
            repositoryName=ECR_REPO_NAME,
            imageScanningConfiguration={"scanOnPush": True},
        )
        uri = resp["repository"]["repositoryUri"]
    except ecr.exceptions.RepositoryAlreadyExistsException:
        resp = ecr.describe_repositories(repositoryNames=[ECR_REPO_NAME])
        uri = resp["repositories"][0]["repositoryUri"]
    print_ok(f"ECR: {uri}")
    return uri


def build_and_push(ecr_uri, region):
    ecr = _client("ecr", region)
    registry = ecr_uri.split("/")[0]
    image_uri = f"{ecr_uri}:latest"

    token_resp = ecr.get_authorization_token()
    raw_token = token_resp["authorizationData"][0]["authorizationToken"]
    ecr_password = base64.b64decode(raw_token).decode().split(":", 1)[1]

    print("  Building Docker image...")
    subprocess.run([_DOCKER, "build", "-t", image_uri, str(ROOT_DIR)], check=True)

    print("  Logging in to ECR...")
    subprocess.run(
        [_DOCKER, "login", "--username", "AWS", "--password-stdin", registry],
        input=ecr_password.encode(), check=True,
    )

    print("  Pushing to ECR...")
    subprocess.run([_DOCKER, "push", image_uri], check=True)
    print_ok(f"Image: {image_uri}")
    return image_uri


def ensure_iam_role(region, account_id):
    """Reuse the shared SalesProspectingAgentRole (already has DynamoDB + Bedrock permissions)."""
    iam = _client("iam", region)
    try:
        role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print_ok(f"Using existing IAM Role: {role_arn}")
        return role_arn
    except iam.exceptions.NoSuchEntityException:
        print(f"  [ERROR] Role {ROLE_NAME} not found. Deploy prospecting agent first.")
        sys.exit(1)


def ensure_recommendations_table(region):
    """Create Recommendations table if it doesn't exist."""
    client = _client("dynamodb", region)
    try:
        client.describe_table(TableName=RECOMMENDATIONS_TABLE)
        print_ok(f"Table {RECOMMENDATIONS_TABLE} exists")
    except client.exceptions.ResourceNotFoundException:
        print(f"  Creating table {RECOMMENDATIONS_TABLE}...")
        client.create_table(
            TableName=RECOMMENDATIONS_TABLE,
            KeySchema=[{"AttributeName": "recommendation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "recommendation_id", "AttributeType": "S"},
                {"AttributeName": "customer_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "CustomerIndex",
                "KeySchema": [{"AttributeName": "customer_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=RECOMMENDATIONS_TABLE)
        print_ok(f"Created {RECOMMENDATIONS_TABLE}")


def deploy_runtime(region, image_uri, role_arn):
    ctrl = _client("bedrock-agentcore-control", region)

    env_vars = {
        "AWS_REGION": region,
        "CUSTOMERS_TABLE": CUSTOMERS_TABLE,
        "INVENTORY_TABLE": INVENTORY_TABLE,
        "RECOMMENDATIONS_TABLE": RECOMMENDATIONS_TABLE,
        "INTERACTIONS_TABLE": INTERACTIONS_TABLE,
        "OUTCOMES_TABLE": OUTCOMES_TABLE,
        "BEDROCK_MODEL_ID": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    }

    # Check for existing runtime
    existing_id = None
    existing_arn = None
    resp = ctrl.list_agent_runtimes(maxResults=50)
    for rt in resp.get("agentRuntimes", []):
        if rt["agentRuntimeName"] == AGENT_NAME:
            existing_id = rt["agentRuntimeId"]
            existing_arn = rt["agentRuntimeArn"]
            break

    if existing_id:
        ctrl.update_agent_runtime(
            agentRuntimeId=existing_id,
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            environmentVariables=env_vars,
        )
        runtime_id = existing_id
        runtime_arn = existing_arn
        print_ok(f"Updated runtime: {runtime_id}")
    else:
        resp = ctrl.create_agent_runtime(
            agentRuntimeName=AGENT_NAME,
            description="Vehicle Recommendation Agent - Personalized vehicle matching (UC-6)",
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": "HTTP"},
            environmentVariables=env_vars,
        )
        runtime_id = resp["agentRuntimeId"]
        runtime_arn = resp["agentRuntimeArn"]
        print_ok(f"Created runtime: {runtime_id}")

    # Wait for READY
    print("  Waiting for runtime to become READY...")
    for attempt in range(40):
        detail = ctrl.get_agent_runtime(agentRuntimeId=runtime_id)
        status = detail.get("status", "")
        if status == "READY":
            print_ok("Runtime is READY")
            break
        if "FAIL" in status.upper():
            print(f"  [ERROR] Runtime failed: {status}")
            print(f"  Details: {json.dumps(detail, default=str, indent=2)}")
            sys.exit(1)
        print(f"    status={status} ({attempt+1}/40)...")
        time.sleep(15)

    # Ensure endpoint exists
    try:
        ep_list = ctrl.list_agent_runtime_endpoints(agentRuntimeId=runtime_id, maxResults=50)
        has_endpoint = any(ep.get("name") == "live" for ep in ep_list.get("runtimeEndpoints", []))
    except Exception:
        has_endpoint = False

    if not has_endpoint:
        ctrl.create_agent_runtime_endpoint(
            agentRuntimeId=runtime_id,
            name="live",
            description="Live endpoint for Vehicle Recommendation Agent",
        )
        print_ok("Created 'live' endpoint")

    return runtime_id, runtime_arn


def main():
    parser = argparse.ArgumentParser(description="Deploy Vehicle Recommendation Agent to AgentCore")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--skip-docker", action="store_true")
    args = parser.parse_args()

    region = args.region

    print_step("Step 1: AWS Identity")
    account_id = get_identity(region)

    print_step("Step 2: ECR Repository")
    ecr_uri = ensure_ecr(region)

    if not args.skip_docker:
        print_step("Step 3: Build & Push Docker Image")
        image_uri = build_and_push(ecr_uri, region)
    else:
        image_uri = f"{ecr_uri}:latest"
        print_ok(f"Skipping Docker — using {image_uri}")

    print_step("Step 4: IAM Role")
    role_arn = ensure_iam_role(region, account_id)

    print_step("Step 5: Create Recommendations Table")
    ensure_recommendations_table(region)

    print_step("Step 6: Deploy AgentCore Runtime")
    runtime_id, runtime_arn = deploy_runtime(region, image_uri, role_arn)

    # Save config
    deploy_config = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArn": runtime_arn,
        "region": region,
        "accountId": account_id,
        "ecrUri": ecr_uri,
        "roleArn": role_arn,
        "customersTable": CUSTOMERS_TABLE,
        "inventoryTable": INVENTORY_TABLE,
        "recommendationsTable": RECOMMENDATIONS_TABLE,
        "deployedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(deploy_config, f, indent=2)

    print(f"\n{'='*60}")
    print(f"  Deployment complete!")
    print(f"  Runtime ID  : {runtime_id}")
    print(f"  Runtime ARN : {runtime_arn}")
    print(f"  Config saved: {OUTPUT_FILE}")
    print(f"\n  Add to .env.local:")
    print(f"  NEXT_PUBLIC_RECOMMENDATION_AGENT_ARN={runtime_arn}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
