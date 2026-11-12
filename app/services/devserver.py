"""Local dev server for the L3 read models (stdlib only).

A thin http.server that routes to the read models so the Next.js UI (L7) can
call real endpoints against the live DynamoDB tables, without standing up API
Gateway yet. This is the local stand-in for L4; the same handler logic moves
behind API Gateway + Lambda in the cloud form.

Routes:
  GET /health
  GET /journeys/{id}/assist      -> assist.build_assist

Run:
  python3 -m app.services.devserver           # from workspace root
  python3 app/services/devserver.py           # also works

Env:
  RETAIL_OPS_LOCAL_DATA  1 = serve from an in-memory store seeded from
                   sample-data/synthetic/{us,de} (no AWS needed); implies
                   RETAIL_OPS_REQUIRE_AUTH=1 so dev tokens select the persona,
                   and sets RETAIL_OPS_AGENT_RUNTIME_DISABLE=1 so every agent
                   runs in-process
  RETAIL_OPS_LOCAL_REBASE  1 (default) = shift the packs' frozen clock (2026-09-10)
                   to now so "today" views are live; 0 = packs verbatim
  RETAIL_OPS_ENV_PREFIX  (default retail-ops-<RETAIL_OPS_ENV>-, i.e. retail-ops-dev-)
  AWS_REGION       (default us-east-1)
  PORT             (default 8080)
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import MutableMapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _env_prefix() -> str:
    """RETAIL_OPS_ENV_PREFIX, else retail-ops-<RETAIL_OPS_ENV>- (app/services/common/envname.py)."""
    from app.services.common import envname
    return envname.table_prefix()

# Allow both `python3 -m app.services.devserver` and direct execution.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.services.api import authorizer, cors, handler  # type: ignore
    from app.services.common import authz  # type: ignore
else:
    from .api import authorizer, cors, handler
    from .common import authz

# Local access-layer switch: when RETAIL_OPS_REQUIRE_AUTH=1 the dev server runs the
# SAME authorizer + scope-enforcing handler path as API Gateway (local == cloud).
# Default off so the UI slice keeps working without a token during development.
_LOCAL_DATA = os.environ.get("RETAIL_OPS_LOCAL_DATA", "0") == "1"


def _local_runtime_guard(env: MutableMapping[str, str]) -> None:
    """Keep every agent in-process when serving the in-memory store: a deployed
    agent runtime writes to the dev tables, which must never mix with local data
    (ADR 0009, design D6e)."""
    if env.get("RETAIL_OPS_LOCAL_DATA") == "1":
        env["RETAIL_OPS_AGENT_RUNTIME_DISABLE"] = "1"


_local_runtime_guard(os.environ)
_REQUIRE_AUTH = os.environ.get("RETAIL_OPS_REQUIRE_AUTH", "1" if _LOCAL_DATA else "0") == "1"


class Handler(BaseHTTPRequestHandler):
    server_version = "retail-ops-devserver/0.2"

    def _send(self, code: int, payload: dict) -> None:
        """A reply the dev server builds itself (health, auth refusal, preflight),
        with the API's CORS headers (app/services/api/cors.py)."""
        body = b"" if code == 204 else json.dumps(payload, default=str).encode("utf-8")
        self._write(code, cors.headers(self.headers.get("Origin")), body)

    def _forward(self, resp: dict) -> None:
        """The handler's response as-is: its headers (CORS included) and body, so
        the SSE route reaches the console as text/event-stream."""
        self._write(resp["statusCode"], resp.get("headers") or {}, (resp.get("body") or "").encode("utf-8"))

    def _write(self, code: int, headers: dict, body: bytes) -> None:
        self.send_response(code)
        for k, v in headers.items():
            if k.lower() != "content-length":
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_event(self, method: str) -> dict:
        """Build an API Gateway proxy event, running the authorizer first so the
        handler sees the same requestContext.authorizer claims it would in AWS."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        event: dict = {"path": parsed.path, "httpMethod": method,
                       "headers": dict(self.headers),
                       "queryStringParameters": qs or None, "requestContext": {}}
        if _REQUIRE_AUTH:
            hdr = self.headers.get("Authorization")
            try:
                claims = authorizer.resolve_principal(hdr)
            except (authz.Unauthenticated, authz.Forbidden) as e:
                return {"__deny__": (401, {"error": f"unauthorized: {e}"})}
            event["requestContext"]["authorizer"] = claims
        else:
            # Auth disabled: inject an OEM-scoped principal so scope checks pass.
            event["requestContext"]["authorizer"] = {"user_id": "dev", "role": "dealer_manager",
                                                      "is_oem": "true", "dealer_ids": ""}
        return event

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path.split("?")[0] == "/health":
            return self._send(200, {"ok": True,
                                    "env_prefix": _env_prefix(),
                                    "require_auth": _REQUIRE_AUTH})
        event = self._proxy_event("GET")
        if "__deny__" in event:
            code, body = event["__deny__"]
            return self._send(code, body)
        self._forward(handler.lambda_handler(event))

    def _with_body(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        event = self._proxy_event(method)
        if "__deny__" in event:
            code, body = event["__deny__"]
            return self._send(code, body)
        event["body"] = raw
        self._forward(handler.lambda_handler(event))

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        self._with_body("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._with_body("PATCH")

    def do_PUT(self) -> None:  # noqa: N802
        self._with_body("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._with_body("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802 — CORS preflight carries no auth
        self._send(204, {})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[devserver] " + (fmt % args) + "\n")


def main() -> int:
    port = int(os.environ.get("PORT", "8080"))
    if _LOCAL_DATA:
        if __package__ in (None, ""):
            from app.services.common import local_ddb  # type: ignore
        else:
            from .common import local_ddb
        # RETAIL_OPS_LOCAL_REBASE (default 1): shift the packs' frozen clock to now, so the
        # sandbox shows today's appointments, live SLA clocks and 7-day stalls rather than a
        # store frozen 50 days ago. 0 serves the packs verbatim (what the cloud tables hold).
        rebase = os.environ.get("RETAIL_OPS_LOCAL_REBASE", "1") == "1"
        import datetime as _dt
        fake = local_ddb.install(rebase_to=_dt.datetime.now(_dt.UTC) if rebase else None)
        print(f"[devserver] LOCAL DATA: {sum(len(t) for t in fake.tables.values())} rows in "
              f"{len(fake.tables)} tables (in-memory; resets on restart)"
              + (f"; clock rebased {local_ddb.SEED_NOW:%Y-%m-%d} -> today" if rebase else "; pack clock as committed"))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[devserver] listening on http://127.0.0.1:{port}  "
          f"(env_prefix={_env_prefix()}, "
          f"region={os.environ.get('AWS_REGION', 'us-east-1')})")
    print("[devserver]   every API route (app/services/api) · auth via dev tokens "
          f"(require_auth={_REQUIRE_AUTH})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
