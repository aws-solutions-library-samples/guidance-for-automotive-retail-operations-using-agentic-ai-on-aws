#!/usr/bin/env python3
"""Delete every AWS resource this solution created for one naming stem.

DESTRUCTIVE when run with --confirm. Without it, the script only lists what it
found (a dry run) and exits 0.

Resources are discovered by NAME, not by local config files, so the script can
remove a stack deployed under an earlier stem (for example `--stem isso`, the
pre-2026-10-19 namespace) as well as the current `retail-ops` one. For stem S
and environment E it matches:

  S-E-*                       DynamoDB tables, Lambdas + layers, IAM roles, REST API,
                              schedules, alarms, Step Functions, SQS, Firehose, Athena
                              workgroup, secrets, Cognito pool, Bedrock guardrail and
                              knowledge base, AgentCore gateways, WAF, CloudFront
                              function + OACs, S3 buckets, log groups
  S_E_*  /  S_agent           Glue database, AgentCore memory, AgentCore runtime
                              (S with "-" replaced by "_")
  bedrock-agentcore-S-<acct>  AgentCore code bucket (bedrock-agentcore-S-E-<acct> when E is not dev)
  AmazonBedrockAgentCore-S-exec  AgentCore execution role (AmazonBedrockAgentCore-S-E-exec when
                              E is not dev; app/infra/naming.py)
  S-lake*, S-builders, S-oem-*   AWS Quick datasets, data source, groups
  S-lake-and-data-read        inline policy on the Quick service role
  S-space, S-field-sales-agent, S-kb-*, S-knowledge-s3, S-journey-funnel,
  S-campaign-roi-funnel, S-*-sql
                              AWS Quick content of deploy_quick_content (space,
                              chat agent, knowledge bases + S3 source, topic, analysis +
                              dashboard, custom-SQL datasets), plus its eight Flows and the
                              console-built MCP connector, matched by exact name
                              (`Retail Ops …`, current stem only); dependents first
  app-bus-E                   EventBridge bus (only rules named S-E-* are removed;
                              the bus is deleted when no rules remain)

E is --env, default RETAIL_OPS_ENV (else dev). The AWS Quick ids carry no
env, so the Quick stages run only for env dev unless --include-quick is passed.

Gates:
  --confirm --account <id>    required to delete; <id> must equal the caller's
                              account (guards against the wrong credentials).
  --include-data              also deletes the DynamoDB tables and the event-lake
                              bucket. Without it they are kept.
  --include-quick-users       also deletes Quick readers the app registered
                              (U-US-…, U-DE-…).
  Environments containing "prod" are refused unless --allow-prod is passed.
  A Cognito pool or DynamoDB table with deletion protection ON is skipped and
  reported; this script never switches protection off.

After a confirmed run, the local config files of that env that describe the
deleted stack (`app/infra/*-config.json` for dev, `*-config.<env>.json` otherwise) are moved to `backup/infra-config-<stem>-<timestamp>/` so the
next deploy starts clean.

Usage:
  python3 -m app.infra.teardown --stem isso                       # dry run
  python3 -m app.infra.teardown --stem <old-stem> --confirm --account <account-id>
  python3 -m app.infra.teardown --confirm --account <account-id> --include-data
  python3 -m app.infra.teardown --env g4f2a91                     # dry run of another env
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sys
import time
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "app" / "infra"
QUICK_STAGES = ("quick_content", "quick")
QUICK_SERVICE_ROLE_PREFIX = "aws-quicksight-service-role"


def runtime_names_for(stem: str, env: str) -> list[str]:
    """Every AgentCore runtime name this solution can create for a stem: the
    single runtime of the first deployments (`<stem>_agent`) and one runtime per
    agent from the agent registry (ADR 0009)."""
    from app.agentcore import registry
    us = stem.replace("-", "_")
    return [f"{us}_agent", *registry.runtime_names(stem_us=us, env=env)]


def select_runtime_names(all_names: list[str], stem: str, env: str) -> list[str]:
    """The runtimes to delete: exact matches of runtime_names_for(stem, env) only
    (no prefix or suffix match), so other runtimes in the account are never touched."""
    wanted = set(runtime_names_for(stem, env))
    return sorted({n for n in all_names if n in wanted})


class Teardown:
    def __init__(self, stem: str, env: str, region: str, confirm: bool, include_data: bool,
                 include_quick_users: bool, wait_cloudfront: int, include_quick: bool | None = None):
        import boto3
        self.boto3 = boto3
        self.stem, self.env, self.region = stem, env, region
        self.confirm, self.include_data, self.include_quick_users = confirm, include_data, include_quick_users
        self.wait_cloudfront = wait_cloudfront
        # AWS Quick ids carry no env (one Quick deployment per account), so only
        # the default env's teardown removes Quick content unless asked explicitly.
        self.include_quick = (env == naming.DEFAULT_ENV) if include_quick is None else include_quick
        self.s = boto3.Session(region_name=region)
        self.account = self.s.client("sts").get_caller_identity()["Account"]
        self.dash = f"{stem}-{env}-"
        self.us = stem.replace("-", "_")
        self.under = f"{self.us}_{env}_"
        self.found: dict[str, list[str]] = {}
        self.deleted = 0
        self.errors: list[str] = []
        self.skipped: list[str] = []

    # ---- helpers -------------------------------------------------------------
    def c(self, name: str, **kw):
        return self.s.client(name, **kw)

    def pages(self, client, op: str, key: str, **kw) -> list:
        out: list = []
        for page in client.get_paginator(op).paginate(**kw):
            out.extend(page.get(key, []))
        return out

    def note(self, category: str, names: list[str]) -> list[str]:
        names = sorted(set(names))
        self.found[category] = names
        return names

    def act(self, label: str, fn, *args, **kw) -> bool:
        if not self.confirm:
            return False
        try:
            fn(*args, **kw)
            self.deleted += 1
            print(f"    deleted {label}")
            return True
        except Exception as e:  # noqa: BLE001 - keep tearing down, report at the end
            name, text = type(e).__name__, str(e)
            if "NotFound" in name or name.startswith("NoSuch") or "does not exist" in text or " not found" in text:
                print(f"    already gone: {label}")
                return True
            if "linked to a service" in text:
                self.skipped.append(f"{label}: owned by its service, removed by AWS with it")
                print(f"    skipped (service-linked): {label}")
                return True
            msg = f"{label}: {name}: {text[:160]}"
            self.errors.append(msg)
            print(f"    ERROR {msg}")
            return False

    def section(self, title: str, names: list[str]) -> None:
        print(f"  {title:42s} {len(names):3d}" + (f"  {', '.join(names[:4])}{' …' if len(names) > 4 else ''}" if names else ""))

    # ---- stages (dependents first) ------------------------------------------
    def schedules(self):
        sch = self.c("scheduler")
        items = [x for x in self.pages(sch, "list_schedules", "Schedules") if x["Name"].startswith(self.dash)]
        self.section("EventBridge Scheduler schedules", self.note("schedules", [x["Name"] for x in items]))
        for x in items:
            self.act(f"schedule {x['Name']}", sch.delete_schedule, Name=x["Name"], GroupName=x.get("GroupName", "default"))

    def alarms(self):
        cw = self.c("cloudwatch")
        names = self.note("alarms", [a["AlarmName"] for a in self.pages(cw, "describe_alarms", "MetricAlarms")
                                     if a["AlarmName"].startswith(self.dash)])
        self.section("CloudWatch alarms", names)
        for i in range(0, len(names), 100):
            self.act(f"{len(names[i:i+100])} alarms", cw.delete_alarms, AlarmNames=names[i:i + 100])

    def cloudfront_and_waf(self):
        cf, waf = self.c("cloudfront"), self.c("wafv2", region_name="us-east-1")
        bucket = f"{self.dash}console-{self.account}"
        dists = [d for d in cf.list_distributions().get("DistributionList", {}).get("Items", [])
                 if any(o["DomainName"].startswith(bucket + ".") for o in d["Origins"]["Items"])]
        self.section("CloudFront distributions (console)", self.note("cloudfront", [d["Id"] for d in dists]))
        acls = [a for a in waf.list_web_acls(Scope="CLOUDFRONT")["WebACLs"] if a["Name"].startswith(self.dash)]
        ipsets = [i for i in waf.list_ip_sets(Scope="CLOUDFRONT")["IPSets"] if i["Name"].startswith(self.dash)]
        self.section("WAF web ACLs + IP sets", self.note("waf", [a["Name"] for a in acls] + [i["Name"] for i in ipsets]))
        # list_functions returns one entry per stage (DEVELOPMENT, LIVE); delete each function once
        funcs = list({f["Name"]: f for f in cf.list_functions().get("FunctionList", {}).get("Items", [])
                      if f["Name"].startswith(self.dash)}.values())
        oacs = [o for o in cf.list_origin_access_controls().get("OriginAccessControlList", {}).get("Items", [])
                if o["Name"].startswith(self.dash)]
        self.section("CloudFront functions + OACs", self.note("cloudfront-extras", [f["Name"] for f in funcs] + [o["Name"] for o in oacs]))
        if not self.confirm:
            return
        pending = []
        for d in dists:
            cfg = cf.get_distribution_config(Id=d["Id"]); conf, etag = cfg["DistributionConfig"], cfg["ETag"]
            if conf.get("Enabled") or conf.get("WebACLId"):
                conf["Enabled"] = False
                conf["WebACLId"] = ""
                self.act(f"(disable + detach WAF) distribution {d['Id']}", cf.update_distribution,
                         Id=d["Id"], IfMatch=etag, DistributionConfig=conf)
            pending.append(d["Id"])
        deadline = time.time() + self.wait_cloudfront
        for did in pending:
            while time.time() < deadline and cf.get_distribution(Id=did)["Distribution"]["Status"] != "Deployed":
                print(f"    waiting for distribution {did} to finish disabling …")
                time.sleep(30)
            if cf.get_distribution(Id=did)["Distribution"]["Status"] != "Deployed":
                self.skipped.append(f"CloudFront distribution {did} is still disabling; re-run the teardown in ~15 min to delete it")
                continue
            etag = cf.get_distribution(Id=did)["ETag"]
            self.act(f"distribution {did}", cf.delete_distribution, Id=did, IfMatch=etag)
        for a in acls:
            lock = waf.get_web_acl(Name=a["Name"], Scope="CLOUDFRONT", Id=a["Id"])["LockToken"]
            self.act(f"web ACL {a['Name']}", waf.delete_web_acl, Name=a["Name"], Scope="CLOUDFRONT", Id=a["Id"], LockToken=lock)
        for i in ipsets:
            lock = waf.get_ip_set(Name=i["Name"], Scope="CLOUDFRONT", Id=i["Id"])["LockToken"]
            self.act(f"IP set {i['Name']}", waf.delete_ip_set, Name=i["Name"], Scope="CLOUDFRONT", Id=i["Id"], LockToken=lock)
        for f in funcs:
            self.act(f"CloudFront function {f['Name']}",
                     lambda n=f["Name"]: cf.delete_function(Name=n, IfMatch=cf.describe_function(Name=n)["ETag"]))
        for o in oacs:
            self.act(f"OAC {o['Name']}",
                     lambda i=o["Id"]: cf.delete_origin_access_control(Id=i, IfMatch=cf.get_origin_access_control(Id=i)["ETag"]))

    def quick_content(self):
        """Quick content deploy_quick_content creates (Flows, chat agent, space,
        dashboard + analysis, topic, knowledge bases + their S3 source, custom-SQL
        datasets) and the console-built MCP connector, dependents first. Ids are
        `S-…`; the Flows and the connector are matched by their exact names, for
        the current stem only."""
        from app.infra import deploy_quick_content as dqc
        qs = self.c("quicksight")
        items = dqc.inventory(qs, self.account, self.stem)
        self.section("Quick content (Flows, agent, space, KBs …)",
                     self.note("quick-content", [f"{k}:{i}" for k, i, _, _ in items]))
        for kind, rid, label, kw in items:
            self.act(f"Quick {kind} {rid} ({label})", getattr(qs, dqc.DELETE_CALL[kind]), **kw)

    def quick(self):
        qs = self.c("quicksight"); acct = self.account
        ds = [d["DataSetId"] for d in self.pages(qs, "list_data_sets", "DataSetSummaries", AwsAccountId=acct)
              if d["DataSetId"].startswith(f"{self.stem}-lake")]
        src = [d["DataSourceId"] for d in qs.list_data_sources(AwsAccountId=acct)["DataSources"]
               if d["DataSourceId"].startswith(f"{self.stem}-lake")]
        groups = [g["GroupName"] for g in qs.list_groups(AwsAccountId=acct, Namespace="default")["GroupList"]
                  if g["GroupName"] == f"{self.stem}-builders" or g["GroupName"].startswith(f"{self.stem}-oem-")]
        users = [u["UserName"] for u in qs.list_users(AwsAccountId=acct, Namespace="default")["UserList"]
                 if re.fullmatch(r"U-(US|DE)-\d{5}", u["UserName"])] if self.include_quick_users else []
        self.section("Quick datasets", self.note("quick-datasets", ds))
        self.section("Quick data sources", self.note("quick-sources", src))
        self.section("Quick groups", self.note("quick-groups", groups))
        if self.include_quick_users:
            self.section("Quick readers registered by the app", self.note("quick-users", users))
        iam = self.c("iam"); policy = f"{self.stem}-lake-and-data-read"; inline = []
        for r in self.pages(iam, "list_roles", "Roles"):
            if r["RoleName"].startswith(QUICK_SERVICE_ROLE_PREFIX) and policy in iam.list_role_policies(RoleName=r["RoleName"])["PolicyNames"]:
                inline.append(f"{r['RoleName']}/{policy}")
        self.section("Quick service-role inline policy", self.note("quick-policy", inline))
        for d in ds:
            self.act(f"Quick dataset {d}", qs.delete_data_set, AwsAccountId=acct, DataSetId=d)
        for d in src:
            self.act(f"Quick data source {d}", qs.delete_data_source, AwsAccountId=acct, DataSourceId=d)
        for g in groups:
            self.act(f"Quick group {g}", qs.delete_group, AwsAccountId=acct, Namespace="default", GroupName=g)
        for u in users:
            self.act(f"Quick reader {u}", qs.delete_user, AwsAccountId=acct, Namespace="default", UserName=u)
        for rp in inline:
            role, pol = rp.split("/", 1)
            self.act(f"inline policy {rp}", iam.delete_role_policy, RoleName=role, PolicyName=pol)

    def agentcore(self):
        ac = self.c("bedrock-agentcore-control")
        from app.infra import deploy_agentcore
        gws = [g for g in ac.list_gateways()["items"] if g["name"].startswith(self.dash)]
        all_rts = deploy_agentcore.list_runtimes(ac)             # every page
        picked = set(select_runtime_names([r["agentRuntimeName"] for r in all_rts], self.stem, self.env))
        rts = [r for r in all_rts if r["agentRuntimeName"] in picked]
        rt_names = runtime_names_for(self.stem, self.env)
        mems = [m for m in ac.list_memories()["memories"] if m.get("id", "").startswith(f"{self.under}agent_memory")]
        self.section("AgentCore gateways", self.note("gateways", [g["gatewayId"] for g in gws]))
        self.section("AgentCore runtimes", self.note("runtimes", [r["agentRuntimeId"] for r in rts]))
        self.section("AgentCore memories (stored memories are lost)", self.note("memories", [m["id"] for m in mems]))
        try:
            wids = [w["name"] for w in self.pages(ac, "list_workload_identities", "workloadIdentities")
                    if w["name"].startswith(self.dash) or any(w["name"].startswith(f"{n}-") for n in rt_names)]
        except Exception:  # noqa: BLE001 - older SDKs
            wids = []
        self.section("AgentCore workload identities", self.note("workload-identities", wids))
        for g in gws:
            for t in ac.list_gateway_targets(gatewayIdentifier=g["gatewayId"]).get("items", []):
                self.act(f"gateway target {g['gatewayId']}/{t['targetId']}", ac.delete_gateway_target,
                         gatewayIdentifier=g["gatewayId"], targetId=t["targetId"])
            if self.confirm:
                for _ in range(20):
                    if not ac.list_gateway_targets(gatewayIdentifier=g["gatewayId"]).get("items"):
                        break
                    time.sleep(3)
            self.act(f"gateway {g['gatewayId']}", ac.delete_gateway, gatewayIdentifier=g["gatewayId"])
        for r in rts:
            self.act(f"runtime {r['agentRuntimeId']}", ac.delete_agent_runtime, agentRuntimeId=r["agentRuntimeId"])
        for m in mems:
            self.act(f"memory {m['id']}", ac.delete_memory, memoryId=m["id"])
        for w in wids:
            self.act(f"workload identity {w}", ac.delete_workload_identity, name=w)

    def bedrock(self):
        ba, br = self.c("bedrock-agent"), self.c("bedrock")
        kbs = [k for k in ba.list_knowledge_bases()["knowledgeBaseSummaries"] if k["name"].startswith(self.dash)]
        grs = [g for g in br.list_guardrails()["guardrails"] if g["name"].startswith(self.dash)]
        self.section("Bedrock knowledge bases", self.note("knowledge-bases", [k["knowledgeBaseId"] for k in kbs]))
        self.section("Bedrock guardrails", self.note("guardrails", [g["id"] for g in grs]))
        try:
            v = self.c("s3vectors")
            vbs = [b["vectorBucketName"] for b in v.list_vector_buckets().get("vectorBuckets", [])
                   if b["vectorBucketName"].startswith(self.dash)]
        except Exception:  # noqa: BLE001
            v, vbs = None, []
        self.section("S3 vector buckets", self.note("vector-buckets", vbs))
        for k in kbs:
            kid = k["knowledgeBaseId"]
            for d in ba.list_data_sources(knowledgeBaseId=kid).get("dataSourceSummaries", []):
                if self.confirm:
                    # RETAIN: the whole vector store is deleted below, so the data source must not
                    # try to clear vectors itself (it fails if the store goes first).
                    try:
                        ds = ba.get_data_source(knowledgeBaseId=kid, dataSourceId=d["dataSourceId"])["dataSource"]
                        upd = {k2: ds[k2] for k2 in ("knowledgeBaseId", "dataSourceId", "name", "dataSourceConfiguration",
                                                      "description", "vectorIngestionConfiguration",
                                                      "serverSideEncryptionConfiguration") if ds.get(k2)}
                        ba.update_data_source(**upd, dataDeletionPolicy="RETAIN")
                    except Exception as e:  # noqa: BLE001
                        print(f"    note: could not set RETAIN on {d['dataSourceId']}: {type(e).__name__}")
                self.act(f"KB data source {d['dataSourceId']}", ba.delete_data_source,
                         knowledgeBaseId=kid, dataSourceId=d["dataSourceId"])
            self.act(f"knowledge base {kid}", ba.delete_knowledge_base, knowledgeBaseId=kid)
        for g in grs:
            self.act(f"guardrail {g['id']}", br.delete_guardrail, guardrailIdentifier=g["id"])
        if v and self.confirm and kbs:
            for _ in range(60):   # the KB must be fully gone before its vector store is removed
                if not [k for k in ba.list_knowledge_bases()["knowledgeBaseSummaries"] if k["name"].startswith(self.dash)]:
                    break
                time.sleep(10)
            else:
                self.skipped.append("knowledge base still deleting; vector store kept — re-run the teardown to remove it")
                vbs = []
        for b in vbs:
            if v:
                for ix in v.list_indexes(vectorBucketName=b).get("indexes", []):
                    self.act(f"vector index {b}/{ix['indexName']}", v.delete_index, vectorBucketName=b, indexName=ix["indexName"])
                self.act(f"vector bucket {b}", v.delete_vector_bucket, vectorBucketName=b)

    def compute_and_api(self):
        agw, lam, sfn = self.c("apigateway"), self.c("lambda"), self.c("stepfunctions")
        apis = [a for a in agw.get_rest_apis(limit=500)["items"] if a["name"].startswith(self.dash)]
        fns = [f["FunctionName"] for f in self.pages(lam, "list_functions", "Functions") if f["FunctionName"].startswith(self.dash)]
        layers = [l["LayerName"] for l in self.pages(lam, "list_layers", "Layers") if l["LayerName"].startswith(self.dash)]
        sms = [m for m in sfn.list_state_machines()["stateMachines"] if m["name"].startswith(self.dash)]
        self.section("API Gateway REST APIs", self.note("rest-apis", [f"{a['name']} ({a['id']})" for a in apis]))
        self.section("Step Functions state machines", self.note("state-machines", [m["name"] for m in sms]))
        self.section("Lambda functions", self.note("lambdas", fns))
        self.section("Lambda layers (all versions)", self.note("layers", layers))
        for a in apis:
            self.act(f"REST API {a['name']}", agw.delete_rest_api, restApiId=a["id"])
            if self.confirm:
                time.sleep(31)   # DeleteRestApi is limited to one call per 30 s per account
        for m in sms:
            self.act(f"state machine {m['name']}", sfn.delete_state_machine, stateMachineArn=m["stateMachineArn"])
        for f in fns:
            self.act(f"Lambda {f}", lam.delete_function, FunctionName=f)
        for l in layers:
            for ver in self.pages(lam, "list_layer_versions", "LayerVersions", LayerName=l):
                self.act(f"layer {l}:{ver['Version']}", lam.delete_layer_version, LayerName=l, VersionNumber=ver["Version"])

    def events_and_streams(self):
        ev, sqs, fh = self.c("events"), self.c("sqs"), self.c("firehose")
        rules = []
        for b in ev.list_event_buses()["EventBuses"]:
            for r in self.pages(ev, "list_rules", "Rules", EventBusName=b["Name"]):
                if r["Name"].startswith(self.dash):
                    rules.append((b["Name"], r["Name"]))
        bus = f"app-bus-{self.env}"
        queues = [u for u in sqs.list_queues(QueueNamePrefix=self.dash).get("QueueUrls", [])]
        streams = [n for n in fh.list_delivery_streams(Limit=100)["DeliveryStreamNames"] if n.startswith(self.dash)]
        self.section("EventBridge rules", self.note("rules", [f"{b}/{r}" for b, r in rules]))
        self.section("SQS queues", self.note("queues", [u.rsplit("/", 1)[1] for u in queues]))
        self.section("Firehose streams", self.note("firehose", streams))
        for b, r in rules:
            targets = [t["Id"] for t in ev.list_targets_by_rule(Rule=r, EventBusName=b).get("Targets", [])]
            if targets:
                self.act(f"targets of rule {r}", ev.remove_targets, Rule=r, EventBusName=b, Ids=targets)
            self.act(f"rule {b}/{r}", ev.delete_rule, Name=r, EventBusName=b)
        buses = [x["Name"] for x in ev.list_event_buses()["EventBuses"]]
        if bus in buses:
            left = [r["Name"] for r in self.pages(ev, "list_rules", "Rules", EventBusName=bus)]
            remaining = [x for x in left if not x.startswith(self.dash)] if not self.confirm else left
            if remaining:
                self.skipped.append(f"event bus {bus} kept: it still has rules {remaining[:3]}")
                self.section("EventBridge bus (kept, other rules on it)", [])
            else:
                self.section("EventBridge bus", self.note("bus", [bus]))
                self.act(f"event bus {bus}", ev.delete_event_bus, Name=bus)
        for u in queues:
            self.act(f"queue {u.rsplit('/', 1)[1]}", sqs.delete_queue, QueueUrl=u)
        for n in streams:
            self.act(f"Firehose {n}", fh.delete_delivery_stream, DeliveryStreamName=n, AllowForceDelete=True)

    def lake_catalog(self):
        glue, ath = self.c("glue"), self.c("athena")
        dbs = [d["Name"] for d in self.pages(glue, "get_databases", "DatabaseList") if d["Name"].startswith(self.under)]
        wgs = [w["Name"] for w in ath.list_work_groups()["WorkGroups"] if w["Name"].startswith(self.dash)]
        self.section("Glue databases (and their tables)", self.note("glue", dbs))
        self.section("Athena workgroups", self.note("athena", wgs))
        for d in dbs:
            self.act(f"Glue database {d}", glue.delete_database, Name=d)
        for w in wgs:
            self.act(f"Athena workgroup {w}", ath.delete_work_group, WorkGroup=w, RecursiveDeleteOption=True)

    def secrets(self):
        sm = self.c("secretsmanager")
        names = [x["Name"] for x in self.pages(sm, "list_secrets", "SecretList") if x["Name"].startswith(self.dash)]
        self.section("Secrets (7-day recovery window)", self.note("secrets", names))
        for n in names:
            self.act(f"secret {n}", sm.delete_secret, SecretId=n, RecoveryWindowInDays=7)

    def cognito(self):
        cg = self.c("cognito-idp")
        pools = [p for p in cg.list_user_pools(MaxResults=60)["UserPools"] if p["Name"].startswith(self.dash)]
        self.section("Cognito user pools (users, clients, IdPs)", self.note("cognito", [f"{p['Name']} ({p['Id']})" for p in pools]))
        for p in pools:
            d = cg.describe_user_pool(UserPoolId=p["Id"])["UserPool"]
            if d.get("DeletionProtection") == "ACTIVE":
                self.skipped.append(f"Cognito pool {p['Id']} has deletion protection ON; switch it off yourself to delete it")
                continue
            for dom in filter(None, (d.get("Domain"), d.get("CustomDomain"))):
                self.act(f"Cognito domain {dom}", cg.delete_user_pool_domain, Domain=dom, UserPoolId=p["Id"])
            self.act(f"Cognito pool {p['Id']}", cg.delete_user_pool, UserPoolId=p["Id"])

    def iam_roles(self):
        iam = self.c("iam")
        exec_role = naming.agentcore_exec_role(self.env, stem=self.stem)
        roles = [r["RoleName"] for r in self.pages(iam, "list_roles", "Roles")
                 if r["RoleName"].startswith(self.dash) or r["RoleName"] == exec_role]
        self.section("IAM roles (with their policies)", self.note("roles", roles))
        for r in roles:
            if self.confirm:
                try:
                    for p in iam.list_attached_role_policies(RoleName=r)["AttachedPolicies"]:
                        iam.detach_role_policy(RoleName=r, PolicyArn=p["PolicyArn"])
                    for p in iam.list_role_policies(RoleName=r)["PolicyNames"]:
                        iam.delete_role_policy(RoleName=r, PolicyName=p)
                    for ip in iam.list_instance_profiles_for_role(RoleName=r)["InstanceProfiles"]:
                        iam.remove_role_from_instance_profile(InstanceProfileName=ip["InstanceProfileName"], RoleName=r)
                except Exception as e:  # noqa: BLE001
                    self.errors.append(f"policies of role {r}: {type(e).__name__}: {e}")
            self.act(f"IAM role {r}", iam.delete_role, RoleName=r)

    def buckets(self):
        s3 = self.c("s3")
        code = naming.agentcore_code_bucket(self.account, self.env, stem=self.stem)
        lake = f"{self.dash}event-lake-{self.account}"
        mine = [b["Name"] for b in s3.list_buckets()["Buckets"] if b["Name"].startswith(self.dash) or b["Name"] == code]
        keep = [] if self.include_data else [b for b in mine if b == lake]
        drop = [b for b in mine if b not in keep]
        self.section("S3 buckets (emptied, then deleted)", self.note("buckets", drop))
        if keep:
            self.section("S3 buckets kept (pass --include-data)", keep)
        for b in drop:
            if self.confirm:
                try:
                    bucket = self.boto3.resource("s3", region_name=self.region).Bucket(b)
                    bucket.object_versions.delete()
                    bucket.objects.delete()
                except Exception as e:  # noqa: BLE001
                    self.errors.append(f"emptying bucket {b}: {type(e).__name__}: {e}")
            self.act(f"bucket {b}", s3.delete_bucket, Bucket=b)

    def tables(self):
        ddb = self.c("dynamodb")
        names = [t for t in self.pages(ddb, "list_tables", "TableNames") if t.startswith(self.dash)]
        if not self.include_data:
            self.section("DynamoDB tables kept (pass --include-data)", names)
            return
        self.section("DynamoDB tables (ALL ROWS DELETED)", self.note("tables", names))
        for t in names:
            if self.confirm and ddb.describe_table(TableName=t)["Table"].get("DeletionProtectionEnabled"):
                self.skipped.append(f"table {t} has deletion protection ON; switch it off yourself to delete it")
                continue
            self.act(f"table {t}", ddb.delete_table, TableName=t)

    def log_groups(self):
        logs = self.c("logs")
        prefixes = [f"/aws/lambda/{self.dash}",
                    *(f"/aws/bedrock-agentcore/runtimes/{n}-" for n in runtime_names_for(self.stem, self.env)),
                    f"/aws/kinesisfirehose/{self.dash}", f"/aws/vendedlogs/states/{self.dash}"]
        names: list[str] = []
        for p in prefixes:
            names += [g["logGroupName"] for g in self.pages(logs, "describe_log_groups", "logGroups", logGroupNamePrefix=p)]
        self.section("CloudWatch log groups", self.note("log-groups", names))
        for n in names:
            self.act(f"log group {n}", logs.delete_log_group, logGroupName=n)

    # ---- run -------------------------------------------------------------------
    STAGES = ("schedules", "alarms", "cloudfront_and_waf", "quick_content", "quick", "agentcore", "bedrock",
              "compute_and_api",
              "events_and_streams", "lake_catalog", "secrets", "cognito", "iam_roles", "buckets", "tables", "log_groups")

    def run(self, only: set[str] | None = None) -> int:
        mode = "DELETE" if self.confirm else "DRY-RUN"
        print(f"[{mode}] stem={self.stem} env={self.env} account={self.account} region={self.region}"
              f"{' include-data' if self.include_data else ''}")
        for st in self.STAGES:
            if only and st not in only:
                continue
            if st in QUICK_STAGES and not self.include_quick:
                print(f"  {st:42s} skipped: AWS Quick ids carry no env; pass --include-quick to remove them")
                continue
            try:
                getattr(self, st)()
            except Exception as e:  # noqa: BLE001 - one failed listing must not stop the rest
                self.errors.append(f"stage {st}: {type(e).__name__}: {str(e)[:160]}")
                print(f"  ERROR in stage {st}: {type(e).__name__}: {str(e)[:160]}")
        total = sum(len(v) for v in self.found.values())
        print(f"\nfound {total} resource(s) for stem '{self.stem}'"
              + (f"; deleted {self.deleted}" if self.confirm else "; nothing deleted (dry run)"))
        for s in self.skipped:
            print(f"  SKIPPED  {s}")
        for e in self.errors:
            print(f"  FAILED   {e}")
        if self.confirm and not self.errors:
            self.archive_local_config()
        return 1 if self.errors else 0

    def archive_local_config(self) -> None:
        cfgs = sorted(INFRA.glob(naming.config_glob(self.env)))      # this env's files only
        mine = []
        for p in cfgs:
            try:
                text = p.read_text()
            except OSError:
                continue
            if self.dash in text or f"{self.us}_" in text or self.account in text:
                mine.append(p)
        if not mine:
            return
        dest = ROOT / "backup" / f"infra-config-{self.stem}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        dest.mkdir(parents=True, exist_ok=True)
        for p in mine:
            shutil.move(str(p), dest / p.name)
        print(f"  moved {len(mine)} local config file(s) to {dest.relative_to(ROOT)} (the next deploy starts clean)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete the AWS resources this solution created for one naming stem.")
    ap.add_argument("--stem", default="retail-ops", help="naming stem: 'retail-ops' (current) or 'isso' (before 2026-10-19)")
    ap.add_argument("--env", default=naming.ENV,
                    help="environment name (default: RETAIL_OPS_ENV, else dev); the one the stack was deployed with")
    ap.add_argument("--include-quick", action="store_true",
                    help="also remove the AWS Quick content for a non-default env (always on for dev)")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--confirm", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--account", help="required with --confirm; must equal the caller's AWS account id")
    ap.add_argument("--include-data", action="store_true", help="also delete DynamoDB tables and the event-lake bucket")
    ap.add_argument("--include-quick-users", action="store_true", help="also delete Quick readers the app registered")
    ap.add_argument("--allow-prod", action="store_true", help="permit an environment name containing 'prod'")
    ap.add_argument("--wait-cloudfront", type=int, default=1200, help="seconds to wait for a distribution to disable")
    ap.add_argument("--only", help="comma-separated stage names (" + ",".join(Teardown.STAGES) + ")")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9-]{2,20}", args.stem) or args.stem in {"app", "aws", "dev", "prod"}:
        print(f"refusing: '{args.stem}' is not a safe naming stem", file=sys.stderr)
        return 2
    try:
        naming.validate_env(args.env)
    except naming.NamingError as e:
        print(f"refusing: {e}".replace(naming.ENV_VAR, "--env"), file=sys.stderr)
        return 2
    if "prod" in args.env.lower() and not args.allow_prod:
        print(f"refusing: environment '{args.env}' looks like production (pass --allow-prod deliberately)", file=sys.stderr)
        return 2
    t = Teardown(args.stem, args.env, args.region, args.confirm, args.include_data,
                 args.include_quick_users, args.wait_cloudfront,
                 include_quick=True if args.include_quick else None)
    if args.confirm and args.account != t.account:
        print(f"refusing: --confirm needs --account {t.account} (the account these credentials belong to)"
              f"{'' if not args.account else f'; you passed {args.account}'}", file=sys.stderr)
        return 2
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    return t.run(only)


if __name__ == "__main__":
    sys.exit(main())
