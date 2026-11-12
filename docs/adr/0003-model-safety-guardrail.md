# ADR 0003 — One managed Bedrock Guardrail on every model call

**Status:** Accepted (2026-10-08). Deployed as `retail-ops-dev-guardrail`
(version 1 in the current development account, <account-id>).
Amended 2026-10-22: the nightly store digest carries its own copy (see
"Amendment: the store digest's guardrail").

## Context

Model safety was prompt-only (a grounding-only system prompt) plus two code
fences: the A2 refuse/escalate classifier for customers and the A6 rules engine
GR-001…GR-008 for OEM staff. That protects the known cases but not the model's
own output, and it does nothing for the direct Converse calls scattered across
the agents. The specification asks for "Bedrock + Guardrails" on every
generative step.

## Decision

Provision **one** Amazon Bedrock Guardrail and attach it to **every** Converse
call in the application through a single helper. Keep the code fences: they
name the rule and offer a safe alternative, which the managed guardrail cannot.

The guardrail (STANDARD tier, cross-region guardrail profile):

| Policy | Content |
|---|---|
| Denied topics | credit decisions · legal interpretation · safety-recall advice · dealer wholesale / allocation / pricing instructions (GR-001/003/005) · dealer termination (GR-002) · personnel decisions (GR-004). Each definition states what it does **not** include (e.g. reporting dealers below objective is allowed) |
| Content filters | hate, insults, sexual, violence, misconduct (HIGH); prompt attack (input only) |
| Word filters | managed profanity list + the A7 scare-language list ("death trap", "unsafe to drive", …) |
| Sensitive information | SSN, card, bank account/routing, driver licence, passwords and keys → anonymised on output; e-mail and phone untouched (dealer contact details are legitimate) |
| Contextual grounding | grounding 0.45 / relevance 0.35 against the context the caller supplied |

## How it is applied

* `platform/generate.guardrail_kwargs()` returns the `guardrailConfig` for
  Converse (id and version from `RETAIL_OPS_GUARDRAIL_ID`/`RETAIL_OPS_GUARDRAIL_VERSION` or
  `app/infra/guardrail-config.json`). Every direct Converse call splats it.
* `generate.ground()` sends the context as a `grounding_source` block and the
  question as a `query` + `guard_content` block, so the grounding filter scores
  the answer against exactly the facts supplied and the topic/content policies
  evaluate the question. (Without `guard_content` the question is not guarded —
  found during deployment.)
* `generate.guardrail_outcome()` reports `{applied, intervened, matched,
  grounding_only}`. A **grounding-only** intervention means the model drifted
  from the context, not that the user asked something fenced, so `ground()`
  returns the deterministic context text instead of a refusal. A topic or filter
  intervention returns the guardrail's blocked message.
* The Strands agent (ADR 0002) attaches the same guardrail to its `BedrockModel`.

## Options considered

1. **Prompt-only + code fences** (previous state). Cheap, but output is unchecked
   and each new call site must remember the fences.
2. **Per-agent guardrails.** More precise, more to maintain; the fences the spec
   lists are the same for every agent.
3. **One guardrail, one helper** *(chosen)*. One place to change, one
   calibration list, consistent behaviour.

## Implementation

```
app/infra/deploy_guardrails.py      create/update + publish version; --calibrate; --test "<text>"
app/infra/guardrail-config.json     {guardrail_id, guardrail_arn, version}
app/services/platform/generate.py   guardrail_config / guardrail_kwargs / guardrail_outcome / ground
app/infra/iam_roles.py              bedrock:ApplyGuardrail on guardrail/* and guardrail-profile/* (all regions)
tests/app/test_guardrails_and_knowledge.py
```

Calibration lists in the deployer (13 prompts that must pass, 8 that must block)
are run with `--calibrate` after every definition change; the deployment is not
considered done until it reports `0 mismatch(es)`.

## Consequences

* Every generated sentence is checked on input and output; the response says so
  (`guardrail.applied`, `guardrail.intervened`).
* The STANDARD tier evaluates through a cross-region guardrail profile, so IAM
  must allow `bedrock:ApplyGuardrail` on `arn:aws:bedrock:*:<account>:guardrail-profile/*`
  in addition to the guardrail itself.
* The grounding filter enforces "answer only from context"; answers that go
  beyond the supplied facts fall back to the facts. This is intended.
* Guardrail units add a small cost per call and ~150 ms latency.

## Amendment: the store digest's guardrail (user decision 2026-10-22)

The nightly `store-digest` job rewrites each cockpit tab's figures-only summary
as a short [AI INSIGHT]. Against the 0.45 / 0.35 grounding check, about 80% of
those rewrites were held back, almost all by the grounding check (paraphrase and
mild inference over figures that are all present) and a few by the wholesale /
credit topics. The user chose a separate guardrail for that one job over
loosening the shared one:

* `retail-ops-dev-guardrail-digest`: every policy in the table above unchanged
  (topics, content, words, sensitive information, tier, messages); contextual
  grounding **0.2, relevance check off**. Its calibration run reports
  `0 mismatch(es)`, so every denied topic still blocks.
* Only `store_digest` asks for it (`generate.ground(..., guardrail="digest")`),
  and only the store-digest Lambda gets `RETAIL_OPS_DIGEST_GUARDRAIL_ID` /
  `_VERSION` (`deploy_l6.job_env`). Customer chat, the Sales Agent, the District
  Manager Assistant and every other call keep the main guardrail.
* If the digest guardrail is not provisioned, the digest carries the main one;
  it never runs without a guardrail.
* `deploy_guardrails.py --which main|digest|all` deploys, tests and calibrates
  either one; the config is `app/infra/guardrail-digest-config.json`.

Trade-off accepted: digest text may add mild inference ("customers are getting
stuck") on top of the figures it quotes. The figures-only text stays one click
away (`insight.figures_only`) and every written insight names its writer.

## Revisit when

* A legitimate question is blocked: add it to `CALIBRATION_ALLOWED`, adjust the
  topic definition, redeploy, re-calibrate.
* A new fence is needed for a new agent: add a topic (and a denied example to
  `CALIBRATION_DENIED`) rather than a new prompt rule.
