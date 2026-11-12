# ADR 0008 — No public Lambda entry points; the SSE floor path rides the authenticated API

**Status:** Accepted (2026-10-08).

## Context

The streaming floor path (spec R2) was first shipped as a dedicated Lambda
(`retail-ops-dev-floor-stream`) behind a **world-accessible Function URL**
(AuthType `NONE` + resource policy `Principal: "*"`), with token verification
done inside the handler. The account's security control
`lambda_function_policy_block_public_access` flagged it
("Auto-mitigation of world accessible retail-ops-dev-floor-stream") and
auto-mitigated by stripping the resource policy — correctly: the block happens
*above* Lambda, so in-handler auth is no defence, and the endpoint 403'd no
matter what our code did.

A corrected design (AuthType `AWS_IAM`, fronted same-origin by the console
CloudFront distribution with a lambda-type Origin Access Control, invoke grant
scoped to `cloudfront.amazonaws.com` + the distribution ARN) was provisioned
and verified piece-by-piece, but the OAC hop kept arriving **unsigned** at the
function: a direct SigV4-signed invoke reached the handler, CloudFront's did
not, and CloudTrail recorded no InvokeFunctionUrl events. Meanwhile the managed
python3.12 runtime cannot progressively stream a response anyway — the handler
buffered the SSE document regardless — so the Function URL bought nothing that
the existing, fully-authenticated API could not provide.

## Decision

1. **No public entry points, ever.** Nothing in this system may be reachable
   without the platform's authentication: API Gateway + the authorizer, or the
   console's CloudFront origins. A Function URL with AuthType `NONE` or any
   resource-policy statement allowing `Principal: "*"` is a defect, not a
   configuration choice — the account will (rightly) auto-mitigate it.
2. **The SSE floor path rides the main API.** `POST
   /journeys/{id}/utterance-sse` returns the SSE document (`event: token` per
   generation delta, final `event: result`) through the standard authorizer,
   Content-Type `text/event-stream`. Buffered by transport (API GW REST +
   python3.12), identical on the wire — a true streaming transport is a URL
   swap for the console client, which parses SSE either way.
3. **The flagged resource is gone.** `retail-ops-dev-floor-stream`, its Function URL,
   role and the `/stream/*` CloudFront behavior are deleted.
4. **Guards stay.** `app/infra/deploy_streaming.py` (kept for a future
   Node/Web-Adapter streaming runtime behind CloudFront OAC) is `AWS_IAM`-only
   and ends every apply with `_assert_not_public()` — it aborts if AuthType is
   not `AWS_IAM` or any policy statement allows a wildcard principal.
   `tests/app/test_no_public_lambda_url.py` pins that shape in CI.

## Consequences

- Sub-2s *perceived* streaming on the floor waits for a streaming-capable
  transport (Node runtime or Lambda Web Adapter, fronted by CloudFront OAC —
  and the OAC signing gap must be root-caused first). `platform/streaming.py`
  already truly streams in-process and is covered by tests.
- The console flag `NEXT_PUBLIC_RETAIL_OPS_STREAM` (default on) selects the SSE route;
  `0` falls back to the plain request/response objection route.
- Any future external entry point must name its authenticator in the deployer
  and carry a `_assert_not_public`-style terminal check.
